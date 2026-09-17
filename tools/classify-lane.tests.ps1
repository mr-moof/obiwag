<#
.SYNOPSIS
    Pester 5 tests for tools/classify-lane.ps1 (OPT-18 lane classifier).

.DESCRIPTION
    Builds throwaway git repos in TEMP with a minimal phase-table.json (lanes only) and known
    diffs, then asserts the recommended lane + that the phase list is read from the table.
#>
BeforeAll {

$ScriptDir  = $PSScriptRoot
$Classifier = Join-Path $ScriptDir 'classify-lane.ps1'

$FixtureTable = @'
{
  "schema_version": 6,
  "lanes": {
    "trivial":  {"phases": [2, 9], "signal": "TRIVIAL LANE: {N} lines of comments/whitespace", "description": "t"},
    "express":  {"phases": [1, 2, 3, 4, 5, 7, 9, 10], "signal": "EXPRESS LANE: {N} lines changed", "description": "e"},
    "standard": {"phases": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "signal": null, "description": "s"},
    "max":      {"phases": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "signal": null, "description": "m", "gates": [2, 3, 4, 5]}
  },
  "phases": []
}
'@

function New-GitFixture {
    param([string]$Slug)
    $repo = Join-Path $env:TEMP "cl-test-$Slug-$(Get-Random)"
    New-Item -ItemType Directory -Force -Path "$repo\phases" | Out-Null
    Set-Content -Path "$repo\phases\phase-table.json" -Value $FixtureTable -Encoding UTF8
    Push-Location $repo
    try {
        & git init -q
        & git config user.email 't@t.t'
        & git config user.name 'test'
        Set-Content -Path "$repo\code.ps1" -Value "Write-Host 'a'`r`nWrite-Host 'b'" -Encoding UTF8
        & git add -A
        & git commit -qm init
    } finally { Pop-Location }
    return $repo
}

function Commit-Change {
    param([string]$Repo, [string]$Content)
    Set-Content -Path "$Repo\code.ps1" -Value $Content -Encoding UTF8
    Push-Location $Repo
    try { & git add -A; & git commit -qm change } finally { Pop-Location }
}

function Add-File {
    param([string]$Repo, [string]$Name, [string]$Content)
    Set-Content -Path (Join-Path $Repo $Name) -Value $Content -Encoding UTF8
    Push-Location $Repo
    try { & git add -A; & git commit -qm "add $Name" } finally { Pop-Location }
}

}

Describe 'classify-lane.ps1' {

    It 'rigor=max routes directly to the max lane (phases include 0)' {
        $repo = New-GitFixture 'max'
        $out = (& $Classifier -Rigor max -RepoRoot $repo) -join "`n" | ConvertFrom-Json
        $out.lane | Should -Be 'max'
        ($out.phases -contains 0) | Should -Be $true
    }

    It 'classifies a small comment-only change as trivial' {
        $repo = New-GitFixture 'trivial'
        Commit-Change -Repo $repo -Content "Write-Host 'a'`r`nWrite-Host 'b'`r`n# a new comment line"
        $out = (& $Classifier -RepoRoot $repo -Base HEAD~1) -join "`n" | ConvertFrom-Json
        $out.lane | Should -Be 'trivial'
        $out.appears_comment_only | Should -Be $true
    }

    It 'classifies a small functional change as express with the express phase list' {
        $repo = New-GitFixture 'express'
        Commit-Change -Repo $repo -Content "Write-Host 'a'`r`nWrite-Host 'b'`r`nWrite-Host 'c'`r`nWrite-Host 'd'"
        $out = (& $Classifier -RepoRoot $repo -Base HEAD~1) -join "`n" | ConvertFrom-Json
        $out.lane | Should -Be 'express'
        ($out.phases -join ',') | Should -Be '1,2,3,4,5,7,9,10'
    }

    It 'classifies a large change as standard' {
        $repo = New-GitFixture 'standard'
        $big = (1..30 | ForEach-Object { "Write-Host 'line $_'" }) -join "`r`n"
        Commit-Change -Repo $repo -Content $big
        $out = (& $Classifier -RepoRoot $repo -Base HEAD~1) -join "`n" | ConvertFrom-Json
        $out.lane | Should -Be 'standard'
        ($out.lines_changed -ge 25) | Should -Be $true
    }

    It 'substitutes {N} in the express signal' {
        $repo = New-GitFixture 'signal'
        Commit-Change -Repo $repo -Content "Write-Host 'a'`r`nWrite-Host 'b'`r`nWrite-Host 'c'"
        $out = (& $Classifier -RepoRoot $repo -Base HEAD~1) -join "`n" | ConvertFrom-Json
        $out.signal | Should -Match '^EXPRESS LANE: \d+ lines changed$'
    }

    It 'throws on git failure (unknown base) instead of silently classifying express' {
        $repo = New-GitFixture 'gitfail'
        { & $Classifier -RepoRoot $repo -Base 'no-such-ref-xyz' } | Should -Throw
    }

    It 'counts .tsx files as code' {
        $repo = New-GitFixture 'tsx'
        Add-File -Repo $repo -Name 'app.tsx' -Content "const a = 1`r`nconst b = 2`r`nconst c = 3"
        $out = (& $Classifier -RepoRoot $repo -Base HEAD~1) -join "`n" | ConvertFrom-Json
        ($out.lines_changed -ge 3) | Should -Be $true
        $out.lane | Should -Be 'express'
    }

    It 'flags non_code_only for a docs-only change (0 code lines)' {
        $repo = New-GitFixture 'docsonly'
        Add-File -Repo $repo -Name 'NOTES.md' -Content "line one`r`nline two`r`nline three"
        $out = (& $Classifier -RepoRoot $repo -Base HEAD~1) -join "`n" | ConvertFrom-Json
        $out.lines_changed | Should -Be 0
        $out.non_code_only | Should -Be $true
    }
}
