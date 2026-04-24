#!/bin/bash
# QA smoke test for Jake Benchmark dashboard
# Verifies the published site loads and data is valid.
#
# Usage: bash qa-dashboard.sh [URL]
# Exit 0 = all checks pass, exit 1 = failures found
set -euo pipefail

DASHBOARD_URL="${1:-https://frankhli843.github.io/jake-benchmark/}"
FAILURES=0
CHECKS=0

check() {
    local name="$1"
    shift
    CHECKS=$((CHECKS + 1))
    if "$@" >/dev/null 2>&1; then
        echo "  PASS: $name"
    else
        echo "  FAIL: $name"
        FAILURES=$((FAILURES + 1))
    fi
}

echo "=== Jake Benchmark Dashboard QA ==="
echo "URL: $DASHBOARD_URL"
echo "Date: $(date -Iseconds)"
echo ""

# 1. Fetch the page (with retry for transient failures)
echo "--- Fetching dashboard ---"
HTTP_CODE="000"
for attempt in 1 2 3; do
    HTTP_CODE=$(curl -s -o /tmp/jake-dashboard-qa.html -w "%{http_code}" --max-time 30 "$DASHBOARD_URL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        break
    fi
    if [ "$attempt" -lt 3 ]; then
        echo "  Retry $attempt (HTTP $HTTP_CODE)..."
        sleep 3
    fi
done

check "HTTP 200 response" test "$HTTP_CODE" = "200"
check "HTML content received" test -s /tmp/jake-dashboard-qa.html

if [ "$HTTP_CODE" != "200" ]; then
    echo ""
    echo "FATAL: Dashboard unreachable (HTTP $HTTP_CODE). Aborting."
    exit 1
fi

PAGE_SIZE=$(wc -c < /tmp/jake-dashboard-qa.html)
check "Page size > 10KB (got ${PAGE_SIZE}B)" test "$PAGE_SIZE" -gt 10000

# 2. Check page structure
echo ""
echo "--- Checking page structure ---"
check "Contains <title>" grep -qi '<title>' /tmp/jake-dashboard-qa.html
check "Contains model data" grep -qi 'scores\|leaderboard\|benchmark' /tmp/jake-dashboard-qa.html
check "Contains Chart.js or chart elements" grep -qi 'chart\|canvas' /tmp/jake-dashboard-qa.html

# "No 404" uses negated grep: pass if grep does NOT find it
not_found_check() {
    ! grep -qi 'page not found' /tmp/jake-dashboard-qa.html
}
check "No 'page not found' text" not_found_check

# 3. Check embedded data
echo ""
echo "--- Checking embedded data ---"
python3 -c "
import re, sys

html = open('/tmp/jake-dashboard-qa.html').read()

score_fractions = re.findall(r'\d+/508', html)
has_scores = len(score_fractions) > 0

has_models = bool(re.search(r'qwen|gemma|deepseek|llama|nemotron|glm|lfm', html, re.IGNORECASE))
has_tasks = bool(re.search(r'email_summarize|calendar_create|phishing_detect', html))

model_cards = len(re.findall(r'score-ring|model-card|leaderboard-entry', html))

print(f'scores_data={has_scores}')
print(f'score_count={len(score_fractions)}')
print(f'model_names={has_models}')
print(f'task_references={has_tasks}')
print(f'model_cards={model_cards}')

sys.exit(0 if (has_scores and has_models) else 1)
" > /tmp/jake-qa-data-check.txt 2>&1 || true

check "Has score data (N/508 format)" grep -q 'scores_data=True' /tmp/jake-qa-data-check.txt
check "Has model names" grep -q 'model_names=True' /tmp/jake-qa-data-check.txt
check "Has task references" grep -q 'task_references=True' /tmp/jake-qa-data-check.txt

# 4. Check nav links (relative paths should work)
echo ""
echo "--- Checking navigation ---"
python3 << 'NAVEOF' > /tmp/jake-qa-nav-check.txt 2>&1
import re
html = open("/tmp/jake-dashboard-qa.html").read()
hrefs = re.findall(r'href="([^"]+)"', html)
internal = [h for h in hrefs if not h.startswith("http") and not h.startswith("#") and not h.startswith("mailto")]
broken = [h for h in internal if ".." in h or h.startswith("/jake-benchmark/jake-benchmark")]
print(f"internal_links={len(internal)}")
print(f"broken_patterns={len(broken)}")
for b in broken[:5]:
    print(f"  broken: {b}")
NAVEOF

check "No broken nav link patterns" grep -q 'broken_patterns=0' /tmp/jake-qa-nav-check.txt

# 5. Check model run pages
echo ""
echo "--- Checking model run pages ---"
RUNS_URL="${DASHBOARD_URL}runs/"
for model_slug in "qwen3-5-27b-q4-k-m-medium" "gemma4-31b-high"; do
    RUN_URL="${RUNS_URL}${model_slug}/"
    RUN_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$RUN_URL" 2>/dev/null || echo "000")
    check "Model run page $model_slug accessible (HTTP $RUN_CODE)" test "$RUN_CODE" = "200" -o "$RUN_CODE" = "301" -o "$RUN_CODE" = "302"
done

# 6. Summary
echo ""
echo "=== Results ==="
echo "Checks: $CHECKS, Passed: $((CHECKS - FAILURES)), Failed: $FAILURES"

# Clean up
rm -f /tmp/jake-dashboard-qa.html /tmp/jake-qa-data-check.txt /tmp/jake-qa-nav-check.txt

if [ $FAILURES -gt 0 ]; then
    echo "STATUS: FAIL"
    exit 1
else
    echo "STATUS: PASS"
    exit 0
fi
