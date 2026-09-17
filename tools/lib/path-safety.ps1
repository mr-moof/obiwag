<# Shared lexical-containment and Windows reparse-point checks for workflow tools. #>

function ConvertTo-NormalPath {
    param([Parameter(Mandatory)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-NormalPathContained {
    param(
        [Parameter(Mandatory)][string]$Candidate,
        [Parameter(Mandatory)][string]$Root
    )
    $rootPath = ConvertTo-NormalPath -Path $Root
    $candidatePath = ConvertTo-NormalPath -Path $Candidate
    $prefix = $rootPath + [System.IO.Path]::DirectorySeparatorChar
    return $candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ReparsePointInPath {
    <#
    .SYNOPSIS Return the first existing reparse-point component between Root and Path.
    .DESCRIPTION Resolve-Path.ProviderPath preserves a junction's lexical name on Windows, so it
    cannot prove physical containment. Walk every existing component instead. Path may itself be
    absent (for example, a not-yet-created checkpoint or archive directory); existing ancestors
    are still checked. Root is included and Path must be lexically contained by Root.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root
    )
    $rootPath = ConvertTo-NormalPath -Path $Root
    $current = ConvertTo-NormalPath -Path $Path
    if (-not (Test-NormalPathContained -Candidate $current -Root $rootPath)) {
        throw "path '$current' escapes root '$rootPath'"
    }

    while ($true) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $item.FullName
            }
        }
        if ($current.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName.TrimEnd('\', '/')
    }
    return $null
}
