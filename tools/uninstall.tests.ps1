<#
.SYNOPSIS
    Pester tests for uninstall.ps1 (#169).

.DESCRIPTION
    Mirrors the same fragment-test style as deploy.tests.ps1: reproduces
    the production logic in `$TestDrive` and asserts behavior. Script-
    level subprocess invocation is deferred to follow-up infra work
    (#173 / #145 / #146).
#>

Describe 'Uninstall Script' {

    BeforeEach {
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()
        $script:ClaudeRoot = Join-Path $TempRoot '.claude'
        $script:CodexRoot  = Join-Path $TempRoot '.codex'
        $script:ToolsRoot  = Join-Path $TempRoot 'obi-tools'
        $script:RepoRoot   = Join-Path $TempRoot 'repo'

        foreach ($d in @(
            $ClaudeRoot, (Join-Path $ClaudeRoot 'commands'),
            (Join-Path $ClaudeRoot 'docs'), (Join-Path $ClaudeRoot 'hooks'),
            (Join-Path $ClaudeRoot '.obi'),
            (Join-Path $ToolsRoot 'tools'),
            $RepoRoot, (Join-Path $RepoRoot 'phases\01-discovery'),
            (Join-Path $RepoRoot 'docs'), (Join-Path $RepoRoot 'hooks')
        )) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
        }
    }

    Context 'Root-containment guard' {

        # In BeforeAll: a function defined directly in a Context body is created during
        # Pester 5's DISCOVERY pass and is gone when It bodies run.
        BeforeAll {
            function script:TestPathUnderAnyRootInline {
                param([string]$CandidatePath, [string[]]$AllowedRoots)
                try {
                    $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
                    # Mirrors production: GetFullPath only THROWS on an embedded drive
                    # spec under .NET Framework (5.1). On pwsh 7 it returns the string
                    # verbatim, so reject an embedded volume separator explicitly or the
                    # guard ACCEPTS "<root>\C:\evil.exe".
                    if ($resolvedCandidate.IndexOf([System.IO.Path]::VolumeSeparatorChar, 2) -ge 0) {
                        return $false
                    }
                    foreach ($root in $AllowedRoots) {
                        $resolvedRoot = [System.IO.Path]::GetFullPath($root)
                        if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
                            $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
                        }
                        if ($resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                            return $true
                        }
                    }
                    return $false
                } catch {
                    return $false
                }
            }
        }

        It 'Accepts path under one of the allowed roots' {
            $allowedRoots = @($ClaudeRoot, $CodexRoot)
            $candidate = Join-Path $ClaudeRoot 'commands\discovery.md'
            (TestPathUnderAnyRootInline -CandidatePath $candidate -AllowedRoots $allowedRoots) | Should -Be $true
        }

        It 'Rejects path outside any allowed root' {
            $allowedRoots = @($ClaudeRoot, $CodexRoot)
            $candidate = Join-Path $TempRoot 'outside.md'
            (TestPathUnderAnyRootInline -CandidatePath $candidate -AllowedRoots $allowedRoots) | Should -Be $false
        }

        It 'Rejects ../ traversal' {
            $allowedRoots = @($ClaudeRoot)
            $candidate = Join-Path $ClaudeRoot '..\outside.md'
            (TestPathUnderAnyRootInline -CandidatePath $candidate -AllowedRoots $allowedRoots) | Should -Be $false
        }

        It 'Rejects malformed paths via try/catch' {
            $allowedRoots = @($ClaudeRoot)
            $malformed = "$ClaudeRoot\C:\evil.exe"
            (TestPathUnderAnyRootInline -CandidatePath $malformed -AllowedRoots $allowedRoots) | Should -Be $false
        }
    }

    Context 'Manifest key resolution' {

        # In BeforeAll: see the note in 'Root-containment guard' above.
        BeforeAll {
            function script:ResolveKeyInline {
                param(
                    [string]$ManifestKey,
                    [string]$Platform,
                    [string]$ClaudeTarget,
                    [string]$CodexTarget,
                    [string]$ToolsTarget,
                    [string]$RepoRoot
                )
                if ([System.IO.Path]::IsPathRooted($ManifestKey)) { return $ManifestKey }
                $normalizedKey = $ManifestKey.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
                if ($Platform -eq 'claude') {
                    if ($ManifestKey -like 'tools/*') {
                        $rel = $ManifestKey.Substring('tools/'.Length).Replace('/', [System.IO.Path]::DirectorySeparatorChar)
                        return Join-Path (Join-Path $ToolsTarget 'tools') $rel
                    }
                    return Join-Path $ClaudeTarget $normalizedKey
                }
                if ($ManifestKey -eq 'AGENTS.md') { return Join-Path $RepoRoot 'AGENTS.md' }
                if ($ManifestKey -like '.codex/*') { return Join-Path $RepoRoot $normalizedKey }
                return Join-Path $CodexTarget $normalizedKey
            }
        }

        It 'Claude root-level keys resolve under ClaudeTarget' {
            $resolved = ResolveKeyInline -ManifestKey 'CLAUDE.md' -Platform 'claude' `
                -ClaudeTarget $ClaudeRoot -CodexTarget $CodexRoot -ToolsTarget $ToolsRoot -RepoRoot $RepoRoot
            $resolved | Should -Be (Join-Path $ClaudeRoot 'CLAUDE.md')
        }

        It 'Claude commands/agents/skills/docs/hooks keys resolve under ClaudeTarget' {
            $resolved = ResolveKeyInline -ManifestKey 'commands/discovery.md' -Platform 'claude' `
                -ClaudeTarget $ClaudeRoot -CodexTarget $CodexRoot -ToolsTarget $ToolsRoot -RepoRoot $RepoRoot
            $resolved | Should -Be (Join-Path $ClaudeRoot 'commands\discovery.md')
        }

        It 'Claude tools/ keys resolve under ToolsTarget (endpoint security carve-out)' {
            $resolved = ResolveKeyInline -ManifestKey 'tools/statusline-command.ps1' -Platform 'claude' `
                -ClaudeTarget $ClaudeRoot -CodexTarget $CodexRoot -ToolsTarget $ToolsRoot -RepoRoot $RepoRoot
            $resolved | Should -Be (Join-Path $ToolsRoot 'tools\statusline-command.ps1')
        }

        It 'Codex absolute keys are returned verbatim' {
            $absolute = 'C:\src\obi-tools\hooks\core\paths.py'
            $resolved = ResolveKeyInline -ManifestKey $absolute -Platform 'codex' `
                -ClaudeTarget $ClaudeRoot -CodexTarget $CodexRoot -ToolsTarget $ToolsRoot -RepoRoot $RepoRoot
            $resolved | Should -Be $absolute
        }

        It 'Codex relative skills/ keys resolve under CodexTarget' {
            $resolved = ResolveKeyInline -ManifestKey 'skills/verify/SKILL.md' -Platform 'codex' `
                -ClaudeTarget $ClaudeRoot -CodexTarget $CodexRoot -ToolsTarget $ToolsRoot -RepoRoot $RepoRoot
            $resolved | Should -Be (Join-Path $CodexRoot 'skills\verify\SKILL.md')
        }

        It 'Codex AGENTS.md key resolves under RepoRoot' {
            $resolved = ResolveKeyInline -ManifestKey 'AGENTS.md' -Platform 'codex' `
                -ClaudeTarget $ClaudeRoot -CodexTarget $CodexRoot -ToolsTarget $ToolsRoot -RepoRoot $RepoRoot
            $resolved | Should -Be (Join-Path $RepoRoot 'AGENTS.md')
        }
    }

    Context 'Manifest-driven file removal' {

        It 'Removes only manifest-listed files' {
            $cmdsDir = Join-Path $ClaudeRoot 'commands'
            Set-Content (Join-Path $cmdsDir 'discovery.md') 'obi' -Encoding UTF8
            Set-Content (Join-Path $cmdsDir 'my-personal-cmd.md') 'mine' -Encoding UTF8

            $manifestKeys = @('commands/discovery.md')

            foreach ($key in $manifestKeys) {
                $targetPath = Join-Path $ClaudeRoot $key.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
                if (Test-Path -LiteralPath $targetPath) {
                    Remove-Item -LiteralPath $targetPath -Force
                }
            }

            (Test-Path -LiteralPath (Join-Path $cmdsDir 'discovery.md')) | Should -Be $false
            (Test-Path -LiteralPath (Join-Path $cmdsDir 'my-personal-cmd.md')) | Should -Be $true
        }

        It 'Preserves .user-backup siblings by default' {
            $cmdsDir = Join-Path $ClaudeRoot 'commands'
            Set-Content (Join-Path $cmdsDir 'discovery.md') 'obi v2' -Encoding UTF8
            Set-Content (Join-Path $cmdsDir 'discovery.md.user-backup') 'user original' -Encoding UTF8

            $targetPath = Join-Path $cmdsDir 'discovery.md'
            $backupPath = "$targetPath.user-backup"
            $RestoreUserBackups = $false

            Remove-Item -LiteralPath $targetPath -Force
            if ($RestoreUserBackups) {
                Move-Item -LiteralPath $backupPath -Destination $targetPath
            }

            (Test-Path -LiteralPath $targetPath) | Should -Be $false
            (Test-Path -LiteralPath $backupPath) | Should -Be $true
            (Get-Content $backupPath -Raw).Trim() | Should -Be 'user original'
        }

        It '-RestoreUserBackups renames .user-backup back to original filename' {
            $cmdsDir = Join-Path $ClaudeRoot 'commands'
            Set-Content (Join-Path $cmdsDir 'discovery.md') 'obi v2' -Encoding UTF8
            Set-Content (Join-Path $cmdsDir 'discovery.md.user-backup') 'user original' -Encoding UTF8

            $targetPath = Join-Path $cmdsDir 'discovery.md'
            $backupPath = "$targetPath.user-backup"
            $RestoreUserBackups = $true

            Remove-Item -LiteralPath $targetPath -Force
            if ($RestoreUserBackups) {
                Move-Item -LiteralPath $backupPath -Destination $targetPath
            }

            (Test-Path -LiteralPath $targetPath) | Should -Be $true
            (Test-Path -LiteralPath $backupPath) | Should -Be $false
            (Get-Content $targetPath -Raw).Trim() | Should -Be 'user original'
        }
    }

    Context 'settings.json restore (hash-guarded)' {

        It 'Restores settings.json from .backup when hashes match' {
            $settingsTarget = Join-Path $ClaudeRoot 'settings.json'
            $settingsBackup = Join-Path $ClaudeRoot 'settings.json.backup'
            $repoSource = Join-Path $TempRoot 'users\user\settings.json'
            New-Item -ItemType Directory -Path (Split-Path $repoSource -Parent) -Force | Out-Null

            $obiContent = '{"obi": "deployed"}'
            Set-Content $repoSource $obiContent -Encoding UTF8 -NoNewline
            # User hasn't edited the deployed file — it still matches the source
            Set-Content $settingsTarget $obiContent -Encoding UTF8 -NoNewline
            Set-Content $settingsBackup '{"user": "original"}' -Encoding UTF8 -NoNewline

            $currentHash  = (Get-FileHash -LiteralPath $settingsTarget -Algorithm SHA256).Hash
            $obiSrcHash   = (Get-FileHash -LiteralPath $repoSource -Algorithm SHA256).Hash
            $shouldRestore = ($currentHash -eq $obiSrcHash)

            if ($shouldRestore) {
                Remove-Item -LiteralPath $settingsTarget -Force
                Move-Item -LiteralPath $settingsBackup -Destination $settingsTarget
            }

            $shouldRestore | Should -Be $true
            (Get-Content $settingsTarget -Raw).Trim() | Should -Be '{"user": "original"}'
            (Test-Path -LiteralPath $settingsBackup) | Should -Be $false
        }

        It 'Preserves settings.json backup when current has been user-edited (hash mismatch)' {
            $settingsTarget = Join-Path $ClaudeRoot 'settings.json'
            $settingsBackup = Join-Path $ClaudeRoot 'settings.json.backup'
            $repoSource = Join-Path $TempRoot 'users\user\settings.json'
            New-Item -ItemType Directory -Path (Split-Path $repoSource -Parent) -Force | Out-Null

            Set-Content $repoSource '{"obi": "deployed"}' -Encoding UTF8 -NoNewline
            # User HAS edited the deployed file since Obi wrote it
            Set-Content $settingsTarget '{"obi": "deployed", "user_addition": true}' -Encoding UTF8 -NoNewline
            Set-Content $settingsBackup '{"user": "original"}' -Encoding UTF8 -NoNewline

            $currentHash  = (Get-FileHash -LiteralPath $settingsTarget -Algorithm SHA256).Hash
            $obiSrcHash   = (Get-FileHash -LiteralPath $repoSource -Algorithm SHA256).Hash
            $shouldRestore = ($currentHash -eq $obiSrcHash)

            $shouldRestore | Should -Be $false
            # Both files unchanged: backup preserved, current settings preserved
            (Test-Path -LiteralPath $settingsBackup) | Should -Be $true
            (Get-Content $settingsTarget -Raw).Trim() | Should -Be '{"obi": "deployed", "user_addition": true}'
            (Get-Content $settingsBackup -Raw).Trim() | Should -Be '{"user": "original"}'
        }
    }

    Context 'Production parameter metadata' {
        It 'Exposes the current platform switches without the retired CopilotOnly switch' {
            $repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
            $command = Get-Command -Name (Join-Path $repoRoot 'tools\uninstall.ps1') `
                -CommandType ExternalScript -ErrorAction Stop

            foreach ($parameterName in @('DryRun', 'RestoreUserBackups', 'ClaudeOnly', 'CodexOnly')) {
                $command.Parameters.ContainsKey($parameterName) | Should -Be $true
            }
            $command.Parameters.ContainsKey('CopilotOnly') | Should -Be $false
        }
    }
}
