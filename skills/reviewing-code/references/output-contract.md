# Reviewing-code — Output Contract

Always return this exact format:

```
## Review Verdict: PASS | FAIL

### Peer First-Pass
- Provider: Codex | Claude | unavailable
- Transport: completed | unavailable | timed_out | idle_killed | output_limit | error | launch_error
- Validation: valid | partial | invalid | not_run
- Summary: <1-3 sentence digest of accepted peer findings, limitations, or unavailability>

### Spec Compliance
- [ ] All requested features implemented
- [ ] Nothing extra added (YAGNI)
- [ ] Matches plan/requirements

### Anti-Hallucination Check
- [ ] All vendor APIs verified in repo evidence
- [ ] Wrapper boundary respected
- [ ] No invented endpoints or parameters
- [ ] No direct vendor SDK calls from business logic

### Code Quality
- [ ] Linter clean (language-appropriate)
- [ ] Tests exist (happy path + error case)
- [ ] Modules >100 lines have test coverage (0% = Required Fix)
- [ ] No TODO/FIXME comments in code
- [ ] Error handling present for external calls
- [ ] Naming follows conventions

### Critical Faults
- [Peer: Codex|Peer: Claude|Primary|Synthesis] <blocker description>

### Required Fixes
- [Peer: Codex|Peer: Claude|Primary|Synthesis] <must-fix description>

### Optional Improvements
- [Peer: Codex|Peer: Claude|Primary|Synthesis] <nice-to-have description>

### Disputed Peer Findings
- [Peer — disputed] <original finding> — rebuttal: <why the primary reviewer disagrees>
```

Every entry in Critical Faults, Required Fixes, and Optional Improvements MUST carry an attribution tag. `Disputed Peer Findings` stays in output even when empty (show `- (none)`). Never attribute a rejected or unvalidated peer claim as a finding.
