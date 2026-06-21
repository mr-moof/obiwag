<#
.SYNOPSIS
    Pester 3.4 tests for tools/lib/dispatch-worker-lib.ps1 (OPT-23 headless dispatch helpers).
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'dispatch-worker-lib.ps1')

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$PersonaFixture = @'
---
name: obi-discovery
description: Research specialist.
tools: Read, Grep, Glob, Bash, Write
model: claude-opus-4-6[1m]
---

# Discovery Agent

You are a senior research analyst. Output DISCOVERY COMPLETE when done.
'@

Describe 'Get-PersonaParts' {
    It 'extracts tools, model, and body from frontmatter' {
        $p = Get-PersonaParts -Raw $PersonaFixture
        $p.Tools | Should Be 'Read, Grep, Glob, Bash, Write'
        $p.Model | Should Be 'claude-opus-4-6[1m]'
        $p.Body  | Should Match 'Discovery Agent'
        $p.Body  | Should Not Match 'description:'
    }

    It 'returns the whole text as Body when there is no frontmatter' {
        $p = Get-PersonaParts -Raw "no frontmatter here`njust body"
        $p.Tools | Should BeNullOrEmpty
        $p.Model | Should BeNullOrEmpty
        $p.Body  | Should Match 'just body'
    }
}

Describe 'Build-ClaudeArgs' {
    It 'always includes -p, json output, max-turns, and the system prompt' {
        $a = Build-ClaudeArgs -Prompt 'do it' -Body 'persona' -MaxTurns 40
        $a -contains '-p' | Should Be $true
        $a -contains '--output-format' | Should Be $true
        $a -contains 'json' | Should Be $true
        ($a -join ' ') | Should Match '--max-turns 40'
        $a -contains '--append-system-prompt' | Should Be $true
    }

    It 'restricts tools via --tools (split on commas), not --allowedTools' {
        $a = Build-ClaudeArgs -Prompt 'p' -Body 'b' -Tools 'Read, Grep, Bash'
        $a -contains '--tools' | Should Be $true
        $a -contains 'Read' | Should Be $true
        $a -contains 'Grep' | Should Be $true
        $a -contains 'Bash' | Should Be $true
        $a -contains '--allowedTools' | Should Be $false
    }

    It 'adds --model only when a model is given' {
        (Build-ClaudeArgs -Prompt 'p' -Body 'b' -Model 'opus') -contains '--model' | Should Be $true
        (Build-ClaudeArgs -Prompt 'p' -Body 'b') -contains '--model' | Should Be $false
    }
}

Describe 'Read-WorkerResult' {
    It 'parses result + is_error from a valid worker JSON and detects the expected signal' {
        $f = Join-Path $env:TEMP "dw-ok-$(Get-Random).json"
        '{"result":"DISCOVERY COMPLETE\nfound stuff","is_error":false}' | Set-Content $f -Encoding UTF8
        $r = Read-WorkerResult -OutFile $f -TimedOut $false -ExpectSignal 'DISCOVERY COMPLETE'
        $r.IsError | Should Be $false
        $r.SignalFound | Should Be $true
        Remove-Item $f -Force
    }

    It 'reports is_error on timeout regardless of file' {
        $r = Read-WorkerResult -OutFile 'nope.json' -TimedOut $true -ExpectSignal 'X'
        $r.IsError | Should Be $true
        $r.SignalFound | Should Be $false
    }

    It 'reports is_error when the output file is missing' {
        $r = Read-WorkerResult -OutFile (Join-Path $env:TEMP "dw-missing-$(Get-Random).json")
        $r.IsError | Should Be $true
    }

    It 'does not find the signal when the result lacks it' {
        $f = Join-Path $env:TEMP "dw-nosig-$(Get-Random).json"
        '{"result":"AUTHOR COMPLETE","is_error":false}' | Set-Content $f -Encoding UTF8
        (Read-WorkerResult -OutFile $f -ExpectSignal 'DISCOVERY COMPLETE').SignalFound | Should Be $false
        Remove-Item $f -Force
    }
}
