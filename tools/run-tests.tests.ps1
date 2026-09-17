<# Regression tests for focused test-suite routing. #>

Describe 'run-tests.ps1 path routing' {
    BeforeAll {
        $script:Runner = Join-Path $PSScriptRoot 'run-tests.ps1'
        $script:PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

        function ConvertTo-RunnerChildText {
            param([object[]]$Records)
            $text = (@($Records) | ForEach-Object { $_.ToString() }) -join ' '
            return ($text -replace '\s+', ' ').Trim()
        }
    }

    It 'does not expand one explicit Pester file into the full Python suite' {
        $sample = Join-Path $TestDrive 'sample.tests.ps1'
        Set-Content -LiteralPath $sample -Encoding UTF8 -Value @'
Describe 'sample' {
    It 'passes' { $true | Should -Be $true }
}
'@

        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner -Path $sample 2>&1 |
            Out-String -Width 4096

        $LASTEXITCODE | Should -Be 0
        $output | Should -Match 'PowerShell: PASSED=1 FAILED=0'
        $output | Should -Not -Match 'Python hook suite'
    }

    It 'routes one explicit Python file to pytest without starting Pester' {
        $sample = Join-Path $TestDrive 'test_sample.py'
        Set-Content -LiteralPath $sample -Encoding UTF8 -Value "def test_sample():`n    assert True`n"

        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner -Path $sample 2>&1 |
            Out-String -Width 4096

        $LASTEXITCODE | Should -Be 0
        $output | Should -Match 'Python: PASSED'
        $output | Should -Not -Match 'PowerShell suite'
    }

    It 'isolates same-named Python test packages across multiple roots' {
        $one = Join-Path $TestDrive 'one\tests'
        $two = Join-Path $TestDrive 'two\tests'
        foreach ($directory in @($one, $two)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            Set-Content -LiteralPath (Join-Path $directory '__init__.py') -Value '' -Encoding UTF8
            Set-Content -LiteralPath (Join-Path $directory 'test_same.py') `
                -Value "def test_from_this_root():`n    assert True`n" -Encoding UTF8
        }

        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner `
            -Path "$one,$two" 2>&1 | Out-String -Width 4096

        $LASTEXITCODE | Should -Be 0
        $output | Should -Match '2 passed'
        $output | Should -Match 'Python: PASSED'
        $output | Should -Not -Match 'import file mismatch|ModuleNotFoundError'
    }

    It 'fails instead of reporting green for an empty requested directory' {
        $empty = Join-Path $TestDrive 'empty-tests'
        New-Item -ItemType Directory -Path $empty | Out-Null

        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner -Path $empty 2>&1 |
            Out-String -Width 4096

        $LASTEXITCODE | Should -Be 2
        $output | Should -Match 'No Pester or pytest targets'
    }

    It 'accepts comma-delimited Pester paths through powershell.exe -File' {
        $one = Join-Path $TestDrive 'one.tests.ps1'
        $two = Join-Path $TestDrive 'two.tests.ps1'
        "Describe 'one' { It 'passes' { `$true | Should -Be `$true } }" |
            Set-Content -LiteralPath $one -Encoding UTF8
        "Describe 'two' { It 'passes' { `$true | Should -Be `$true } }" |
            Set-Content -LiteralPath $two -Encoding UTF8

        $combined = "$one,$two"
        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner -Path $combined 2>&1 |
            Out-String -Width 4096

        $LASTEXITCODE | Should -Be 0
        $output | Should -Match 'PowerShell: PASSED=2 FAILED=0'
    }

    It 'rejects a missing test file instead of passing an invalid Pester path' {
        $missing = Join-Path $TestDrive 'missing.tests.ps1'
        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner -Path $missing 2>&1 |
            Out-String -Width 4096

        $LASTEXITCODE | Should -Be 2
        $output | Should -Match 'Requested test target does not exist'
        $output | Should -Not -Match 'SUITE GREEN'
    }

    It 'fails for discovery errors even when another file passes' {
        $valid = Join-Path $TestDrive 'valid.tests.ps1'
        $broken = Join-Path $TestDrive 'broken.tests.ps1'
        "Describe 'valid' { It 'passes' { `$true | Should -Be `$true } }" |
            Set-Content -LiteralPath $valid -Encoding UTF8
        "Describe 'broken' {" | Set-Content -LiteralPath $broken -Encoding UTF8
        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner `
            -Path "$valid,$broken" -PowerShellOnly 2>&1 | Out-String -Width 4096

        $LASTEXITCODE | Should -Not -Be 0
        $output | Should -Match 'CONTAINER OR DISCOVERY FAILURE'
        $output | Should -Match 'SUITE FAILED'
        $output | Should -Not -Match 'SUITE GREEN'
    }

    It 'fails when Pester executes zero tests' {
        $emptyTest = Join-Path $TestDrive 'zero.tests.ps1'
        '# intentionally no Describe blocks' | Set-Content -LiteralPath $emptyTest -Encoding UTF8
        $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner -Path $emptyTest 2>&1 |
            Out-String -Width 4096

        $LASTEXITCODE | Should -Not -Be 0
        $output | Should -Not -Match 'SUITE GREEN'
    }

    It 'fails when Pester returns null without throwing' {
        $moduleBase = Join-Path $TestDrive 'fake-modules'
        $module = Join-Path $moduleBase 'Pester\5.99.0'
        New-Item -ItemType Directory -Path $module -Force | Out-Null
        @'
@{
    RootModule = 'Pester.psm1'
    ModuleVersion = '5.99.0'
    GUID = 'd844d3b7-947d-4fc9-a4a4-cc0c7efefaae'
    FunctionsToExport = @('New-PesterConfiguration', 'Invoke-Pester')
}
'@ | Set-Content -LiteralPath (Join-Path $module 'Pester.psd1') -Encoding UTF8
        @'
function New-PesterConfiguration {
    [pscustomobject]@{
        Run = [pscustomobject]@{ Path = $null; PassThru = $false }
        Output = [pscustomobject]@{ Verbosity = 'None' }
    }
}
function Invoke-Pester { param($Configuration) return $null }
Export-ModuleMember -Function New-PesterConfiguration, Invoke-Pester
'@ | Set-Content -LiteralPath (Join-Path $module 'Pester.psm1') -Encoding UTF8
        $sample = Join-Path $TestDrive 'null-result.tests.ps1'
        "Describe 'unused' { It 'passes' { `$true | Should -Be `$true } }" |
            Set-Content -LiteralPath $sample -Encoding UTF8
        $oldModulePath = $env:PSModulePath
        try {
            $env:PSModulePath = "$moduleBase;$oldModulePath"
            $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner `
                -Path $sample -PowerShellOnly 2>&1 | Out-String -Width 4096
            $code = $LASTEXITCODE
        } finally {
            $env:PSModulePath = $oldModulePath
        }

        $code | Should -Not -Be 0
        $output | Should -Match 'PowerShell: PASSED=0 FAILED=1'
        $output | Should -Match 'SUITE FAILED'
        $output | Should -Not -Match 'SUITE GREEN'
    }

    It 'rejects incomplete or unsafe shard option combinations' {
        $missingIndexRecords = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner `
            -PowerShellOnly -ShardCount 3 2>&1
        $missingIndexCode = $LASTEXITCODE
        $missingIndex = ConvertTo-RunnerChildText -Records $missingIndexRecords
        $missingIndexCode | Should -Be 2
        $missingIndex | Should -Match 'must be supplied together'

        $mixedSuiteRecords = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner `
            -ShardIndex 1 -ShardCount 3 2>&1
        $mixedSuiteCode = $LASTEXITCODE
        $mixedSuite = ConvertTo-RunnerChildText -Records $mixedSuiteRecords
        $mixedSuiteCode | Should -Be 2
        $mixedSuite | Should -Match 'requires -PowerShellOnly'
    }

    It 'lists three deterministic shards whose union is complete and disjoint' {
        $repo = Split-Path -Parent $PSScriptRoot
        $expected = @(git -C $repo ls-files --cached --others --exclude-standard -- '*.tests.ps1' |
            Sort-Object -Unique | ForEach-Object { Join-Path $repo $_ })
        $listed = @()
        foreach ($index in 1..3) {
            $output = & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Runner `
                -PowerShellOnly -ShardIndex $index -ShardCount 3 -ListTargets 2>&1
            $LASTEXITCODE | Should -Be 0
            $selected = @($output | ForEach-Object {
                if ([string]$_ -match '^TARGET=(.+)$') { $Matches[1] }
            })
            $selected.Count | Should -BeGreaterThan 0
            $listed += $selected
        }

        $listed.Count | Should -Be $expected.Count
        @($listed | Sort-Object -Unique).Count | Should -Be $expected.Count
        @(Compare-Object ($expected | Sort-Object) ($listed | Sort-Object)).Count | Should -Be 0
    }
}
