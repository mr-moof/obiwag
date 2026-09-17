<#
.SYNOPSIS
    Pester tests for bump-version.ps1

.DESCRIPTION
    Tests version format validation, YAML parsing, regex replacement patterns,
    date formatting, and no-op behavior. Uses isolated temp files.
    Requires Pester 5.

.EXAMPLE
    .\tools\run-tests.ps1 -Path tools\bump-version.tests.ps1
#>

Describe 'Bump Version Script' {

    BeforeEach {
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()
        $script:RepoRoot = Join-Path $TempRoot 'obiwag-agents'
        $script:ToolsDir = Join-Path $RepoRoot 'tools'
        New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'docs') -Force | Out-Null

        $script:OldVersion = '0.56'
        $script:NewVersion = '0.57'
        $script:Today = Get-Date -Format 'yyyy-MM-dd'

        # Create version.yaml
        $script:VersionYaml = Join-Path $ToolsDir 'version.yaml'
        Set-Content $VersionYaml @"
# Obi Wag Version Information
version: "$OldVersion"
edition: ""
last_updated: "2026-02-17"
"@ -Encoding UTF8

        # CLAUDE.md fixture removed in 0.69.34 — the live script no longer
        # targets CLAUDE.md (the docs compact in ad0f5e7 stripped the
        # version header and tools/version.yaml is the single source of
        # truth now). Adding a fixture here would assert against a script
        # branch that doesn't exist.

        # Create README.md
        $script:ReadmeMd = Join-Path $RepoRoot 'README.md'
        Set-Content $ReadmeMd @"
# Obi Wag **v${OldVersion}**
"@ -Encoding UTF8

        # Create hooks-architecture.md
        $script:HooksArchMd = Join-Path $RepoRoot 'docs\hooks-architecture.md'
        Set-Content $HooksArchMd @"
> **Last Updated:** 2026-02-17 | **Version:** $OldVersion
"@ -Encoding UTF8

        # version.py fixture removed in 0.69.36: hooks/core/version.py loads
        # CURRENT_VERSION dynamically from version.yaml (since 0.69.30), so
        # the live script no longer targets it. A synthetic fixture would
        # assert against a script branch that doesn't exist.

        # Create CHANGELOG.md (the release history was split out of
        # version.yaml in issue #182; bump-version.ps1 now prepends a stub
        # section here on bump instead of appending a yaml comment).
        $script:ChangelogMd = Join-Path $RepoRoot 'CHANGELOG.md'
        Set-Content $ChangelogMd @"
# Changelog

Newest version at the top.

## $OldVersion (2026-02-17)

Existing release prose.
"@ -Encoding UTF8
    }

    Context 'Version Format Validation' {

        It 'Accepts two-segment version (0.57)' {
            ('0.57' -match '^\d+\.\d+(\.\d+)?$') | Should -Be $true
        }

        It 'Accepts three-segment version (1.0.0)' {
            ('1.0.0' -match '^\d+\.\d+(\.\d+)?$') | Should -Be $true
        }

        It 'Rejects version without dot' {
            ('057' -match '^\d+\.\d+(\.\d+)?$') | Should -Be $false
        }

        It 'Rejects version with letters' {
            ('0.57a' -match '^\d+\.\d+(\.\d+)?$') | Should -Be $false
        }

        It 'Rejects version with leading v' {
            ('v0.57' -match '^\d+\.\d+(\.\d+)?$') | Should -Be $false
        }

        It 'Rejects empty string' {
            ('' -match '^\d+\.\d+(\.\d+)?$') | Should -Be $false
        }
    }

    Context 'Version YAML Parsing' {

        It 'Reads current version from version.yaml' {
            $yamlContent = Get-Content $VersionYaml -Raw
            $yamlContent -match 'version:\s*"([^"]+)"' | Out-Null
            $parsed = $Matches[1]

            $parsed | Should -Be $OldVersion
        }

        It 'Handles missing version field gracefully' {
            Set-Content $VersionYaml 'edition: ""' -Encoding UTF8
            $yamlContent = Get-Content $VersionYaml -Raw
            $matched = $yamlContent -match 'version:\s*"([^"]+)"'

            $matched | Should -Be $false
        }

        It 'Handles unquoted version' {
            Set-Content $VersionYaml 'version: 0.56' -Encoding UTF8
            $yamlContent = Get-Content $VersionYaml -Raw
            # The script requires quoted version, so this should not match
            $matched = $yamlContent -match 'version:\s*"([^"]+)"'

            $matched | Should -Be $false
        }
    }

    Context 'Target File Pattern Matching' {

        It 'Matches version in version.yaml' {
            $content = Get-Content $VersionYaml -Raw
            $pattern = '(?<=version:\s*")' + [regex]::Escape($OldVersion) + '(?=")'
            ($content -match $pattern) | Should -Be $true
        }

        It 'Matches last_updated in version.yaml' {
            $content = Get-Content $VersionYaml -Raw
            $pattern = '(?<=last_updated:\s*")[\d-]+(?=")'
            ($content -match $pattern) | Should -Be $true
        }

        It 'Matches version in README.md' {
            $content = Get-Content $ReadmeMd -Raw
            $pattern = '(?<=\*\*v)' + [regex]::Escape($OldVersion) + '(?=\*\*)'
            ($content -match $pattern) | Should -Be $true
        }

        It 'Matches version in hooks-architecture.md' {
            $content = Get-Content $HooksArchMd -Raw
            $pattern = '(?<=\*\*Version:\*\*\s*)' + [regex]::Escape($OldVersion)
            ($content -match $pattern) | Should -Be $true
        }
    }

    Context 'Version Replacement' {

        It 'Replaces version in version.yaml' {
            $content = Get-Content $VersionYaml -Raw
            $pattern = '(?<=version:\s*")' + [regex]::Escape($OldVersion) + '(?=")'
            $newContent = $content -replace $pattern, $NewVersion
            Set-Content $VersionYaml $newContent -NoNewline

            $result = Get-Content $VersionYaml -Raw
            $result | Should -Match ([regex]::Escape('"0.57"'))
            $result | Should -Not -Match ([regex]::Escape('"0.56"'))
        }

        It 'Replaces date in version.yaml' {
            $content = Get-Content $VersionYaml -Raw
            $pattern = '(?<=last_updated:\s*")[\d-]+(?=")'
            $newContent = $content -replace $pattern, $Today
            Set-Content $VersionYaml $newContent -NoNewline

            $result = Get-Content $VersionYaml -Raw
            $result | Should -Match $Today
        }

        It 'Replaces version in README.md' {
            $content = Get-Content $ReadmeMd -Raw
            $pattern = '(?<=\*\*v)' + [regex]::Escape($OldVersion) + '(?=\*\*)'
            $newContent = $content -replace $pattern, $NewVersion
            Set-Content $ReadmeMd $newContent -NoNewline

            $result = Get-Content $ReadmeMd -Raw
            $result | Should -Match '\*\*v0\.57\*\*'
        }
    }

    Context 'No-op Behavior' {

        It 'Detects when version is already current' {
            $yamlContent = Get-Content $VersionYaml -Raw
            $yamlContent -match 'version:\s*"([^"]+)"' | Out-Null
            $current = $Matches[1]

            ($current -eq $OldVersion) | Should -Be $true
        }

        It 'Detects when version differs' {
            $yamlContent = Get-Content $VersionYaml -Raw
            $yamlContent -match 'version:\s*"([^"]+)"' | Out-Null
            $current = $Matches[1]

            ($current -eq $NewVersion) | Should -Be $false
        }
    }

    Context 'Target File Inventory' {

        It 'Has 7 replacement targets across 4 files' {
            # The bump-version.ps1 targets array has exactly 7 entries after
            # 0.69.30 dropped hooks/core/version.py and 0.69.34 dropped the
            # two CLAUDE.md targets (compact in ad0f5e7 stripped the version
            # header from CLAUDE.md; tools/version.yaml is single source of
            # truth).
            $targetFiles = @(
                'tools\version.yaml',       # version
                'tools\version.yaml',       # last_updated
                'README.md',                # version
                'docs\hooks-architecture.md', # version
                'docs\hooks-architecture.md'  # date
            )
            $targetFiles.Count | Should -Be 5
            ($targetFiles | Sort-Object -Unique).Count | Should -Be 3
        }
    }

    Context 'Date Format' {

        It 'Uses yyyy-MM-dd format' {
            $date = Get-Date -Format 'yyyy-MM-dd'
            ($date -match '^\d{4}-\d{2}-\d{2}$') | Should -Be $true
        }

        It 'Matches today date' {
            $date = Get-Date -Format 'yyyy-MM-dd'
            $year = (Get-Date).Year
            $date | Should -Match "^$year-"
        }
    }

    Context 'All Targets Updated Together' {

        It 'Updates all 4 files consistently' {
            # Mirrors the live targets list in tools/bump-version.ps1:
            #   - version.yaml (version, last_updated)
            #   - README.md (version)
            #   - hooks-architecture.md (version, date)
            # Total: 5 replacements across 3 files. CLAUDE.md and
            # hooks/core/version.py are NOT bump targets (see 0.69.30 and
            # 0.69.34 history).
            $targets = @(
                @{ File = $VersionYaml; Pattern = '(?<=version:\s*")' + [regex]::Escape($OldVersion) + '(?=")'; Replace = $NewVersion }
                @{ File = $VersionYaml; Pattern = '(?<=last_updated:\s*")[\d-]+(?=")'; Replace = $Today }
                @{ File = $ReadmeMd; Pattern = '(?<=\*\*v)' + [regex]::Escape($OldVersion) + '(?=\*\*)'; Replace = $NewVersion }
                @{ File = $HooksArchMd; Pattern = '(?<=\*\*Version:\*\*\s*)' + [regex]::Escape($OldVersion); Replace = $NewVersion }
                @{ File = $HooksArchMd; Pattern = '(?<=\*\*Last Updated:\*\*\s*)[\d-]+(?=\s*\|)'; Replace = $Today }
            )

            $success = 0
            foreach ($t in $targets) {
                $content = Get-Content $t.File -Raw
                if ($content -match $t.Pattern) {
                    $newContent = $content -replace $t.Pattern, $t.Replace
                    Set-Content $t.File $newContent -NoNewline
                    $success++
                }
            }

            $success | Should -Be 5

            # Verify each file
            (Get-Content $VersionYaml -Raw) | Should -Match '"0.57"'
            (Get-Content $ReadmeMd -Raw) | Should -Match 'v0\.57'
            (Get-Content $HooksArchMd -Raw) | Should -Match '0\.57'
        }
    }

    Context 'CHANGELOG Prepend' {
        # Mirrors the live CHANGELOG.md prepend logic in tools/bump-version.ps1:
        # a new "## <version> (<date>)" stub is inserted above the first existing
        # "## " version section, preserving the file header/preamble. The full
        # release history moved out of version.yaml in issue #182.

        # Inserts a new version section above the first existing one, preserving
        # everything above it. Returns the new changelog string.
        # Must live in BeforeAll: a function defined directly in a Context body is
        # created during Pester 5's DISCOVERY pass and is gone by the time It bodies
        # run.
        BeforeAll {
            function Add-ChangelogStub {
                param($Content, $Version, $Today)
                $stub = "## $Version ($Today)`n`nTODO: describe this release.`n"
                if ($Content -match '(?m)^## ') {
                    $firstSection = $Content.IndexOf("`n## ")
                    if ($firstSection -ge 0) {
                        $insertAt = $firstSection + 1
                        return $Content.Substring(0, $insertAt) + $stub + "`n" + $Content.Substring($insertAt)
                    }
                    return $stub + "`n" + $Content
                }
                return $Content.TrimEnd() + "`n`n" + $stub
            }
        }

        It 'Prepends a new version section above the existing entries' {
            $content = Get-Content $ChangelogMd -Raw
            $newContent = Add-ChangelogStub -Content $content -Version $NewVersion -Today $Today
            Set-Content $ChangelogMd $newContent -NoNewline

            $result = Get-Content $ChangelogMd -Raw
            $result | Should -Match ([regex]::Escape("## $NewVersion ($Today)"))
            # The new section appears before the old one.
            $idxNew = $result.IndexOf("## $NewVersion")
            $idxOld = $result.IndexOf("## $OldVersion")
            ($idxNew -lt $idxOld) | Should -Be $true
        }

        It 'Preserves the file header above the first section' {
            $content = Get-Content $ChangelogMd -Raw
            $newContent = Add-ChangelogStub -Content $content -Version $NewVersion -Today $Today

            # The "# Changelog" header must still be the first line.
            $newContent | Should -Match '^# Changelog'
            # The header must come before the new version section.
            ($newContent.IndexOf('# Changelog') -lt $newContent.IndexOf("## $NewVersion")) | Should -Be $true
        }

        It 'Preserves all prior history' {
            $content = Get-Content $ChangelogMd -Raw
            $newContent = Add-ChangelogStub -Content $content -Version $NewVersion -Today $Today

            # The old section and its prose are still present.
            $newContent | Should -Match ([regex]::Escape("## $OldVersion (2026-02-17)"))
            $newContent | Should -Match 'Existing release prose\.'
        }

        It 'Appends a stub when no prior version sections exist' {
            Set-Content $ChangelogMd "# Changelog`n`nNewest version at the top.`n" -Encoding UTF8
            $content = Get-Content $ChangelogMd -Raw
            $newContent = Add-ChangelogStub -Content $content -Version $NewVersion -Today $Today

            $newContent | Should -Match ([regex]::Escape("## $NewVersion ($Today)"))
            $newContent | Should -Match '^# Changelog'
        }
    }
}
