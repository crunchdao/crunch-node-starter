#!/usr/bin/env bash
#
# verify_deployment.sh — Post-deploy verification for crunch-node
#
# Checks:
#   1. Docker containers healthy
#   2. All API endpoints return expected shapes
#   3. Docker logs free of errors
#   4. Data pipeline flowing (predictions, scores, snapshots exist)
#
# Usage:
#   ./scripts/verify_deployment.sh [API_URL] [UI_URL]
#
# Defaults:
#   API_URL=http://localhost:8000
#   UI_URL=http://localhost:3000

set -euo pipefail

API="${1:-http://localhost:8000}"
UI="${2:-http://localhost:3000}"
PASS=0
FAIL=0
WARN=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { ((PASS++)); echo -e "  ${GREEN}✓${NC} $1"; }
fail() { ((FAIL++)); echo -e "  ${RED}✗${NC} $1"; }
warn() { ((WARN++)); echo -e "  ${YELLOW}⚠${NC} $1"; }

check_endpoint() {
    local path="$1"
    local desc="$2"
    local expect_type="${3:-array}"  # array, object, any

    local resp
    local http_code
    http_code=$(curl -s -o /tmp/verify_resp.json -w "%{http_code}" "${API}${path}" 2>/dev/null || echo "000")

    if [[ "$http_code" == "000" ]]; then
        fail "$desc — connection refused"
        return
    fi
    if [[ "$http_code" != "200" ]]; then
        fail "$desc — HTTP $http_code"
        return
    fi

    if [[ "$expect_type" == "array" ]]; then
        if jq -e 'type == "array"' /tmp/verify_resp.json >/dev/null 2>&1; then
            local count
            count=$(jq 'length' /tmp/verify_resp.json)
            pass "$desc — $count items"
        else
            fail "$desc — expected array, got $(jq -r 'type' /tmp/verify_resp.json 2>/dev/null || echo 'invalid json')"
        fi
    elif [[ "$expect_type" == "object" ]]; then
        if jq -e 'type == "object"' /tmp/verify_resp.json >/dev/null 2>&1; then
            pass "$desc"
        else
            fail "$desc — expected object, got $(jq -r 'type' /tmp/verify_resp.json 2>/dev/null || echo 'invalid json')"
        fi
    else
        pass "$desc — HTTP 200"
    fi
}

check_endpoint_has_data() {
    local path="$1"
    local desc="$2"

    local http_code
    http_code=$(curl -s -o /tmp/verify_resp.json -w "%{http_code}" "${API}${path}" 2>/dev/null || echo "000")

    if [[ "$http_code" != "200" ]]; then
        fail "$desc — HTTP $http_code"
        return
    fi

    local count
    count=$(jq 'if type == "array" then length else 1 end' /tmp/verify_resp.json 2>/dev/null || echo "0")
    if [[ "$count" -gt 0 ]] && [[ "$count" != "null" ]]; then
        pass "$desc — $count items"
    else
        warn "$desc — empty (pipeline may not have produced data yet)"
    fi
}

# ── 1. Docker containers ──

echo ""
echo "═══ 1. Docker Containers ═══"

if ! command -v docker &>/dev/null; then
    warn "docker not found — skipping container checks"
else
    containers=(
        "init-db"
        "predict-worker"
        "score-worker"
        "report-worker"
        "model-orchestrator"
        "report-ui"
        "db"
    )
    for name in "${containers[@]}"; do
        # Match partial container name
        status=$(docker ps --filter "name=$name" --format '{{.Status}}' 2>/dev/null | head -1)
        if [[ -z "$status" ]]; then
            if [[ "$name" == "init-db" ]]; then
                # init-db exits after migration — check if it exited cleanly
                exit_code=$(docker ps -a --filter "name=$name" --format '{{.Status}}' 2>/dev/null | head -1)
                if [[ "$exit_code" == *"Exited (0)"* ]]; then
                    pass "$name — exited cleanly"
                else
                    warn "$name — not found or non-zero exit: $exit_code"
                fi
            else
                fail "$name — not running"
            fi
        elif [[ "$status" == *"Up"* ]]; then
            pass "$name — $status"
        else
            fail "$name — $status"
        fi
    done
fi

# ── 2. API Health ──

echo ""
echo "═══ 2. API Health ═══"

check_endpoint "/healthz" "healthz" "object"
check_endpoint "/info" "node info" "object"
check_endpoint "/openapi.json" "OpenAPI schema" "object"

# ── 3. Schema endpoints ──

echo ""
echo "═══ 3. Report Schema ═══"

check_endpoint "/reports/schema" "full schema" "object"
check_endpoint "/reports/schema/leaderboard-columns" "leaderboard columns" "array"
check_endpoint "/reports/schema/metrics-widgets" "metrics widgets" "array"

# Verify schema has MODEL column
http_code=$(curl -s -o /tmp/verify_resp.json -w "%{http_code}" "${API}/reports/schema/leaderboard-columns" 2>/dev/null)
if [[ "$http_code" == "200" ]]; then
    has_model=$(jq '[.[] | select(.type == "MODEL")] | length' /tmp/verify_resp.json 2>/dev/null || echo "0")
    if [[ "$has_model" -gt 0 ]]; then
        pass "schema has MODEL column"
    else
        fail "schema missing MODEL column"
    fi

    # List all columns
    echo "  Leaderboard columns:"
    jq -r '.[] | "    \(.property) (\(.type)) — \(.displayName)"' /tmp/verify_resp.json 2>/dev/null || true
fi

# ── 4. Data pipeline endpoints ──

echo ""
echo "═══ 4. Data Pipeline ═══"

check_endpoint "/reports/models" "models list" "array"
check_endpoint_has_data "/reports/leaderboard" "leaderboard"
check_endpoint_has_data "/reports/models/global" "models global scores"
check_endpoint "/reports/models/params" "models by params" "array"
check_endpoint "/reports/predictions" "predictions" "array"
check_endpoint "/reports/snapshots" "snapshots" "array"
check_endpoint "/reports/feeds" "feeds" "array"
check_endpoint "/reports/feeds/tail" "feeds tail" "array"
check_endpoint "/reports/checkpoints" "checkpoints" "array"

# ── 5. Secondary endpoints ──

echo ""
echo "═══ 5. Secondary Endpoints ═══"

check_endpoint "/reports/diversity" "diversity overview" "array"
check_endpoint "/reports/ensemble/history" "ensemble history" "array"
check_endpoint "/reports/checkpoints/rewards" "checkpoint rewards" "array"
check_endpoint "/reports/models/metrics" "models metrics timeseries" "array"
check_endpoint "/reports/models/summary" "models summary" "array"
check_endpoint "/reports/merkle/cycles" "merkle cycles" "array"

# ── 6. Data flow verification ──

echo ""
echo "═══ 6. Data Flow ═══"

# Check that predictions exist and have scores
http_code=$(curl -s -o /tmp/verify_resp.json -w "%{http_code}" "${API}/reports/predictions" 2>/dev/null)
if [[ "$http_code" == "200" ]]; then
    total=$(jq 'length' /tmp/verify_resp.json 2>/dev/null || echo "0")
    scored=$(jq '[.[] | select(.score_value != null)] | length' /tmp/verify_resp.json 2>/dev/null || echo "0")
    failed=$(jq '[.[] | select(.score_failed == true)] | length' /tmp/verify_resp.json 2>/dev/null || echo "0")

    if [[ "$total" -gt 0 ]]; then
        pass "predictions: $total total, $scored scored, $failed failed"
        if [[ "$scored" -eq 0 ]] && [[ "$total" -gt 0 ]]; then
            warn "no predictions have been scored yet — score worker may not have run"
        fi
        if [[ "$failed" -gt 0 ]]; then
            warn "$failed predictions failed scoring"
        fi
    else
        warn "no predictions yet — predict worker may not have run"
    fi
fi

# Check leaderboard has ranking values
http_code=$(curl -s -o /tmp/verify_resp.json -w "%{http_code}" "${API}/reports/leaderboard" 2>/dev/null)
if [[ "$http_code" == "200" ]]; then
    count=$(jq 'length' /tmp/verify_resp.json 2>/dev/null || echo "0")
    if [[ "$count" -gt 0 ]]; then
        # Check that ranking values aren't all zero (the old bug)
        all_zero=$(jq '[.[].score_ranking.value // 0] | all(. == 0)' /tmp/verify_resp.json 2>/dev/null || echo "true")
        if [[ "$all_zero" == "true" ]] && [[ "$count" -gt 0 ]]; then
            warn "all leaderboard ranking values are 0.0 — check Aggregation.value_field matches a score field"
        else
            pass "leaderboard ranking values are non-zero"
        fi

        echo "  Top entries:"
        jq -r '.[:5][] | "    #\(.rank) \(.model_id) — ranking: \(.score_ranking.value // "null")"' /tmp/verify_resp.json 2>/dev/null || true
    fi
fi

# ── 7. Docker logs check ──

echo ""
echo "═══ 7. Docker Logs (errors) ═══"

if command -v docker &>/dev/null; then
    workers=("score-worker" "predict-worker" "report-worker")
    for worker in "${workers[@]}"; do
        container=$(docker ps --filter "name=$worker" --format '{{.Names}}' 2>/dev/null | head -1)
        if [[ -z "$container" ]]; then
            continue
        fi
        error_count=$(docker logs "$container" --tail 200 2>&1 | grep -ci "error\|exception\|traceback" || true)
        if [[ "$error_count" -gt 0 ]]; then
            warn "$worker — $error_count error lines in recent logs"
            echo "    Recent errors:"
            docker logs "$container" --tail 200 2>&1 | grep -i "error\|exception\|traceback" | tail -3 | sed 's/^/      /'
        else
            pass "$worker — no errors in recent logs"
        fi
    done
else
    warn "docker not found — skipping log checks"
fi

# ── 8. UI check ──

echo ""
echo "═══ 8. UI Reachability ═══"

ui_code=$(curl -s -o /dev/null -w "%{http_code}" "$UI" 2>/dev/null || echo "000")
if [[ "$ui_code" == "200" ]]; then
    pass "UI reachable at $UI"
else
    fail "UI not reachable at $UI — HTTP $ui_code"
fi

# ── Summary ──

echo ""
echo "════════════════════════════════"
echo -e "  ${GREEN}PASS: $PASS${NC}  ${RED}FAIL: $FAIL${NC}  ${YELLOW}WARN: $WARN${NC}"
echo "════════════════════════════════"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
