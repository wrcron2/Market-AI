#!/usr/bin/env bash
# E2E test for /api/llm-provider toggle endpoint.
# Requires the backend to be running (docker-compose up or local).
# Usage: ./scripts/test_llm_provider_e2e.sh [http://localhost:8080]

set -euo pipefail

BASE="${1:-http://localhost:8080}"
PASS=0
FAIL=0

green() { echo -e "\033[32m  PASS\033[0m $1"; ((PASS++)) || true; }
red()   { echo -e "\033[31m  FAIL\033[0m $1"; ((FAIL++)) || true; }

assert_eq() {
  local desc="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then green "$desc"; else red "$desc  (got='$got' want='$want')"; fi
}

echo "==> LLM Provider Toggle E2E tests against $BASE"
echo ""

# ── 1. Health check ────────────────────────────────────────────────────────────
echo "── Connectivity ──"
status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/healthz")
assert_eq "GET /healthz returns 200"  "$status" "200"
echo ""

# ── 2. GET returns a valid provider ───────────────────────────────────────────
echo "── GET /api/llm-provider ──"
body=$(curl -s "$BASE/api/llm-provider")
provider=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('provider','MISSING'))")
if [ "$provider" = "aws" ] || [ "$provider" = "local" ]; then
  green "GET returns valid provider ('$provider')"
else
  red   "GET returned unexpected provider: '$provider'"
fi
echo ""

# ── 3. SET local ───────────────────────────────────────────────────────────────
echo "── POST → local ──"
body=$(curl -s -X POST "$BASE/api/llm-provider" \
  -H "Content-Type: application/json" \
  -d '{"provider":"local"}')
got=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('provider',''))")
assert_eq "POST {provider:local} returns local"  "$got" "local"

# Read back
got=$(curl -s "$BASE/api/llm-provider" | python3 -c "import sys,json; print(json.load(sys.stdin).get('provider',''))")
assert_eq "GET after set-local returns local"    "$got" "local"
echo ""

# ── 4. SET aws ─────────────────────────────────────────────────────────────────
echo "── POST → aws ──"
body=$(curl -s -X POST "$BASE/api/llm-provider" \
  -H "Content-Type: application/json" \
  -d '{"provider":"aws"}')
got=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('provider',''))")
assert_eq "POST {provider:aws} returns aws"  "$got" "aws"

got=$(curl -s "$BASE/api/llm-provider" | python3 -c "import sys,json; print(json.load(sys.stdin).get('provider',''))")
assert_eq "GET after set-aws returns aws"    "$got" "aws"
echo ""

# ── 5. Invalid provider rejected ──────────────────────────────────────────────
echo "── Validation ──"
http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/llm-provider" \
  -H "Content-Type: application/json" \
  -d '{"provider":"gcp"}')
assert_eq "POST {provider:gcp} returns 400"  "$http_code" "400"

http_code=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$BASE/api/llm-provider")
assert_eq "GET still allowed after bad POST"  "$http_code" "200"
echo ""

# ── 6. Toggle cycle ────────────────────────────────────────────────────────────
echo "── Toggle cycle ──"
for expected in local aws local aws local; do
  curl -s -X POST "$BASE/api/llm-provider" \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"$expected\"}" > /dev/null
  got=$(curl -s "$BASE/api/llm-provider" | python3 -c "import sys,json; print(json.load(sys.stdin).get('provider',''))")
  assert_eq "Cycle → $expected" "$got" "$expected"
done
echo ""

# ── 7. Reset to local (saves money) ───────────────────────────────────────────
curl -s -X POST "$BASE/api/llm-provider" \
  -H "Content-Type: application/json" \
  -d '{"provider":"local"}' > /dev/null
echo "-- Reset to local (AWS billing protection)"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "==> ALL TESTS PASSED" && exit 0 || echo "==> TESTS FAILED" && exit 1
