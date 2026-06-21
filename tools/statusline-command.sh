#!/bin/bash

# Color codes
C_RESET='\033[0m'
C_GRAY='\033[38;5;245m'  # explicit gray for default text
C_BAR_EMPTY='\033[38;5;238m'
C_ACCENT='\033[38;5;173m'  # orange (used for model name and non-context elements)

input=$(cat)

# Single jq read of all needed stdin fields. Newline-separated + mapfile
# handles empty fields cleanly (TSV + `read` mis-aligns on consecutive tabs
# under git-bash).
mapfile -t _fields < <(echo "$input" | jq -r '
    .model.display_name // .model.id // "?",
    .cwd // "",
    .transcript_path // "",
    .cost.total_cost_usd // 0,
    .cost.total_lines_added // 0,
    .cost.total_lines_removed // 0,
    .context_window.context_window_size // 200000
' | tr -d '\r')
model="${_fields[0]}"
cwd="${_fields[1]}"
transcript_path="${_fields[2]}"
cost_usd="${_fields[3]:-0}"
lines_added="${_fields[4]:-0}"
lines_removed="${_fields[5]:-0}"
max_context="${_fields[6]:-200000}"
dir=$(basename "$cwd" 2>/dev/null || echo "?")

# Detect instruction file deviations (only show when something is unusual)
# Baseline expectation: global CLAUDE.md + 1 project CLAUDE.md + MEMORY.md all exist
md_alerts=()

# Check global ~/.claude/CLAUDE.md
[[ ! -f "$HOME/.claude/CLAUDE.md" ]] && md_alerts+=("!global")

# Check project CLAUDE.md files (walk cwd up to home)
project_md_names=()
if [[ -n "$cwd" && -d "$cwd" ]]; then
    check_dir="$cwd"
    while [[ -n "$check_dir" && ${#check_dir} -gt 3 ]]; do
        if [[ -f "$check_dir/CLAUDE.md" ]]; then
            project_md_names+=("$(basename "$check_dir")")
        fi
        [[ "$check_dir" == "$HOME" ]] && break
        parent=$(dirname "$check_dir")
        [[ "$parent" == "$check_dir" ]] && break
        check_dir="$parent"
    done
fi
# 0 project MDs = unusual, 2+ = unusual (show which dirs)
if [[ ${#project_md_names[@]} -eq 0 ]]; then
    md_alerts+=("!project")
elif [[ ${#project_md_names[@]} -gt 1 ]]; then
    md_alerts+=("$(IFS='+'; echo "${project_md_names[*]}")")
fi

# Check MEMORY.md - try project hash from cwd walking up
has_memory=false
if [[ -n "$cwd" && -d "$HOME/.claude/projects" ]]; then
    check_path="$cwd"
    while [[ -n "$check_path" && ${#check_path} -gt 3 ]]; do
        hash=$(echo "$check_path" | sed 's/:/-/g; s/[\\\/]/-/g')
        if [[ -f "$HOME/.claude/projects/$hash/memory/MEMORY.md" ]]; then
            has_memory=true
            break
        fi
        parent=$(dirname "$check_path")
        [[ "$parent" == "$check_path" ]] && break
        check_path="$parent"
    done
fi
[[ "$has_memory" == false ]] && md_alerts+=("!memory")

# .claude/rules/ - always noteworthy since not default
if [[ -n "$cwd" && -d "$cwd/.claude/rules" ]]; then
    rule_count=$(find "$cwd/.claude/rules" -maxdepth 1 -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
    [[ "$rule_count" -gt 0 ]] && md_alerts+=("${rule_count} rules")
fi

# Join alerts with space (empty string if no deviations)
md_info=""
if [[ ${#md_alerts[@]} -gt 0 ]]; then
    md_info=$(IFS=' '; echo "${md_alerts[*]}")
fi

# cost_usd / lines_added / lines_removed / transcript_path / max_context were
# extracted in the consolidated jq call near the top of the script.
# Reference: github.com/anthropics/claude-code/issues/13652
cost_str=$(printf '$%.2f' "$cost_usd")
lines_str="+${lines_added}/-${lines_removed}"
max_k=$((max_context / 1000))

# Calculate context percentage from transcript
if [[ -n "$transcript_path" && -f "$transcript_path" ]]; then
    context_length=$(jq -s '
        map(select(.message.usage and .isSidechain != true and .isApiErrorMessage != true)) |
        last |
        if . then
            (.message.usage.input_tokens // 0) +
            (.message.usage.cache_read_input_tokens // 0) +
            (.message.usage.cache_creation_input_tokens // 0)
        else 0 end
    ' < "$transcript_path")

    baseline=20000
    if [[ "$context_length" -gt 0 ]]; then
        pct=$((context_length * 100 / max_context))
        pct_prefix=""
    else
        pct=$((baseline * 100 / max_context))
        pct_prefix="~"
    fi
else
    pct=$((20000 * 100 / max_context))
    pct_prefix="~"
fi
[[ $pct -gt 100 ]] && pct=100

# Dynamic color: green < 50%, orange 50-75%, red > 75%
if [[ $pct -ge 75 ]]; then
    C_BAR='\033[38;5;167m'   # red
elif [[ $pct -ge 50 ]]; then
    C_BAR='\033[38;5;173m'   # orange
else
    C_BAR='\033[38;5;71m'    # green
fi

# === Session burn / corrections / Obi mood ============================
# Plan: ~/.claude/plans/i-don-t-need-the-radiant-sunrise.md
# Threshold-stack cascade for mood (first-trip wins): sleepy → wary →
# alert → happy. Designed to be explainable per Stacey-Barr / FHWA RAG.

# Combine "now epoch" + "today date" in one date fork (~170 ms saved per
# render on Windows git-bash where each fork is ~170 ms).
IFS='|' read -r now_ts today_str < <(date '+%s|%F' | tr -d '\r')

# Session-start timestamp: short-circuit jq with first(inputs | ...) — reads
# the transcript stream and exits at the first .timestamp it finds.  Falls
# back to file mtime when the transcript has no parseable timestamp yet.
start_iso=""
start_ts=""
if [[ -n "$transcript_path" && -f "$transcript_path" ]]; then
    start_iso=$(jq -rn 'first(inputs | select(.timestamp) | .timestamp) // empty' \
        < "$transcript_path" 2>/dev/null | tr -d '\r')
    if [[ -n "$start_iso" ]]; then
        start_ts=$(date -d "$start_iso" +%s 2>/dev/null)
    fi
    if [[ -z "$start_ts" ]]; then
        start_ts=$(stat -c %Y "$transcript_path" 2>/dev/null)
    fi
fi
elapsed_sec=$(( now_ts - ${start_ts:-$now_ts} ))
[[ $elapsed_sec -lt 0 ]] && elapsed_sec=0
elapsed_min=$(( elapsed_sec / 60 ))

# Burn-rate display: suppress /h until elapsed >= 2 min (otherwise the
# extrapolation is noise — $0.05 in 30s reads as $6/h).
if (( elapsed_min >= 2 )); then
    rate=$(awk -v c="$cost_usd" -v m="$elapsed_min" 'BEGIN{printf "%.2f", c/(m/60)}')
    burn_str="${elapsed_min}m · \$${rate}/h"
else
    burn_str="${elapsed_min}m · ${cost_str}"
fi

# Corrections since session start.  The session_id field in corrections.jsonl
# is a hash of `datetime.now()` at stop-hook fire time (see hooks/stop.py:203),
# so it is NOT stable per Claude Code session — we cannot filter by it.
# Filter by timestamp >= start_ts instead.  GNU `date --file=-` parses each
# ISO string (naive -> local->UTC, Z-suffixed -> UTC), so cross-timezone compare
# is safe in epoch space.
corr_dir="$HOME/.claude/.obi/memory/corrections"
corr_count=0
if [[ -n "$start_ts" && -d "$corr_dir" ]]; then
    corr_files=()
    [[ -f "$corr_dir/$today_str.jsonl" ]] && corr_files+=("$corr_dir/$today_str.jsonl")
    if (( elapsed_sec > 14400 )); then
        yest_str=$(date -d "@$(( now_ts - 86400 ))" +%F 2>/dev/null)
        [[ -n "$yest_str" && -f "$corr_dir/$yest_str.jsonl" ]] && corr_files+=("$corr_dir/$yest_str.jsonl")
    fi
    if (( ${#corr_files[@]} > 0 )); then
        corr_count=$(awk -F'"' '
            /"timestamp"/ {
                for (i=1; i<=NF; i++) if ($i=="timestamp") { print $(i+2); break }
            }' "${corr_files[@]}" 2>/dev/null \
            | { date --file=- +%s 2>/dev/null || true; } \
            | awk -v start="$start_ts" '$0+0 >= start+0 {c++} END{print c+0}')
    fi
fi
corr_str=""
if (( corr_count > 0 )); then
    C_CORR='\033[38;5;167m'  # red, same as >75% bar
    corr_str=" | ${C_CORR}corr:${corr_count}${C_GRAY}"
fi

# Obi mood -- threshold-stack cascade.
if [[ -z "$start_ts" ]] || (( elapsed_min < 1 )); then
    mood="💤"
elif (( corr_count >= 20 )) || (( pct >= 75 )) || awk -v c="$cost_usd" 'BEGIN{exit !(c>=10)}'; then
    mood="🐺"
elif (( elapsed_min > 20 )) && awk -v c="$cost_usd" 'BEGIN{exit !(c>0.50)}'; then
    mood="🦮"
else
    mood="🐶"
fi
# === end ==============================================================

# === OPT-22: Stall marker warning ====================================
C_STALL='\033[38;5;167m'  # red — same accent as high-context bar
stall_warning=""
if [[ -n "$cwd" && -d "$cwd/.obi/state" ]]; then
    stall_marker=$(find "$cwd/.obi/state" -maxdepth 1 -name 'stall-*.marker' 2>/dev/null | head -1)
    if [[ -n "$stall_marker" && -f "$stall_marker" ]]; then
        stall_phase=$(jq -r '.phase // "?"' < "$stall_marker" 2>/dev/null)
        stall_warning=" | ${C_STALL}!! P${stall_phase} stalled${C_GRAY}"
    fi
fi
# === end stall ========================================================

# Build the text portion
text="${C_ACCENT}${model}${C_GRAY} | ${dir}"
[[ -n "$md_info" ]] && text+=" | ${C_ACCENT}${md_info}${C_GRAY}"
text+=" | ${cost_str} ${lines_str} | ${C_BAR}${pct_prefix}${pct}%${C_GRAY} "

# Fixed 20-char bar
bar_width=20

# How many chars are filled
filled=$((pct * bar_width / 100))
[[ $filled -gt $bar_width ]] && filled=$bar_width

# Build the bar: filled + empty
bar=""
for ((i=0; i<filled; i++)); do bar+="█"; done
for ((i=filled; i<bar_width; i++)); do bar+="░"; done

output="${text}${C_BAR}${bar}${C_RESET}"
output+="${C_GRAY} | ${burn_str}${corr_str}${stall_warning} | ${mood}${C_RESET}"
printf '%b\n' "$output"
