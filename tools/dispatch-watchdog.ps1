<#
.SYNOPSIS
    Dispatch stall watchdog (OPT-22). Polls heartbeat file; alerts on staleness.

.DESCRIPTION
    Launched by the orchestrator via Bash run_in_background before each Agent
    dispatch call.  Polls .obi/state/heartbeat-<RunId>.json; when the file's
    mtime stops advancing for longer than ThresholdMinutes, writes a stall
    marker and beeps.

    Self-terminates on:
      1. Heartbeat resumes (mtime advances after a stall alert)
      2. Phase-completion marker appears (.obi/state/phase-<Phase>-complete.marker)
      3. Hard ceiling elapsed (default 30 min, configurable for tests)

.PARAMETER RunId
    The autonomous-run id (from .obi/state/run-id.txt).

.PARAMETER Phase
    The phase number being dispatched (1-10).

.PARAMETER ThresholdMinutes
    Minutes of heartbeat silence before declaring a stall. Default 5.

.PARAMETER CeilingMinutes
    Hard self-termination ceiling in minutes. Default 30.

.PARAMETER PollSeconds
    Polling interval in seconds. Default 20.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RunId,

    [Parameter(Mandatory)]
    [int]$Phase,

    [double]$ThresholdMinutes = 5,

    [double]$CeilingMinutes = 30,

    [int]$PollSeconds = 20
)

$ErrorActionPreference = 'Stop'

$stateDir = Join-Path '.obi' 'state'
$heartbeatPath = Join-Path $stateDir "heartbeat-${RunId}.json"
$completionPath = Join-Path $stateDir "phase-${Phase}-complete.marker"
$stallMarkerPath = Join-Path $stateDir "stall-${Phase}.marker"

$startTime = [datetime]::UtcNow
$thresholdSpan = [timespan]::FromMinutes($ThresholdMinutes)
$ceilingSpan = [timespan]::FromMinutes($CeilingMinutes)
$stallAlerted = $false

while ($true) {
    # --- Exit condition: hard ceiling ---
    if (([datetime]::UtcNow - $startTime) -ge $ceilingSpan) {
        break
    }

    # --- Exit condition: phase-completion marker appeared ---
    if (Test-Path $completionPath) {
        # Clean up stall marker if we wrote one
        if ((Test-Path $stallMarkerPath) -and $stallAlerted) {
            Remove-Item $stallMarkerPath -Force -ErrorAction SilentlyContinue
        }
        break
    }

    # --- Check heartbeat staleness ---
    if (Test-Path $heartbeatPath) {
        $lastWrite = (Get-Item $heartbeatPath).LastWriteTimeUtc
        $staleness = [datetime]::UtcNow - $lastWrite

        if ($staleness -ge $thresholdSpan) {
            if (-not $stallAlerted) {
                # Read last heartbeat content for the marker
                $lastHb = $null
                try {
                    $lastHb = Get-Content $heartbeatPath -Raw | ConvertFrom-Json
                } catch {
                    # Malformed heartbeat - still write the marker
                }

                $markerData = @{
                    phase          = $Phase
                    detected_at    = [datetime]::UtcNow.ToString('o')
                    last_heartbeat = if ($lastHb.ts) { $lastHb.ts } else { $lastWrite.ToString('o') }
                }
                $markerData | ConvertTo-Json | Set-Content -Path $stallMarkerPath -Encoding UTF8

                # Primary alert: console beep (guaranteed to work)
                [Console]::Beep()

                # Best-effort: Windows toast notification (some security software may block)
                try {
                    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
                    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
                        [Windows.UI.Notifications.ToastTemplateType]::ToastText02
                    )
                    $textNodes = $template.GetElementsByTagName('text')
                    $textNodes.Item(0).AppendChild($template.CreateTextNode('Obi Wag: Dispatch Stall')) | Out-Null
                    $textNodes.Item(1).AppendChild($template.CreateTextNode("Phase $Phase stalled. Ctrl-C + resume.")) | Out-Null
                    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Obi Wag')
                    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
                    $notifier.Show($toast)
                } catch {
                    # Toast failed (some security software, missing API, etc.) - beep already fired
                }

                $stallAlerted = $true
            }
        } else {
            # Heartbeat is fresh
            if ($stallAlerted) {
                # --- Exit condition: heartbeat resumed after stall ---
                # Clean up the stall marker
                Remove-Item $stallMarkerPath -Force -ErrorAction SilentlyContinue
                break
            }
        }
    }
    # If heartbeat file does not exist yet, keep waiting (subagent may not
    # have fired its first tool call yet).

    Start-Sleep -Seconds $PollSeconds
}
