BeforeAll {
    $SourceScript = Join-Path $PSScriptRoot 'peer-plan-review.ps1'
}

Describe 'peer-plan-review' {
    BeforeEach {
        $script:Harness = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $Harness | Out-Null
        Copy-Item -LiteralPath $SourceScript -Destination (Join-Path $Harness 'peer-plan-review.ps1')
        $script:Plan = Join-Path $TestDrive 'outside-plan.md'
        Set-Content -LiteralPath $Plan -Value '# Plan' -Encoding UTF8
        $fake = @'
param($Operation, $Provider, $Platform, $Authorization, $ApprovalScopeSha256, $RepoRoot, $RequestFile, $TimeoutSec)
$request = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json
[ordered]@{
  operation = $Operation
  provider = $Provider
  platform = $Platform
  authorization = $Authorization
  approval_scope_sha256 = $ApprovalScopeSha256
  repo_root = $RepoRoot
  timeout_sec = $TimeoutSec
  request_file = $RequestFile
  objective = $request.objective
  attachment_label = $request.attachments[0].label
  attachment_path = $request.attachments[0].path
} | ConvertTo-Json -Compress
exit 0
'@
        Set-Content -LiteralPath (Join-Path $Harness 'peer-review.ps1') -Value $fake -Encoding UTF8
    }

    It 'creates one exact plan attachment and delegates semantically' {
        $raw = & (Join-Path $Harness 'peer-plan-review.ps1') -PlanPath $Plan `
            -RepoRoot $TestDrive -Provider auto -Platform codex -TimeoutSec 77
        $result = ($raw | Out-String).Trim() | ConvertFrom-Json
        $result.operation | Should -Be 'start'
        $result.provider | Should -Be 'auto'
        $result.platform | Should -Be 'codex'
        $result.authorization | Should -Be 'auto'
        $result.timeout_sec | Should -Be 77
        $result.attachment_label | Should -Be 'plan'
        $result.attachment_path | Should -Be (Resolve-Path $Plan).Path
        $result.objective | Should -Match 'Adversarially review'
        (Test-Path -LiteralPath $result.request_file) | Should -Be $false
    }

    It 'can explicitly select the bounded foreground lane' {
        $raw = & (Join-Path $Harness 'peer-plan-review.ps1') -PlanPath $Plan `
            -RepoRoot $TestDrive -Operation run -TimeoutSec 120 -Authorization approved `
            -ApprovalScopeSha256 (('a' * 64) -join '')
        $result = ($raw | Out-String).Trim() | ConvertFrom-Json
        $result.operation | Should -Be 'run'
        $result.timeout_sec | Should -Be 120
        $result.authorization | Should -Be 'approved'
        $result.approval_scope_sha256 | Should -Be (('a' * 64) -join '')
    }

    It 'supports no-packet preflight without a timeout' {
        $raw = & (Join-Path $Harness 'peer-plan-review.ps1') -PlanPath $Plan `
            -RepoRoot $TestDrive -Operation preflight -Platform codex
        $result = ($raw | Out-String).Trim() | ConvertFrom-Json
        $result.operation | Should -Be 'preflight'
        $result.platform | Should -Be 'codex'
        $result.timeout_sec | Should -BeNullOrEmpty
    }

    It 'fails fast when the plan is missing' {
        $output = & powershell.exe -NoProfile -File (Join-Path $Harness 'peer-plan-review.ps1') `
            -PlanPath (Join-Path $TestDrive 'missing.md') -RepoRoot $TestDrive 2>&1
        ($output | Out-String) | Should -Match 'plan file not found'
        $LASTEXITCODE | Should -Be 2
    }

    It 'exposes no provider CLI pass-through parameter' {
        $text = Get-Content -LiteralPath $SourceScript -Raw
        $text | Should -Not -Match 'CodexArgs'
        $text | Should -Not -Match 'codex-run'
        $text | Should -Match ([regex]::Escape('Join-Path $PSScriptRoot ''peer-review.ps1'''))
    }
}
