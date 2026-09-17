<#
.SYNOPSIS
    Pester 5 tests for tools/audit-docs.ps1.

.DESCRIPTION
    Every case builds a throwaway git repo in $TestDrive, because the script derives its
    file list from `git ls-files --cached --others --exclude-standard`. The point of these tests is to prove each sweep can go
    RED for the defect it exists to catch -- a gate only ever observed printing "clean" is
    not evidence it works (that is exactly how the rigor=max grep gate stayed a silent
    no-op for months).

.EXAMPLE
    .\tools\run-tests.ps1 -Path tools\audit-docs.tests.ps1
#>
BeforeAll {

$ScriptPath = Join-Path $PSScriptRoot 'audit-docs.ps1'

$TABLE = @'
{
  "schema_version": 6,
  "lanes": { "standard": { "phases": [1], "signal": null, "description": "s" } },
  "phases": [
    {
      "n": 1, "name": "Discovery", "agent": "obi-discovery",
      "delegated": true, "default_strategy": "dispatch", "recipe": null,
      "inline_fallback_eligible": false,
      "primary_signal": {"value": "DISCOVERY COMPLETE", "match_mode": "literal"},
      "special_signals": [], "verdict_in_body": false,
      "display": {"command": "/discovery", "model": "opus", "strategy_note": null,
                  "recipe_note": null, "fallback_note": null,
                  "signal_display": "`DISCOVERY COMPLETE`"}
    }
  ]
}
'@

$AGENT = @'
---
name: obi-discovery
description: Research specialist.
tools: Read, Grep
model: opus[1m]
---

Do research. Emit DISCOVERY COMPLETE when done.
'@

function New-AuditRepo {
    param([string]$Slug)
    $repo = Join-Path $TestDrive "audit-$Slug"
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'phases') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'platforms\claude-code\agents') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'docs') | Out-Null

    # WriteAllText + UTF8Encoding($false), NOT Set-Content -Encoding UTF8: under
    # PowerShell 5.1 the latter emits a BOM, and the agent fixture starts with '---',
    # so every fixture would trip Sweep C's BOM-before-frontmatter check. A baseline
    # fixture must represent a HEALTHY repo; the BOM case is constructed explicitly in
    # the Sweep C tests instead.
    $noBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $repo 'phases\phase-table.json'), $TABLE, $noBom)
    [System.IO.File]::WriteAllText((Join-Path $repo 'platforms\claude-code\agents\obi-discovery.md'), $AGENT, $noBom)
    [System.IO.File]::WriteAllText((Join-Path $repo 'docs\ok.md'), 'See `phases/phase-table.json` for routing.', $noBom)

    Push-Location $repo
    try {
        # `2>&1 | Out-Null` (not `2>$null`): git writes notices to stderr, and under
        # $ErrorActionPreference='Stop' a native command's stderr becomes a terminating
        # RemoteException inside the test body -- the fixture would then "fail" on a
        # benign "LF will be replaced by CRLF" notice. autocrlf=false stops that notice
        # at the source; the redirect keeps the fixture immune to the caller's preference.
        & git init -q 2>&1 | Out-Null
        & git config core.autocrlf false 2>&1 | Out-Null
        & git config user.email 't@t.t' 2>&1 | Out-Null
        & git config user.name 'test' 2>&1 | Out-Null
        & git add -A 2>&1 | Out-Null
        & git commit -qm init 2>&1 | Out-Null
    } finally { Pop-Location }
    return $repo
}

# Add a tracked doc after init (git ls-files needs it staged).
function Add-Doc {
    param([string]$Repo, [string]$Name, [string]$Body)
    $p = Join-Path $Repo $Name
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $p) | Out-Null
    [System.IO.File]::WriteAllText($p, $Body, (New-Object System.Text.UTF8Encoding($false)))
    Push-Location $Repo
    try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }
}

function Add-UntrackedDoc {
    param([string]$Repo, [string]$Name, [string]$Body)
    $p = Join-Path $Repo $Name
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $p) | Out-Null
    [System.IO.File]::WriteAllText($p, $Body, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Audit {
    param([string]$Repo)
    $out = & powershell -NoProfile -File $ScriptPath -RepoRoot $Repo 2>&1
    return [pscustomobject]@{ Output = ($out -join "`n"); Exit = $LASTEXITCODE }
}

}

Describe 'audit-docs' {

    Context 'Sweep A - dangling path references' {

        It 'passes on a repo whose refs all resolve' {
            $repo = New-AuditRepo 'clean'
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }

        It 'FAILS on a reference to a path that does not exist' {
            $repo = New-AuditRepo 'dangling'
            Add-Doc -Repo $repo -Name 'docs\bad.md' -Body 'Read `docs/no-such-file.md` first.'
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS FAIL'
            $r.Output | Should -Match 'docs/no-such-file\.md'
            $r.Exit | Should -Be 1
        }

        It 'FAILS on a dangling reference in a new untracked proposal' {
            $repo = New-AuditRepo 'untracked-dangling'
            Add-UntrackedDoc -Repo $repo -Name 'docs\proposal-new.md' -Body 'Read `docs/no-such-new-file.md` first.'
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'docs/no-such-new-file\.md'
            $r.Exit | Should -Be 1
        }

        It 'ignores runtime markdown under .obi' {
            $repo = New-AuditRepo 'runtime-state'
            Add-UntrackedDoc -Repo $repo -Name '.obi\state\resume.md' -Body 'Generated pointer `some/missing.md`.'
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }

        It 'does NOT flag docs/policies/* (policies deploy there, so source-dangling is correct)' {
            $repo = New-AuditRepo 'policyref'
            Add-Doc -Repo $repo -Name 'docs\pol.md' -Body 'Rules: `docs/policies/zero-hallucination.md`.'
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }

        It 'does NOT flag deploy targets or env/template placeholders' {
            $repo = New-AuditRepo 'benign'
            Add-Doc -Repo $repo -Name 'docs\benign.md' -Body @'
Deployed as `commands/discovery.md` and `agents/obi-discovery.md`.
Runtime state in `.obi/state/run-id.txt`. Settings at `users/$USER/settings.json`.
Graphify output at `graphify-out/GRAPH_REPORT.md` (generated, gitignored).
Template form is `phases/0N-name/command.md`.
'@
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }
    }

    Context 'Sweep B - agent vs phase-table drift' {

        It 'FAILS when the agent pins a model the table reports as unset' {
            # The real 0.69.71 defect: agents moved to opus[1m] but display.model stayed null.
            $repo = New-AuditRepo 'modeldrift'
            $t = Join-Path $repo 'phases\phase-table.json'
            $c = [System.IO.File]::ReadAllText($t).Replace('"model": "opus"', '"model": null')
            [System.IO.File]::WriteAllText($t, $c)
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'model drift'
            $r.Exit | Should -Be 1
        }

        It 'FAILS when the primary_signal string is absent from the agent body' {
            $repo = New-AuditRepo 'nosignal'
            $a = Join-Path $repo 'platforms\claude-code\agents\obi-discovery.md'
            $c = [System.IO.File]::ReadAllText($a).Replace('DISCOVERY COMPLETE', 'ALL DONE')
            [System.IO.File]::WriteAllText($a, $c)
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match "primary_signal 'DISCOVERY COMPLETE' not present"
            $r.Exit | Should -Be 1
        }

        It 'FAILS when a phase-table entry has no agent file' {
            $repo = New-AuditRepo 'noagent'
            Remove-Item (Join-Path $repo 'platforms\claude-code\agents\obi-discovery.md') -Force
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'agent file missing'
            $r.Exit | Should -Be 1
        }
    }

    Context 'Sweep C - BOM before YAML frontmatter' {

        It 'FAILS when a frontmatter file starts with a UTF-8 BOM' {
            # The real 0.69.73 regression: a PS 5.1 `Set-Content -Encoding UTF8` rewrite
            # added a BOM to orchestration/obi-auto.md and the deployed command's
            # description parsed as the literal "---".
            $repo = New-AuditRepo 'bomfront'
            $p = Join-Path $repo 'docs\cmd.md'
            $body = "---`ndescription: A command.`n---`n`nBody text.`n"
            [System.IO.File]::WriteAllText($p, $body, (New-Object System.Text.UTF8Encoding($true)))
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'BOM before YAML frontmatter'
            $r.Exit | Should -Be 1
        }

        It 'does NOT flag a BOM on a file without frontmatter' {
            $repo = New-AuditRepo 'bomplain'
            $p = Join-Path $repo 'docs\plain.md'
            [System.IO.File]::WriteAllText($p, "# Heading`n`nNo frontmatter here.`n", (New-Object System.Text.UTF8Encoding($true)))
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }
    }

    Context 'Sweep D - peer enum vs catch-log taxonomy' {

        # The plan-critique schema is only useful because an accepted finding maps 1:1 onto a
        # log-codex-catch.ps1 call. That rests on two hand-maintained lists agreeing, with
        # nothing linking them -- exactly the drift this repo keeps getting bitten by.
        # In BeforeAll: a function defined directly in a Context body is created during
        # Pester 5's DISCOVERY pass and is gone when It bodies run.
        BeforeAll {
            function Add-PeerPair {
                param([string]$Repo, [string[]]$Enum, [string[]]$Taxonomy)
                $schemaDir = Join-Path $Repo 'tools\schemas'
                New-Item -ItemType Directory -Force -Path $schemaDir | Out-Null
                $enumJson = ($Enum | ForEach-Object { '"' + $_ + '"' }) -join ','
                $schema = '{"properties":{"findings":{"items":{"properties":{"category":{"enum":[' + $enumJson + ']}}}}}}'
                [System.IO.File]::WriteAllText((Join-Path $schemaDir 'peer-review-result.schema.json'), $schema, (New-Object System.Text.UTF8Encoding($false)))

                $polDir = Join-Path $Repo 'policies'
                New-Item -ItemType Directory -Force -Path $polDir | Out-Null
                $rows = ($Taxonomy | ForEach-Object { "| ``$_`` | meaning |" }) -join "`n"
                $policy = "# Peer review`n`n### Accepted-finding taxonomy`n`n$rows`n"
                [System.IO.File]::WriteAllText((Join-Path $polDir 'peer-review.md'), $policy, (New-Object System.Text.UTF8Encoding($false)))

                Push-Location $Repo
                try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }
            }
        }

        It 'passes when the enum and the taxonomy match' {
            $repo = New-AuditRepo 'enummatch'
            Add-PeerPair -Repo $repo -Enum @('plan-gap','test-gap') -Taxonomy @('plan-gap','test-gap')
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }

        It 'FAILS when the schema enum has a category the taxonomy lacks' {
            $repo = New-AuditRepo 'enumextra'
            Add-PeerPair -Repo $repo -Enum @('plan-gap','zzz-bogus') -Taxonomy @('plan-gap')
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'absent from the peer-review\.md taxonomy'
            $r.Exit | Should -Be 1
        }

        It 'FAILS when the taxonomy has a category the schema enum lacks' {
            $repo = New-AuditRepo 'enummissing'
            Add-PeerPair -Repo $repo -Enum @('plan-gap') -Taxonomy @('plan-gap','security')
            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'absent from the schema enum'
            $r.Exit | Should -Be 1
        }

        It 'ignores backticked field rows outside the accepted-finding taxonomy' {
            $repo = New-AuditRepo 'enumothertable'
            Add-PeerPair -Repo $repo -Enum @('plan-gap') -Taxonomy @('plan-gap')
            $policyPath = Join-Path $repo 'policies\peer-review.md'
            $policy = [System.IO.File]::ReadAllText($policyPath)
            $policy += "`n## Result contract`n`n| Field | Meaning |`n|---|---|`n| ``findings`` | accepted findings |`n"
            [System.IO.File]::WriteAllText($policyPath, $policy, (New-Object System.Text.UTF8Encoding($false)))
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'AUDIT DOCS: clean'
            $r.Exit | Should -Be 0
        }

        It 'FAILS when the accepted-finding taxonomy section is absent' {
            $repo = New-AuditRepo 'enumsectionmissing'
            Add-PeerPair -Repo $repo -Enum @('plan-gap') -Taxonomy @('plan-gap')
            $policyPath = Join-Path $repo 'policies\peer-review.md'
            $policy = [System.IO.File]::ReadAllText($policyPath).Replace('### Accepted-finding taxonomy', '### Categories')
            [System.IO.File]::WriteAllText($policyPath, $policy, (New-Object System.Text.UTF8Encoding($false)))
            Push-Location $repo; try { & git add -A 2>&1 | Out-Null } finally { Pop-Location }

            $r = Invoke-Audit -Repo $repo
            $r.Output | Should -Match 'missing the Accepted-finding taxonomy section'
            $r.Exit | Should -Be 1
        }
    }

    Context 'RepoRoot resolution' {

        It 'audits the cwd git top-level when -RepoRoot is omitted' {
            # Same trap as run-grep-gates.ps1: this script runs from the deployed
            # $env:OBI_HOME copy, so inferring the root from $PSScriptRoot would audit
            # obi-tools instead of the target repo and report a meaningless "clean".
            $repo = New-AuditRepo 'cwdroot'
            Add-Doc -Repo $repo -Name 'docs\bad.md' -Body 'Read `docs/definitely-absent.md`.'
            Push-Location $repo
            try {
                $out = & powershell -NoProfile -File $ScriptPath 2>&1
                $code = $LASTEXITCODE
            } finally { Pop-Location }
            ($out -join "`n") | Should -Match 'docs/definitely-absent\.md'
            $code | Should -Be 1
        }
    }
}
