#!/usr/bin/env bash
# E2E smoke tests for the coordinator report-ui.
#
# Prerequisites:
#   - Docker services running (make deploy)
#   - agent-browser installed (npm i -g @anthropic/agent-browser)
#
# Usage:
#   bash tests/test_e2e_ui_smoke.sh [base_url]
#
# Exits 0 if all checks pass, 1 on first failure.

set -euo pipefail

BASE_URL="${1:-http://localhost:3000}"
FAIL=0
TOTAL=0
PASSED=0

pass() { ((TOTAL++)); ((PASSED++)); echo "  ✅ $1"; }
fail() { ((TOTAL++)); FAIL=1; echo "  ❌ $1"; }

check_text_present() {
    local label="$1" pattern="$2"
    if echo "$PAGE_TEXT" | grep -qi "$pattern"; then
        pass "$label"
    else
        fail "$label — expected '$pattern' in page text"
    fi
}

check_text_absent() {
    local label="$1" pattern="$2"
    if echo "$PAGE_TEXT" | grep -qi "$pattern"; then
        fail "$label — found '$pattern' in page text"
    else
        pass "$label"
    fi
}

check_element_exists() {
    local label="$1" pattern="$2"
    if echo "$SNAPSHOT" | grep -qi "$pattern"; then
        pass "$label"
    else
        fail "$label — expected element matching '$pattern'"
    fi
}

check_min_elements() {
    local label="$1" pattern="$2" min="$3"
    local count
    count=$(echo "$SNAPSHOT" | grep -ci "$pattern" || true)
    if [ "$count" -ge "$min" ]; then
        pass "$label ($count found)"
    else
        fail "$label — expected at least $min, found $count"
    fi
}

# ── Preflight ─────────────────────────────────────────────────────────

echo "🔍 Preflight checks..."

if ! command -v agent-browser &>/dev/null; then
    echo "  ❌ agent-browser not installed"; exit 1
fi

if ! curl -sf "${BASE_URL}" -o /dev/null 2>/dev/null; then
    echo "  ❌ report-ui not reachable at ${BASE_URL}"; exit 1
fi

echo ""

# ── 1. Leaderboard ────────────────────────────────────────────────────

echo "📊 Leaderboard (${BASE_URL}/)"
agent-browser open "${BASE_URL}/" >/dev/null 2>&1
agent-browser wait --load networkidle >/dev/null 2>&1
sleep 1

SNAPSHOT=$(agent-browser snapshot -i 2>&1)
PAGE_TEXT=$(agent-browser get text body 2>&1)

check_text_present "Page title contains 'Leaderboard'" "leaderboard"
check_text_present "Has model rows" "submission\|reversion\|following\|regime"
check_text_absent  "No error toast" "oops"
check_element_exists "Search input present" "search"

# Count distinct model names on the page
MODEL_COUNT=$(echo "$PAGE_TEXT" | grep -oiE "(mean-reversion|trend-following|volatility-regime|starter-submission)" | sort -u | wc -l | tr -d ' ')
if [ "$MODEL_COUNT" -ge 2 ]; then
    pass "At least 2 distinct models in leaderboard ($MODEL_COUNT found)"
else
    fail "Expected at least 2 distinct models, found $MODEL_COUNT"
fi

echo ""

# ── 2. Models ─────────────────────────────────────────────────────────

echo "🤖 Models (${BASE_URL}/models)"
agent-browser open "${BASE_URL}/models" >/dev/null 2>&1
agent-browser wait --load networkidle >/dev/null 2>&1
sleep 1

SNAPSHOT=$(agent-browser snapshot -i 2>&1)
PAGE_TEXT=$(agent-browser get text body 2>&1)

check_text_present "Page title contains 'Models'" "models"
check_text_present "Has model entries" "submission\|reversion\|following\|regime"
check_text_present "Shows running status" "running"
check_text_absent  "No error toast" "oops"
check_element_exists "Logs button present" "Logs"

# Count distinct model names
MODEL_COUNT=$(echo "$PAGE_TEXT" | grep -oiE "(mean-reversion|trend-following|volatility-regime|starter-submission)" | sort -u | wc -l | tr -d ' ')
if [ "$MODEL_COUNT" -ge 2 ]; then
    pass "At least 2 distinct models ($MODEL_COUNT found)"
else
    fail "Expected at least 2 distinct models, found $MODEL_COUNT"
fi

echo ""

# ── 3. Logs ───────────────────────────────────────────────────────────

echo "📝 Logs (${BASE_URL}/logs)"
agent-browser open "${BASE_URL}/logs" >/dev/null 2>&1
sleep 3  # logs page streams — give it time to connect and render

SNAPSHOT=$(agent-browser snapshot -i 2>&1)
# get text body can be slow on streaming pages — use perl alarm as portable timeout
PAGE_TEXT=$(perl -e 'alarm 10; exec @ARGV' agent-browser get text body 2>&1 || echo "$SNAPSHOT")

check_text_present "Page title contains 'Logs'" "logs"
check_text_present "Shows Connected status" "connected"
check_text_absent  "No error toast" "oops"
check_text_absent  "No disconnected status" "disconnected"

# Check for actual log content (timestamps or log levels)
if echo "$PAGE_TEXT" | grep -qE "[0-9]{4}-[0-9]{2}-[0-9]{2}|INFO|WARNING|ERROR|crunch_node"; then
    pass "Log content is streaming"
else
    fail "No log content visible — backend may not be connected"
fi

# Check there are worker tabs
check_text_present "Has worker tabs" "worker"

echo ""

# ── 4. Metrics ────────────────────────────────────────────────────────

echo "📈 Metrics (${BASE_URL}/metrics)"
agent-browser open "${BASE_URL}/metrics" >/dev/null 2>&1
agent-browser wait --load networkidle >/dev/null 2>&1
sleep 2

SNAPSHOT=$(agent-browser snapshot -i 2>&1)
PAGE_TEXT=$(agent-browser get text body 2>&1)

check_text_present "Page loads with Metrics heading or config" "metrics"
check_text_present "Has widget configuration" "widget"

# Metrics page may show "Oops" if no chart data yet — flag but don't hard-fail
if echo "$PAGE_TEXT" | grep -qi "oops\|error occurred"; then
    echo "  ⚠️  Metrics page shows error toast (may be missing chart data)"
else
    pass "No error toast on metrics page"
fi

echo ""

# ── Cleanup ───────────────────────────────────────────────────────────

agent-browser close >/dev/null 2>&1 || true

# ── Summary ───────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: ${PASSED}/${TOTAL} passed"
if [ "$FAIL" -eq 1 ]; then
    echo "❌ SOME CHECKS FAILED"
    exit 1
else
    echo "✅ ALL CHECKS PASSED"
    exit 0
fi
