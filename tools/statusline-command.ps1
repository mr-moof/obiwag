# Status line for Claude Code (Windows PowerShell 5.1 compatible)
# Color theme: gray, orange, blue, teal, green, lavender, rose, gold, slate, cyan
$COLOR = "orange"

# ANSI escape character (PS 5.1 compatible)
$ESC = [char]27

# ANSI color codes
$C_RESET = "$ESC[0m"
$C_GRAY = "$ESC[38;5;245m"
$C_BAR_EMPTY = "$ESC[38;5;238m"
$C_ACCENT = switch ($COLOR) {
    "orange"   { "$ESC[38;5;173m" }
    "blue"     { "$ESC[38;5;74m" }
    "teal"     { "$ESC[38;5;66m" }
    "green"    { "$ESC[38;5;71m" }
    "lavender" { "$ESC[38;5;139m" }
    "rose"     { "$ESC[38;5;132m" }
    "gold"     { "$ESC[38;5;136m" }
    "slate"    { "$ESC[38;5;60m" }
    "cyan"     { "$ESC[38;5;37m" }
    default    { $C_GRAY }
}

# Semantic palette from UX design spec
$C_GREEN = "$ESC[38;5;114m"    # Muted green - completed phases
$C_AMBER = "$ESC[38;5;179m"    # Amber - active phase
$C_DIM = "$ESC[38;5;240m"      # Dim gray - upcoming phases
$C_LAVENDER = "$ESC[38;5;139m" # Lavender - phase labels

# Read JSON input from stdin
$inputJson = $input | Out-String
try {
    $data = $inputJson | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-Output "${C_GRAY}Status line error: Invalid JSON${C_RESET}"
    exit 0
}

# Extract model, directory, and cwd
$model = if ($data.model.display_name) { $data.model.display_name } elseif ($data.model.id) { $data.model.id } else { "?" }
$cwd = $data.cwd
$dir = if ($cwd) { Split-Path -Leaf $cwd } else { "?" }

# Read Obi version (cached at script start)
# OBI_HOME = CB-trusted deploy root (C:\src\obi-tools by default).
# Fall back to the literal so statusline doesn't break the prompt on a fresh
# session before the User-scope env var is inherited.
$obiVersion = ""
$obiHome = if ($env:OBI_HOME) { $env:OBI_HOME } else { 'C:\src\obi-tools' }
$versionYaml = Join-Path $obiHome 'tools\version.yaml'
if (Test-Path $versionYaml) {
    $vContent = Get-Content $versionYaml -Raw
    if ($vContent -match 'version:\s*"([^"]+)"') { $obiVersion = "v$($Matches[1])" }
}

# Detect instruction file deviations (only show when something is unusual)
# Baseline expectation: global CLAUDE.md + 1 project CLAUDE.md + MEMORY.md all exist
$mdAlerts = @()

# Check global ~/.claude/CLAUDE.md
$hasGlobal = Test-Path (Join-Path $HOME ".claude\CLAUDE.md")
if (-not $hasGlobal) { $mdAlerts += "!global" }

# Check project CLAUDE.md files (walk cwd up to home)
$projectMdNames = @()
if ($cwd -and (Test-Path $cwd)) {
    $checkDir = $cwd
    while ($checkDir -and $checkDir.Length -gt 3) {
        if (Test-Path (Join-Path $checkDir "CLAUDE.md")) {
            $projectMdNames += Split-Path -Leaf $checkDir
        }
        if ($checkDir -eq $HOME) { break }
        $parent = Split-Path $checkDir
        if ($parent -eq $checkDir) { break }
        $checkDir = $parent
    }
}
# 0 project MDs = unusual, 2+ = unusual (show which dirs)
if ($projectMdNames.Count -eq 0) {
    $mdAlerts += "!project"
} elseif ($projectMdNames.Count -gt 1) {
    $mdAlerts += ($projectMdNames -join "+")
}

# Check MEMORY.md - try project hash from cwd walking up
$hasMemory = $false
if ($cwd) {
    $projectsBase = Join-Path $HOME ".claude\projects"
    if (Test-Path $projectsBase) {
        $checkPath = $cwd
        while ($checkPath -and $checkPath.Length -gt 3 -and -not $hasMemory) {
            $hash = ($checkPath -replace ':', '-') -replace '[\\/]', '-'
            $memFile = Join-Path $projectsBase (Join-Path $hash "memory\MEMORY.md")
            if (Test-Path $memFile) { $hasMemory = $true }
            $parent = Split-Path $checkPath
            if ($parent -eq $checkPath) { break }
            $checkPath = $parent
        }
    }
}
if (-not $hasMemory) { $mdAlerts += "!memory" }

# .claude/rules/ - always noteworthy since not default
if ($cwd) {
    $rulesDir = Join-Path $cwd ".claude\rules"
    if (Test-Path $rulesDir) {
        $ruleFiles = @(Get-ChildItem $rulesDir -Filter "*.md" -ErrorAction SilentlyContinue)
        if ($ruleFiles.Count -gt 0) { $mdAlerts += "$($ruleFiles.Count) rules" }
    }
}

$mdInfo = if ($mdAlerts.Count -gt 0) { $mdAlerts -join " " } else { "" }

# Extract session cost and lines changed
$costUsd = if ($data.cost.total_cost_usd) { $data.cost.total_cost_usd } else { 0 }
$linesAdded = if ($data.cost.total_lines_added) { [int]$data.cost.total_lines_added } else { 0 }
$linesRemoved = if ($data.cost.total_lines_removed) { [int]$data.cost.total_lines_removed } else { 0 }

# Format cost (literal $ + formatted number)
$costStr = '$' + ('{0:N2}' -f [double]$costUsd)

# Format lines changed
$linesStr = "+${linesAdded}/-${linesRemoved}"

# Get transcript path for context calculation
$transcriptPath = $data.transcript_path
$maxContext = if ($data.context_window.context_window_size) { $data.context_window.context_window_size } else { 200000 }
$maxK = [int]($maxContext / 1000)

# Calculate context bar
$barWidth = 10
$baseline = 20000
$pct = 0
$pctPrefix = "~"
$contextLength = 0

if ($transcriptPath -and (Test-Path $transcriptPath)) {
    try {
        $transcriptContent = Get-Content $transcriptPath -Raw -ErrorAction Stop
        # Parse JSON Lines format (each line is a separate JSON object)
        $lines = $transcriptContent -split "`n" | Where-Object { $_.Trim() }

        # Find the last line with usage info
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            try {
                $entry = $lines[$i] | ConvertFrom-Json -ErrorAction Stop
                if ($entry.message.usage -and -not $entry.isSidechain -and -not $entry.isApiErrorMessage) {
                    $usage = $entry.message.usage
                    $inputTokens = if ($usage.input_tokens) { $usage.input_tokens } else { 0 }
                    $cacheRead = if ($usage.cache_read_input_tokens) { $usage.cache_read_input_tokens } else { 0 }
                    $cacheCreate = if ($usage.cache_creation_input_tokens) { $usage.cache_creation_input_tokens } else { 0 }
                    $contextLength = $inputTokens + $cacheRead + $cacheCreate
                    break
                }
            } catch {
                continue
            }
        }
    } catch {
        # Transcript parsing failed, use baseline
    }
}

if ($contextLength -gt 0) {
    $pct = [int]($contextLength * 100 / $maxContext)
    $pctPrefix = ""
} else {
    $pct = [int]($baseline * 100 / $maxContext)
}
if ($pct -gt 100) { $pct = 100 }

# Build progress bar
$bar = ""
for ($i = 0; $i -lt $barWidth; $i++) {
    $barStart = $i * 10
    $progress = $pct - $barStart
    if ($progress -ge 8) {
        $bar += "$C_ACCENT$([char]0x2588)$C_RESET"  # Full block
    } elseif ($progress -ge 3) {
        $bar += "$C_ACCENT$([char]0x2584)$C_RESET"  # Lower half block
    } else {
        $bar += "$C_BAR_EMPTY$([char]0x2591)$C_RESET"  # Light shade
    }
}

$ctx = "$bar ${C_GRAY}${pctPrefix}${pct}% of ${maxK}k"

# Detect Obi workflow phase from transcript
# Scans for phase completion signals to determine current workflow position
$phaseInfo = ""
if ($transcriptPath -and (Test-Path $transcriptPath)) {
    try {
        # Phase completion signals in order (map signal -> phase number completed)
        # We search from the end of transcript for the most recent signal
        $phaseSignals = @{
            'LEARNING CAPTURED'       = 10
            'RELEASE GATE PASSED'     = 9
            'RELEASE GATE FAILED'     = 9
            'README REVIEW COMPLETE'  = 8
            'README COMPLETE'         = 7
            'README SKIPPED'          = 7
            'RE-REVIEW COMPLETE'      = 6
            'INTEGRATE COMPLETE'      = 5
            'REVIEW COMPLETE'         = 4
            'SIMPLIFY COMPLETE'       = 3
            'SIMPLIFY SKIPPED'        = 3
            'AUTHOR COMPLETE'         = 2
            'DISCOVERY COMPLETE'      = 1
        }

        $lastCompletedPhase = 0
        $inWorkflow = $false

        # Read transcript lines (from end, looking for signals)
        if (-not $lines) {
            $transcriptContent = Get-Content $transcriptPath -Raw -ErrorAction Stop
            $lines = $transcriptContent -split "`n" | Where-Object { $_.Trim() }
        }

        # Search last 50 entries for phase signals (performance bound)
        $searchStart = [Math]::Max(0, $lines.Count - 50)
        for ($i = $lines.Count - 1; $i -ge $searchStart; $i--) {
            $line = $lines[$i]
            # Quick text check before expensive JSON parse
            if ($line -match 'COMPLETE|CAPTURED|PASSED|FAILED|SKIPPED') {
                try {
                    $entry = $line | ConvertFrom-Json -ErrorAction Stop
                    if ($entry.message.role -eq 'assistant' -and $entry.message.content) {
                        $content = if ($entry.message.content -is [string]) {
                            $entry.message.content
                        } elseif ($entry.message.content -is [array]) {
                            ($entry.message.content | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.text }) -join ' '
                        } else { '' }

                        foreach ($signal in $phaseSignals.Keys) {
                            if ($content -match [regex]::Escape($signal)) {
                                $phaseNum = $phaseSignals[$signal]
                                if ($phaseNum -gt $lastCompletedPhase) {
                                    $lastCompletedPhase = $phaseNum
                                    $inWorkflow = $true
                                }
                            }
                        }
                    }
                } catch {
                    continue
                }
            }

            # Also detect workflow entry (obi-auto or /obi invocation)
            if ($line -match 'obi-auto|Obi Wag.*autonomous|Obi Wag.*manual') {
                $inWorkflow = $true
            }
        }

        if ($inWorkflow -and $lastCompletedPhase -lt 10) {
            $currentPhase = $lastCompletedPhase + 1
            $indicator = ""
            for ($p = 0; $p -lt 10; $p++) {
                $phaseNum = $p + 1
                if ($phaseNum -lt $currentPhase) {
                    # Completed
                    $indicator += "${C_GREEN}$([char]0x25CF)${C_RESET}"
                } elseif ($phaseNum -eq $currentPhase) {
                    # Active
                    $indicator += "${C_AMBER}$([char]0x25D0)${C_RESET}"
                } else {
                    # Upcoming
                    $indicator += "${C_DIM}$([char]0x25CB)${C_RESET}"
                }
            }
            $phaseInfo = " ${C_LAVENDER}${currentPhase}/10${C_RESET} $indicator"
        } elseif ($inWorkflow -and $lastCompletedPhase -ge 10) {
            $phaseInfo = " ${C_GREEN}$([char]0x2713)done${C_RESET}"
        }
    } catch {
        # Phase detection failed silently
    }
}

# OPT-22: Stall marker warning
$C_STALL = "$ESC[38;5;167m"  # red - same accent as high-context bar
$stallWarning = ""
if ($cwd) {
    $stallDir = Join-Path $cwd '.obi' 'state'
    if (Test-Path $stallDir) {
        $stallMarker = Get-ChildItem $stallDir -Filter 'stall-*.marker' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($stallMarker) {
            try {
                $stallData = Get-Content $stallMarker.FullName -Raw | ConvertFrom-Json
                $stallPhase = if ($stallData.phase) { $stallData.phase } else { "?" }
            } catch {
                $stallPhase = "?"
            }
            $stallWarning = " | ${C_STALL}!! P${stallPhase} stalled${C_GRAY}"
        }
    }
}

# Build output: Model | Dir | [Phase] | [MDs if unusual] | Cost +/-Lines | Context
$output = "${C_ACCENT}${model}${C_GRAY} | ${dir}"
if ($obiVersion) { $output += " | ${C_ACCENT}${obiVersion}${C_GRAY}" }
if ($phaseInfo) { $output += " |$phaseInfo" }
if ($mdInfo) { $output += " | ${C_ACCENT}${mdInfo}${C_GRAY}" }
$output += " | ${costStr} ${linesStr}${stallWarning} | ${ctx}${C_RESET}"

Write-Output $output

# Get user's last message
if ($transcriptPath -and (Test-Path $transcriptPath)) {
    try {
        if (-not $lines) {
            $transcriptContent = Get-Content $transcriptPath -Raw -ErrorAction Stop
            $lines = $transcriptContent -split "`n" | Where-Object { $_.Trim() }
        }

        # Calculate max length for truncation
        $plainOutput = "$model | ${dir}"
        if ($phaseInfo) { $plainOutput += " | XX/10 .........." }
        if ($mdInfo) { $plainOutput += " | ${mdInfo}" }
        $plainOutput += " | ${costStr} ${linesStr} | xxxxxxxxxx ${pct}% of ${maxK}k"
        $maxLen = $plainOutput.Length

        # Find last user message (search from end)
        $lastUserMsg = ""
        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
            try {
                $entry = $lines[$i] | ConvertFrom-Json -ErrorAction Stop
                if ($entry.type -eq "user") {
                    $content = $entry.message.content
                    $text = ""

                    if ($content -is [string]) {
                        $text = $content
                    } elseif ($content -is [array]) {
                        $textParts = $content | Where-Object { $_.type -eq "text" } | ForEach-Object { $_.text }
                        $text = $textParts -join " "
                    }

                    # Clean up and check if unhelpful
                    $text = $text -replace "`n", " " -replace "\s+", " "
                    if ($text -and -not ($text -match "^\[Request (interrupted|cancelled)") -and $text.Trim()) {
                        $lastUserMsg = $text.Trim()
                        break
                    }
                }
            } catch {
                continue
            }
        }

        if ($lastUserMsg) {
            if ($lastUserMsg.Length -gt $maxLen) {
                $lastUserMsg = $lastUserMsg.Substring(0, $maxLen - 3) + "..."
            }
            Write-Output "$([char]0x1F4AC) $lastUserMsg"
        }
    } catch {
        # Transcript parsing failed, skip last message
    }
}
