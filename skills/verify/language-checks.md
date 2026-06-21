# Language-Specific Verification Checks

Detailed recipes for each supported project type. The main `SKILL.md` has the procedure; this file has the specifics.

## Go

```bash
go build ./...          # Compilation check
go vet ./...            # Static analysis
go test ./...           # Unit tests
go test -race ./...     # Race detection (if tests exist)
```

**Common gotchas:**
- `go vet` catches more than `go build` — always run both
- Use `-count=1` to disable test caching during verification
- If `go.sum` is stale, run `go mod tidy` first

## Python

```bash
python -m py_compile <file>     # Syntax check per changed file
pytest                          # If pytest available and tests exist
pytest --tb=short               # Shorter output for faster triage
```

**Common gotchas:**
- Always use bare `python` (it's on PATH), never `C:\Python314\python.exe`
- Write scripts to `/tmp/script.py` then run, never `python -c "..."`
- If `pytest` not installed, `py_compile` is the minimum check

## PowerShell

```powershell
Invoke-ScriptAnalyzer -Path <module> -Recurse   # If PSScriptAnalyzer available
```

**Common gotchas:**
- Workstation runs PS 5.1 and Pester 3.4.0
- Pester 3.4 lacks: `Should -Invoke`, `Should -InvokeVerifiable`, `InModuleScope` with `-Parameters`, `| Should -Be` pipeline syntax
- Use `Assert-MockCalled` instead of `Should -Invoke`
- Use `Should Be` (v3) not `Should -Be` (v5)
- `-ErrorAction Stop` with some vendor SDK cmdlets turns benign warnings into terminating errors and kills pipelines — use `2>$null` instead
- For complex PS commands through Bash, write a `.ps1` helper and call with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <script>`

## Node.js / TypeScript

```bash
npm run build    # If build script exists in package.json
npm run lint     # If lint script exists
npm test         # If test script exists
```

**Common gotchas:**
- Check `package.json` for available scripts before running
- `npm ci` is faster than `npm install` for clean installs
- TypeScript: `tsc --noEmit` for type-check without build output

## Dockerfile

```bash
docker build --no-cache -t test-build .    # Build verification
docker run --rm test-build <healthcheck>   # Smoke test if applicable
```

**Common gotchas:**
- Multi-stage builds: verify final stage has all runtime deps
- Check `.dockerignore` excludes sensitive files
- Verify `EXPOSE` ports match application config
