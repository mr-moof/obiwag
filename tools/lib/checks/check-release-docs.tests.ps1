<#
.SYNOPSIS
    Pester tests for release and lint contract checks (OPT-12, #186).
.DESCRIPTION
    Split from check-dispatch-docs.tests.ps1 without changing cases. Requires Pester 5.
#>
BeforeAll {
. (Join-Path $PSScriptRoot 'check-dispatch-docs.test-support.ps1')
}

Describe 'Test-ReleaseDocs' {

    BeforeEach {
        $script:ScriptDir = $script:ToolsDir
        $script:RepoRoot = $null

        # Dot-source the module under test and its dependencies
        . (Join-Path $ScriptDir 'lib\common.ps1')
        . (Join-Path $ScriptDir 'lib\checks\check-dispatch-docs.ps1')
    }

    It 'returns invalid when PowerShell lint can bind multiple paths without terminating errors' {
        $repo = New-FixtureRepo 'unsafelint'
        Set-SyncedFixture -Repo $repo
        Set-Content -LiteralPath "$repo\orchestration\inline-fallback-recipes.md" `
            -Value 'Invoke-ScriptAnalyzer -Path $targets; report the returned count.' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'one path at a time'
    }

    It 'returns invalid when a recipe retains a bare ScriptAnalyzer invocation' {
        $repo = New-FixtureRepo 'stale-lint-example'
        Set-SyncedFixture -Repo $repo
        Add-Content -LiteralPath "$repo\orchestration\inline-fallback-recipes.md" `
            -Value 'Recipe S: run `Invoke-ScriptAnalyzer .` after each edit.' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'bare ScriptAnalyzer invocation'
    }

    It 'returns invalid when Python lint scans the repository root' {
        $repo = New-FixtureRepo 'full-tree-python-lint'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'Run python -m ruff check <path> once per selected path for sorted unique existing changed Python paths,',
            'Run python -m ruff check . for every Python change,')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'must not scan the repository root'
    }

    It 'returns invalid when the changed-file Python lint contract is omitted' {
        $repo = New-FixtureRepo 'missing-python-lint'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [regex]::Replace(
            [System.IO.File]::ReadAllText($path),
            '(?m)^Run python -m ruff check <path>.*\r?\n.*\r?\n',
            '')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'once per existing changed Python path'
    }

    It 'returns invalid when the PowerShell lint severity policy is omitted' {
        $repo = New-FixtureRepo 'missing-lint-severity'
        Set-SyncedFixture -Repo $repo
        Set-Content -LiteralPath "$repo\orchestration\inline-fallback-recipes.md" `
            -Value 'Invoke-ScriptAnalyzer once per selected path with -ErrorAction Stop. For filtered Pester, executed count = PassedCount + FailedCount + SkippedCount; TotalCount includes NotRunCount, so assert the expected executed count.' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'Severity Error'
    }

    It 'returns invalid when filtered Pester executed-count semantics are omitted' {
        $repo = New-FixtureRepo 'missing-executed-count'
        Set-SyncedFixture -Repo $repo
        Set-Content -LiteralPath "$repo\orchestration\inline-fallback-recipes.md" `
            -Value 'Invoke-ScriptAnalyzer once per selected path with -ErrorAction Stop.' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'filtered Pester executed count'
    }

    It 'returns invalid when README review sends a Windows temp path to Bash' {
        $repo = New-FixtureRepo 'unsafe-bash-parse'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace('bash -n -c', 'bash -n <tempfile>')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'Windows temp-file path'
    }

    It 'returns invalid when README review leaves Windows Bash resolution ambient' {
        $repo = New-FixtureRepo 'ambient-windows-bash'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'resolves CLAUDE_CODE_GIT_BASH_PATH, and rejects System32\bash.exe',
            'uses whichever bash is first on PATH')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'Git Bash explicitly'
    }

    It 'returns invalid when the release recipe drops deterministic sharding' {
        $repo = New-FixtureRepo 'monolithic-release'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace('-ShardCount 3', '-ShardCount 1')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'bounded-shard marker'
    }

    It 'returns invalid when the release Pester timeout falls back to five minutes' {
        $repo = New-FixtureRepo 'short-release-pester-timeout'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'Each Pester shard uses timeout: 600000',
            'Each Pester shard uses timeout: 300000')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'Pester shards at 600000 ms'
    }

    It 'returns invalid when the release Python timeout inherits the Pester exception' {
        $repo = New-FixtureRepo 'long-release-python-timeout'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'the Python component uses timeout: 300000',
            'the Python component uses timeout: 600000')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'Python component at 300000 ms'
    }

    It 'returns invalid when the release version scan ignores enumeration errors' {
        $repo = New-FixtureRepo 'release-version-scan-fail-open'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'Get-ChildItem -LiteralPath $sourcePath -Recurse -File -ErrorAction Stop',
            'Get-ChildItem -Path $sourcePaths -Recurse -File')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'terminating errors'
    }

    It 'returns invalid when Re-review returns to an unconditional full suite' {
        $repo = New-FixtureRepo 'rereview-full-suite'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [regex]::Replace([System.IO.File]::ReadAllText($path),
            '(?s)Run the smallest\s+focused\s+selection\..*?Use full-suite\s+sharding only when isolation is unsafe\.',
            'Run the full no-argument suite unconditionally.')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'keep Re-review focused'
    }

    It 'returns invalid when Re-review omits the Pester timeout exception' {
        $repo = New-FixtureRepo 'short-rereview-pester-timeout'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\inline-fallback-recipes.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'A Pester-only selection uses timeout: 600000',
            'A Pester-only selection uses timeout: 300000')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'Pester selections 600000 ms'
    }

    It 'returns invalid when Phase 9 drops source policy paths and restores blanket staging' {
        $repo = New-FixtureRepo 'stale-release-command'
        Set-SyncedFixture -Repo $repo
        Set-Content -LiteralPath "$repo\phases\09-release\command.md" -Encoding UTF8 `
            -Value "Read docs/policies/zero-hallucination.md.`n4. Stage changes: git add -A"
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'blanket git add -A staging'
    }

    It 'returns invalid when the Claude release agent contradicts live local deploy' {
        $repo = New-FixtureRepo 'release-no-deploy'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\platforms\claude-code\agents\obi-release-gate.md"
        $content = [System.IO.File]::ReadAllText($path) + "`nYou do NOT push, create MRs, or deploy.`n"
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'contradicts its required local live-deploy'
    }
}
