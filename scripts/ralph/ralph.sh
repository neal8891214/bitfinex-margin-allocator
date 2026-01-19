#!/bin/bash
# Ralph - Long-running AI agent loop (Claude Code version)
# Usage: ./ralph.sh [max_iterations]

set -e

MAX_ITERATIONS=${1:-10}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRD_FILE="$SCRIPT_DIR/prd.json"
PROGRESS_FILE="$SCRIPT_DIR/progress.txt"
ARCHIVE_DIR="$SCRIPT_DIR/archive"
LAST_BRANCH_FILE="$SCRIPT_DIR/.last-branch"
LOG_FILE="$SCRIPT_DIR/ralph.log"

# 顯示進度摘要
show_progress() {
  if [ -f "$PRD_FILE" ]; then
    local total=$(jq '.userStories | length' "$PRD_FILE")
    local passed=$(jq '[.userStories[] | select(.passes == true)] | length' "$PRD_FILE")
    local remaining=$((total - passed))
    echo "Progress: $passed/$total stories complete ($remaining remaining)"
  fi
}

# 顯示下一個要處理的 story
show_next_story() {
  if [ -f "$PRD_FILE" ]; then
    local next=$(jq -r '[.userStories[] | select(.passes == false)] | sort_by(.priority) | .[0] | "\(.id): \(.title)"' "$PRD_FILE" 2>/dev/null)
    if [ -n "$next" ] && [ "$next" != "null: null" ]; then
      echo "Next story: $next"
    fi
  fi
}

# 解析 Claude 輸出中的狀態標記
show_iteration_summary() {
  local output="$1"
  echo ""
  echo "─────────────────────────────────────────────────────────"
  echo "  ITERATION SUMMARY"
  echo "─────────────────────────────────────────────────────────"

  if echo "$output" | grep -q "STORY_COMPLETED:"; then
    local completed=$(echo "$output" | grep "STORY_COMPLETED:" | tail -1 | sed 's/.*STORY_COMPLETED://')
    echo "  ✓ Completed:$completed"
  elif echo "$output" | grep -q "STORY_FAILED:"; then
    local failed=$(echo "$output" | grep "STORY_FAILED:" | tail -1 | sed 's/.*STORY_FAILED://')
    echo "  ✗ Failed:$failed"
  elif echo "$output" | grep -q "STORY_BLOCKED:"; then
    local blocked=$(echo "$output" | grep "STORY_BLOCKED:" | tail -1 | sed 's/.*STORY_BLOCKED://')
    echo "  ⚠ Blocked:$blocked"
  fi

  show_progress
}

# Archive previous run if branch changed
if [ -f "$PRD_FILE" ] && [ -f "$LAST_BRANCH_FILE" ]; then
  CURRENT_BRANCH=$(jq -r '.branchName // empty' "$PRD_FILE" 2>/dev/null || echo "")
  LAST_BRANCH=$(cat "$LAST_BRANCH_FILE" 2>/dev/null || echo "")
  
  if [ -n "$CURRENT_BRANCH" ] && [ -n "$LAST_BRANCH" ] && [ "$CURRENT_BRANCH" != "$LAST_BRANCH" ]; then
    # Archive the previous run
    DATE=$(date +%Y-%m-%d)
    # Strip "ralph/" prefix from branch name for folder
    FOLDER_NAME=$(echo "$LAST_BRANCH" | sed 's|^ralph/||')
    ARCHIVE_FOLDER="$ARCHIVE_DIR/$DATE-$FOLDER_NAME"
    
    echo "Archiving previous run: $LAST_BRANCH"
    mkdir -p "$ARCHIVE_FOLDER"
    [ -f "$PRD_FILE" ] && cp "$PRD_FILE" "$ARCHIVE_FOLDER/"
    [ -f "$PROGRESS_FILE" ] && cp "$PROGRESS_FILE" "$ARCHIVE_FOLDER/"
    echo "   Archived to: $ARCHIVE_FOLDER"
    
    # Reset progress file for new run
    echo "# Ralph Progress Log" > "$PROGRESS_FILE"
    echo "Started: $(date)" >> "$PROGRESS_FILE"
    echo "---" >> "$PROGRESS_FILE"
  fi
fi

# Track current branch
if [ -f "$PRD_FILE" ]; then
  CURRENT_BRANCH=$(jq -r '.branchName // empty' "$PRD_FILE" 2>/dev/null || echo "")
  if [ -n "$CURRENT_BRANCH" ]; then
    echo "$CURRENT_BRANCH" > "$LAST_BRANCH_FILE"
  fi
fi

# Initialize progress file if it doesn't exist
if [ ! -f "$PROGRESS_FILE" ]; then
  echo "# Ralph Progress Log" > "$PROGRESS_FILE"
  echo "Started: $(date)" >> "$PROGRESS_FILE"
  echo "---" >> "$PROGRESS_FILE"
fi

echo "Starting Ralph - Max iterations: $MAX_ITERATIONS"
echo "" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"
echo "Ralph Run: $(date)" >> "$LOG_FILE"
echo "Max Iterations: $MAX_ITERATIONS" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

for i in $(seq 1 $MAX_ITERATIONS); do
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  RALPH ITERATION $i / $MAX_ITERATIONS"
  echo "═══════════════════════════════════════════════════════════"
  show_progress
  show_next_story
  echo "─────────────────────────────────────────────────────────"

  # Run claude with the ralph prompt (log + display)
  OUTPUT=$(cat "$SCRIPT_DIR/prompt.md" | claude -p --dangerously-skip-permissions 2>&1 | tee -a "$LOG_FILE" | tee /dev/stderr) || true

  # Show iteration summary
  show_iteration_summary "$OUTPUT"

  # Check for completion signal
  if echo "$OUTPUT" | grep -q "<promise>COMPLETE</promise>"; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  RALPH COMPLETE!"
    echo "═══════════════════════════════════════════════════════════"
    show_progress
    echo "Completed at iteration $i of $MAX_ITERATIONS"
    exit 0
  fi

  echo ""
  echo "Continuing to next iteration..."
  sleep 2
done

echo ""
echo "Ralph reached max iterations ($MAX_ITERATIONS) without completing all tasks."
echo "Check $PROGRESS_FILE for status."
exit 1
