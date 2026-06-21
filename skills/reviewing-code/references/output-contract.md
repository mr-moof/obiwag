# Reviewing-code — Output Contract

Always return this exact format:

```
## Review Verdict: PASS | FAIL

### Codex First-Pass
- Status: ran | unavailable
- Codex pass: ran | unavailable
- Summary: <1-3 sentence digest of what Codex raised>

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
- [Codex|Claude|Synthesis] <blocker description>

### Required Fixes
- [Codex|Claude|Synthesis] <must-fix description>

### Optional Improvements
- [Codex|Claude|Synthesis] <nice-to-have description>

### Disputed Codex Findings
- [Codex — disputed] <original finding> — rebuttal: <why Claude disagrees>
```

Every entry in Critical Faults, Required Fixes, and Optional Improvements MUST carry an attribution tag. `Disputed Codex Findings` stays in output even when empty (show `- (none)`).
