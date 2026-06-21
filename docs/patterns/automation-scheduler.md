# Automation Scheduler Patterns

> **Version:** 1.0 | **Last Updated:** 2026-02-04

Patterns for building reliable automation schedulers in automation systems.

---

## Job State Machine

```
┌─────────┐
│ pending │ ← initial state
└────┬────┘
     │ claim
     ▼
┌────────────┐
│ processing │ ← active execution
└─────┬──────┘
      │
  ┌───┴───┐
  │       │
  ▼       ▼
┌────┐  ┌────────┐
│done│  │ failed │
└────┘  └───┬────┘
            │ retry_count < max?
            │ yes → back to pending
            │ no  → stays failed, alert
```

---

## Required Table Schema

```sql
CREATE TABLE automation_jobs (
    id SERIAL PRIMARY KEY,

    -- Workflow identification
    workflow_type VARCHAR(50) NOT NULL,  -- e.g., 'host_build', 'firmware_update'
    workflow_id UUID DEFAULT gen_random_uuid(),

    -- Job sequencing
    step_name VARCHAR(100) NOT NULL,
    step_order INT NOT NULL,

    -- State management
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    claimed_by VARCHAR(100),
    claimed_at TIMESTAMP,
    lease_expires_at TIMESTAMP,

    -- Retry handling
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    last_error TEXT,

    -- Scheduling
    scheduled_at TIMESTAMP,  -- NULL = run immediately
    priority INT DEFAULT 0,  -- Higher = more urgent

    -- Payload
    payload JSONB NOT NULL,
    result JSONB,

    -- Audit
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,

    -- Idempotency
    idempotency_key VARCHAR(255) UNIQUE,

    -- Constraints
    CONSTRAINT valid_status CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled'))
);

-- Indexes for common queries
CREATE INDEX idx_jobs_claimable ON automation_jobs (status, scheduled_at, priority)
    WHERE status = 'pending';
CREATE INDEX idx_jobs_stale ON automation_jobs (lease_expires_at)
    WHERE status = 'processing';
CREATE INDEX idx_jobs_workflow ON automation_jobs (workflow_id, step_order);
```

---

## Locking Strategies

### Primary: SELECT FOR UPDATE SKIP LOCKED

**Best for:** Job queue pattern, claim-and-process workflows.

```sql
-- Claim next available job atomically
UPDATE automation_jobs
SET status = 'processing',
    claimed_by = $1,
    claimed_at = NOW(),
    lease_expires_at = NOW() + INTERVAL '5 minutes'
WHERE id = (
    SELECT id FROM automation_jobs
    WHERE status = 'pending'
      AND (scheduled_at IS NULL OR scheduled_at <= NOW())
    ORDER BY priority DESC, created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

**Go implementation:**
```go
type Job struct {
    ID          int64
    Status      string
    ClaimedBy   string
    ClaimedAt   time.Time
    Payload     json.RawMessage
}

func (s *Scheduler) ClaimNextJob(ctx context.Context, workerID string) (*Job, error) {
    query := `
        UPDATE automation_jobs
        SET status = 'processing',
            claimed_by = $1,
            claimed_at = NOW(),
            lease_expires_at = NOW() + INTERVAL '5 minutes'
        WHERE id = (
            SELECT id FROM automation_jobs
            WHERE status = 'pending'
              AND (scheduled_at IS NULL OR scheduled_at <= NOW())
            ORDER BY priority DESC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, status, claimed_by, claimed_at, payload`

    var job Job
    err := s.db.QueryRowContext(ctx, query, workerID).Scan(
        &job.ID, &job.Status, &job.ClaimedBy, &job.ClaimedAt, &job.Payload)

    if err == sql.ErrNoRows {
        return nil, nil // No jobs available
    }
    return &job, err
}
```

### Alternative: PostgreSQL Advisory Locks

**Best for:** Single-DB systems, moderate scale.

```go
func (s *Scheduler) ClaimJob(ctx context.Context, jobID int64) (bool, error) {
    lockKey := hashJobID(jobID)

    var acquired bool
    err := s.db.QueryRowContext(ctx,
        "SELECT pg_try_advisory_lock($1)", lockKey).Scan(&acquired)
    if err != nil {
        return false, fmt.Errorf("failed to acquire lock: %w", err)
    }

    return acquired, nil
}

func (s *Scheduler) ReleaseJob(ctx context.Context, jobID int64) error {
    lockKey := hashJobID(jobID)
    _, err := s.db.ExecContext(ctx,
        "SELECT pg_advisory_unlock($1)", lockKey)
    return err
}
```

---

## Heartbeat/Lease Pattern

**Essential for:** Long-running jobs, crash recovery.

### Critical Parameters

| Parameter | Recommended | Why |
|-----------|-------------|-----|
| Lease duration | 5 minutes | Long enough to survive network blips |
| Heartbeat interval | 2 minutes | 1/3 of lease, tolerates 2 missed beats |
| Stale job scanner | Every 1 minute | Detect crashed workers promptly |

### Go Heartbeat Implementation

```go
func (w *Worker) RunWithHeartbeat(ctx context.Context, job *Job) error {
    // Start heartbeat goroutine
    heartbeatCtx, cancel := context.WithCancel(ctx)
    defer cancel()

    go func() {
        ticker := time.NewTicker(2 * time.Minute)
        defer ticker.Stop()

        for {
            select {
            case <-heartbeatCtx.Done():
                return
            case <-ticker.C:
                if err := w.renewLease(ctx, job.ID); err != nil {
                    log.Printf("heartbeat failed for job %d: %v", job.ID, err)
                }
            }
        }
    }()

    // Execute actual job
    return w.executeJob(ctx, job)
}

func (w *Worker) renewLease(ctx context.Context, jobID int64) error {
    _, err := w.db.ExecContext(ctx, `
        UPDATE automation_jobs
        SET lease_expires_at = NOW() + INTERVAL '5 minutes'
        WHERE id = $1 AND claimed_by = $2 AND status = 'processing'
    `, jobID, w.workerID)
    return err
}
```

### Stale Job Scanner

```go
func (s *Scheduler) RecoverStaleJobs(ctx context.Context) (int64, error) {
    result, err := s.db.ExecContext(ctx, `
        UPDATE automation_jobs
        SET status = 'pending',
            claimed_by = NULL,
            claimed_at = NULL,
            retry_count = retry_count + 1
        WHERE status = 'processing'
          AND lease_expires_at < NOW()
          AND retry_count < max_retries
    `)
    if err != nil {
        return 0, err
    }
    return result.RowsAffected()
}
```

---

## Workflow Orchestration

For multi-step workflows (like host build), use the saga pattern.

### Workflow Definition

```go
type Workflow struct {
    ID    uuid.UUID
    Type  string
    Steps []Step
}

type Step struct {
    Name       string
    Execute    func(ctx context.Context, payload json.RawMessage) (json.RawMessage, error)
    Compensate func(ctx context.Context, payload json.RawMessage) error  // Rollback
}

// Host Build Workflow
var HostBuildWorkflow = Workflow{
    Type: "host_build",
    Steps: []Step{
        {Name: "find_server", Execute: findAvailableServer, Compensate: nil},
        {Name: "find_profile", Execute: findAvailableProfile, Compensate: nil},
        {Name: "configure_autodeploy", Execute: configureAutodeploy, Compensate: removeAutodeployRule},
        {Name: "associate_profile", Execute: associateProfile, Compensate: disassociateProfile},
        {Name: "wait_for_boot", Execute: waitForBoot, Compensate: nil},
        {Name: "validate_health", Execute: validateHealth, Compensate: nil},
        {Name: "initialize_cluster", Execute: initializeInCluster, Compensate: removeFromCluster},
    },
}
```

### Scheduler Loop Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Scheduler Loop                           │
├─────────────────────────────────────────────────────────────┤
│  1. SELECT FOR UPDATE SKIP LOCKED → claim job               │
│  2. Start heartbeat goroutine (renew lease every 2min)      │
│  3. Execute job steps                                        │
│     - Each step uses idempotency key for external calls     │
│  4. Mark complete (or failed with retry count)              │
│  5. Heartbeat stops, lease expires naturally                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                 Stale Job Scanner (every 1min)              │
├─────────────────────────────────────────────────────────────┤
│  SELECT * FROM jobs                                          │
│  WHERE status = 'processing'                                 │
│    AND lease_expires_at < NOW()                              │
│  → Reset to 'pending' with incremented retry_count           │
│  → If retry_count > max_retries, mark as 'failed', alert    │
└─────────────────────────────────────────────────────────────┘
```

---

## Idempotency Keys

Use with any locking strategy to handle duplicate execution attempts.

```sql
CREATE TABLE job_executions (
    idempotency_key VARCHAR(255) PRIMARY KEY,
    job_id INT NOT NULL,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    result JSONB
);

-- Before starting job
INSERT INTO job_executions (idempotency_key, job_id)
VALUES ($1, $2)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING *;

-- If no row returned, job already running/completed
```

### Go Pattern for External APIs

```go
func generateIdempotencyKey(operation string, serverSerial string, profileName string) string {
    return fmt.Sprintf("%s:%s:%s:%s",
        operation,
        serverSerial,
        profileName,
        time.Now().Format("2006-01-02")) // Daily granularity
}
```

---

## Monitoring Requirements

### Metrics (Prometheus/StatsD)

```go
// Essential metrics
automation_jobs_total{workflow, status}           // Counter: jobs by outcome
automation_job_duration_seconds{workflow, step}   // Histogram: execution time
automation_jobs_in_progress{workflow}             // Gauge: current active
automation_job_retries_total{workflow}            // Counter: retry attempts
automation_job_queue_depth{workflow}              // Gauge: pending jobs
```

### Alerts

| Condition | Severity | Action |
|-----------|----------|--------|
| Job failed after max retries | Critical | Page on-call |
| Queue depth > threshold | Warning | Investigate backlog |
| Job processing > 30 min | Warning | Check for stuck job |
| No jobs processed in 1 hour | Warning | Scheduler health check |

### Structured Logging

```go
// Log every job state transition
log.Info("job state changed",
    "job_id", job.ID,
    "workflow", job.WorkflowType,
    "step", job.StepName,
    "from_status", oldStatus,
    "to_status", newStatus,
    "duration_ms", duration.Milliseconds(),
    "error", err,
)
```

---

## Polling with Adaptive Backoff

For long-running operations (e.g. cloud deployments) that take variable time.

```go
const (
    initialInterval = 10 * time.Second
    maxInterval     = 60 * time.Second
    totalTimeout    = 30 * time.Minute
)

func pollWithBackoff(ctx context.Context, check func() (bool, error)) error {
    deadline := time.Now().Add(totalTimeout)
    interval := initialInterval

    for time.Now().Before(deadline) {
        done, err := check()
        if err != nil {
            return err
        }
        if done {
            return nil
        }

        select {
        case <-ctx.Done():
            return ctx.Err()
        case <-time.After(interval):
            interval = min(interval*2, maxInterval)
        }
    }
    return fmt.Errorf("timeout after %v", totalTimeout)
}
```

---

## Zero Hallucination Notice

If a pattern or API is not documented above:
1. Check the existing codebase for working examples
2. Check `docs/domain-patterns/` for API documentation
3. If not found, output `MISSING SOURCE: [pattern/API]`
4. Do not proceed until documented

---

*See `docs/gotchas.md` for related pitfalls and `docs/domain-patterns/` for any documented vendor API patterns.*
