# PowerShell Interop on Windows

## Runtime Environment

| Component | Version | Notes |
|-----------|---------|-------|
| PowerShell | 5.1 | Windows PowerShell, NOT PowerShell 7+ |
| Pester | 3.4.0 | Shipped with Windows, NOT the modern v5 |

## Pester 3.4 Limitations

These features **do not exist** in Pester 3.4. Using them causes syntax errors or silent failures:

| Feature | Pester 5 Syntax | Pester 3.4 Equivalent |
|---------|-----------------|----------------------|
| Assertion | `Should -Be $value` | `Should Be $value` (no hyphen) |
| Mock verify | `Should -Invoke` | `Assert-MockCalled` |
| Mock verify | `Should -InvokeVerifiable` | `Assert-VerifiableMocks` |
| InModuleScope params | `InModuleScope -Parameters @{...}` | Not supported — use `$script:` vars |
| Pipeline assertion | `$x | Should -Be $y` | `$x | Should Be $y` |
| BeforeAll/AfterAll | `BeforeAll { }` | Not supported — use `Setup` or inline |
| BeforeDiscovery | `BeforeDiscovery { }` | Not supported |

## PS 5.1 Gotchas

- **No `&&` operator** — use `;` or separate commands
- **No ternary** (`$x ? $a : $b`) — use `if/else`
- **No null-coalescing** (`$x ?? $default`) — use `if ($null -eq $x) { $default } else { $x }`
- **`ConvertFrom-Json` returns PSCustomObject**, not hashtable — use `.PropertyName` not `['key']`
- **`Invoke-RestMethod` lacks `-SkipHttpErrorCheck`** — wrap in `try/catch` for error handling
- **`[ordered]` works** but `@{}` does NOT preserve order

## Execution from Bash

When running PowerShell from Git Bash:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File /path/to/script.ps1
```

For inline commands (simple only):
```bash
powershell.exe -NoProfile -Command "Get-Process | Select-Object -First 5"
```

Avoid complex inline commands — escaping is fragile between Bash and PS. Write a `.ps1` file instead.
