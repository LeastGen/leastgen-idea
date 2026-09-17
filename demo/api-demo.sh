#!/usr/bin/env bash
# =============================================================================
# leastgen-hosted — API Demo Script (curl + jq)
# =============================================================================
# Polished showcase of the token billing + caching API.
# Run against the leastgen-hosted server at http://localhost:8756.
#
# Usage:
#   ./api-demo.sh                  # Run all sections (live)
#   ./api-demo.sh --dry-run        # Print commands without executing
#   ./api-demo.sh --base-url URL   # Override default base URL
#   ./api-demo.sh --no-jq          # Skip jq pretty-printing
# =============================================================================

set -uo pipefail

# ── Configuration ──────────────────────────────────────────────────────
BASE_URL="${API_BASE_URL:-http://localhost:8756}"
DRY_RUN=false
USE_JQ=true
# NOTE: dummy demo-only credentials for local demo script — not real accounts
DEMO_EMAIL="${DEMO_EMAIL:-demo@leastgen.com}"
DEMO_PASS="${DEMO_PASS:-password123}"
DEMO_NAME="Demo User"
DEMO_TOKEN=""
DEMO_UID=""
# DEMO_ENV_FILE: project-root .env holding TAP_API_KEY for the §5 hashstring demo.
# The key is read only to compute the HMAC locally — it is never echoed or logged.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_ENV_FILE="${DEMO_ENV_FILE:-$SCRIPT_DIR/../.env}"

# ── Colors ─────────────────────────────────────────────────────────────
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'
MAGENTA=$'\033[0;35m'
CYAN=$'\033[0;36m'
WHITE=$'\033[0;97m'
DIM=$'\033[0;90m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────
section() {
  local num="$1" title="$2"
  printf '\n%s\n' "${BLUE}${BOLD}══════════════════════════════════════════════════════════════${RESET}"
  printf "${BLUE}${BOLD}  %s. %s${RESET}\n" "$num" "$title"
  printf "${BLUE}${BOLD}══════════════════════════════════════════════════════════════${RESET}\n"
}

subtitle() {
  printf "\n${CYAN}${BOLD}── $1 ──${RESET}\n"
}

cmd_label() {
  printf "${DIM}# $1${RESET}\n"
}

success() { printf "${GREEN}✓${RESET} $1\n"; }
warn()   { printf "${YELLOW}⚠${RESET} $1\n"; }
info()   { printf "${BLUE}ℹ${RESET} $1\n"; }

print_separator() {
  printf "${DIM}────────────────────────────────────────────────────────────────${RESET}\n"
}

pretty_json() {
  if [[ "$USE_JQ" == true ]] && command -v jq &>/dev/null; then
    jq . 2>/dev/null || cat
  else
    cat
  fi
}

# ── curl helper ─────────────────────────────────────────────────────────
# CACHE_AVAILABLE: 1 = server has /api/cache/* (leastgen-hosted), 0 = skip
CACHE_AVAILABLE=1
probe_cache() {
  if [[ "$DRY_RUN" == true ]]; then return 0; fi
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/cache/stats" 2>/dev/null) || code="000"
  if [[ "$code" =~ ^2 ]]; then CACHE_AVAILABLE=1; else CACHE_AVAILABLE=0; fi
}

skip_cache_note() {
  warn "SKIP — $BASE_URL has no /api/cache/* routes (think-fast has no cache router)"
}

# ensure_demo_user: idempotent login-or-signup; sets DEMO_TOKEN/DEMO_UID.
# Prints nothing on success. Returns 0 with token set, 1 without.
ensure_demo_user() {
  if [[ -n "$DEMO_TOKEN" ]]; then return 0; fi
  local auth_resp
  auth_resp=$(curl -s -X POST "$BASE_URL/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASS\"}" 2>/dev/null)
  DEMO_TOKEN=$(echo "$auth_resp" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
  if [[ -z "$DEMO_TOKEN" ]]; then
    auth_resp=$(curl -s -X POST "$BASE_URL/api/auth/signup" \
      -H "Content-Type: application/json" \
      -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASS\",\"name\":\"$DEMO_NAME\"}" 2>/dev/null)
    DEMO_TOKEN=$(echo "$auth_resp" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
  fi
  if [[ -n "$DEMO_TOKEN" ]]; then
    DEMO_UID=$(echo "$auth_resp" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
  fi
  [[ -n "$DEMO_TOKEN" ]]
}

do_curl() {
  local method="$1" url="$2"
  shift 2
  local headers=()
  local body=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -H|--header) headers+=("-H" "$2"); shift 2 ;;
      -d|--data)   body="$2"; shift 2 ;;
      *) shift ;;
    esac
  done

  if [[ "$DRY_RUN" == true ]]; then
    printf "${DIM}curl -s -X %s '%s'${RESET}\n" "$method" "$url"
    if [[ ${#headers[@]} -gt 0 ]]; then
      for ((i=0; i<${#headers[@]}; i+=2)); do
        printf "${DIM}  %s '%s'${RESET}\n" "${headers[i]}" "${headers[i+1]:-}"
      done
    fi
    [[ -n "$body" ]] && printf "${DIM}  -d '%s'${RESET}\n" "$body"
    return 0
  fi

  local cmd=(curl -s -w "\n%{http_code}" -X "$method")
  cmd+=("$url")
  if [[ ${#headers[@]} -gt 0 ]]; then
    for h in "${headers[@]}"; do cmd+=("$h"); done
  fi
  [[ -n "$body" ]] && cmd+=("-d" "$body")

  printf "${DIM}▸ %s %s${RESET}\n" "$method" "$url"
  local output
  output=$("${cmd[@]}" 2>/dev/null) || { printf "${RED}Request failed${RESET}\n"; return 1; }

  # Split body and HTTP code
  local http_code="${output: -3}"
  local body="${output%???}"

  if [[ "$http_code" =~ ^2 ]]; then
    success "HTTP $http_code"
  else
    warn "HTTP $http_code"
  fi

  if [[ -n "$body" ]]; then
    printf '%s\n' "$body" | pretty_json
  fi
  printf '\n'
}

# ── Banner ─────────────────────────────────────────────────────────────
print_banner() {
  cat <<'BANNER'

  ╔═══════════════════════════════════════════════════════════════╗
  ║                                                                     ║
  ║   ██████╗ ██████╗ ███████╗███╗   ███╗ ██████╗████████╗██╗  ██╗  ║
  ║   ██╔══██╗██╔══██╗██╔════╝████╗ ████║██╔════╝╚══██╔══╝██║ ██╔╝  ║
  ║   ██████╔╝██████╔╝█████╗  ██╔████╔██║██║      ██╔╝   █████╔╝   ║
  ║   ██╔═══╝ ██╔══██╗██╔══╝  ██║╚██╔╝██║██║      ██║    ██╔═██╗   ║
  ║   ██║     ██║  ██║███████╗██║ ╚═╝ ██║╚██████╗   ██║   ██║  ██╗  ║
  ║   ╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝ ╚═════╝   ╚═╝   ╚═╝  ╚═╝ ║
  ║                                                                     ║
  ║         leastgen-hosted — Token Billing + Caching API         ║
  ║                                                                     ║
  ╚═══════════════════════════════════════════════════════════════╝
BANNER
  printf "\n${WHITE}${BOLD}Base URL:${RESET}  ${GREEN}%s${RESET}\n" "$BASE_URL"
  printf "${WHITE}${BOLD}Tool:${RESET}       ${CYAN}curl + jq${RESET}\n"
  printf "${WHITE}${BOLD}Mode:${RESET}       %s\n" "$([ "$DRY_RUN" == true ] && echo 'DRY-RUN (commands only)' || echo 'LIVE (requests sent)')"
  printf "\n"
}

# ── 1. Billing Plans ──────────────────────────────────────────────────
section_1() {
  section "1" "Token Packs & Subscription Plans — GET /api/billing/plans"

  subtitle "Request"
  cmd_label "curl -s -X GET http://localhost:8756/api/billing/plans"
  do_curl GET "$BASE_URL/api/billing/plans"

  subtitle "What you get"
  info "Returns three token packs and three subscription tiers:"
  printf "  ${YELLOW}Token Packs:${RESET}\n"
  printf "    • 10M Token Pack  — \$49   (starter, no expiration)\n"
  printf "    • 50M Token Pack  — \$199  (best value for regular researchers)\n"
  printf "    • 200M Token Pack — \$699  (enterprise grade, team sharing)\n"
  printf "  ${CYAN}Subscriptions:${RESET}\n"
  printf "    • Free    — \$0/mo   (10 runs/month)\n"
  printf "    • Pro     — \$29/mo  (unlimited runs, 30 days)\n"
  printf "    • Pro Yr  — \$290/yr (unlimited runs, 365 days, 2 months free)\n"
  printf '\n'
}

# ── 2. Cache Stats ────────────────────────────────────────────────────
# leastgen-hosted only: think-fast has no cache router → section skips cleanly.
section_2() {
  section "2" "Cache Statistics — GET /api/cache/stats"

  if [[ "$DRY_RUN" == false && "$CACHE_AVAILABLE" == "0" ]]; then
    skip_cache_note
    printf '\n'
    return 0
  fi
  subtitle "Request"
  cmd_label "curl -s -X GET http://localhost:8756/api/cache/stats"
  do_curl GET "$BASE_URL/api/cache/stats"

  subtitle "What you get"
  info "Returns hit rates and entry counts across three cache tiers:"
  printf "  ${GREEN}semantic${RESET}  — Semantic similarity matching (vector embeddings)\n"
  printf "  ${GREEN}template${RESET}  — Template-based exact-match caching\n"
  printf "  ${GREEN}documents${RESET} — Document-level cache for full-text results\n"
  printf "  ${CYAN}tokens_saved${RESET} — Total tokens avoided via cache hits\n"
  printf '\n'
}

# ── 3. Cache Lookup ───────────────────────────────────────────────────
# leastgen-hosted only: think-fast has no cache router → section skips cleanly.
section_3() {
  section "3" "Semantic Cache Lookup — POST /api/cache/lookup"

  if [[ "$DRY_RUN" == false && "$CACHE_AVAILABLE" == "0" ]]; then
    skip_cache_note
    printf '\n'
    return 0
  fi
  subtitle "Request"
  cmd_label 'curl -s -X POST http://localhost:8756/api/cache/lookup \
    -H "Content-Type: application/json" \
    -d '"'"'{"query":"efficient speculative decoding for LLM"}'"'"''
  do_curl POST "$BASE_URL/api/cache/lookup" \
    -H "Content-Type: application/json" \
    -d '{"query":"efficient speculative decoding for LLM"}'

  subtitle "What you get"
  info "Returns a cached match if semantic similarity exceeds the threshold:"
  printf '  {"found":true,"source":"semantic_cache","similarity":0.94,"response":"...","phase":null}\n'
  printf '  (miss → {"found":false})\n'
  printf '\n'
}

# ── 4. Cache Store ────────────────────────────────────────────────────
# leastgen-hosted only: think-fast has no cache router → section skips cleanly.
section_4() {
  section "4" "Cache Store — POST /api/cache/store"

  if [[ "$DRY_RUN" == false && "$CACHE_AVAILABLE" == "0" ]]; then
    skip_cache_note
    printf '\n'
    return 0
  fi
  subtitle "Request"
  cmd_label 'curl -s -X POST http://localhost:8756/api/cache/store \
    -H "Content-Type: application/json" \
    -d '"'"'{"query":"efficient speculative decoding","response":"..."}'"'"''
  do_curl POST "$BASE_URL/api/cache/store" \
    -H "Content-Type: application/json" \
    -d '{"query":"efficient speculative decoding","response":"..."}'

  subtitle "What you get"
  info "Stores a query-response pair for future semantic lookups (schema: query, response, phase?):"
  printf '  {"status":"stored"}\n'
  printf '\n'
}

# ── 5. Webhook Verification (Tap's real `hashstring` scheme) ─────────────
# Tap proves webhook authenticity via a `hashstring` header: HMAC-SHA256 over
# x_id{...}x_amount{...}x_currency{...}x_gateway_reference{...}x_payment_reference{...}
# x_status{...}x_created{...}, keyed with the Secret API Key (sk_live_*/sk_test_*).
# Docs: https://developers.tap.company/docs/webhook ("Validate the webhook").
# This section computes a VALID hashstring with python3 so the live call returns
# 200; a tampered header returns 401.
section_5() {
  section "5" "Webhook hashstring Verification — POST /api/billing/webhook"

  local uid="$DEMO_UID"
  local charge="ch_demo_$(date +%s)"
  if [[ "$DRY_RUN" == false ]]; then
    ensure_demo_user || true
    uid="${DEMO_UID:-usr_abc123}"
  else
    uid="<demo-user-id>"
  fi

  local amount="29.00" currency="USD" gw_ref="gw_demo_1" pay_ref="pay_demo_$charge"
  local status="CAPTURED" created="1720000000000"
  local body="{\"id\":\"$charge\",\"object\":\"charge\",\"status\":\"$status\",\"amount\":29.0,\"currency\":\"$currency\",\"response\":{\"code\":\"000\",\"message\":\"Success\"},\"reference\":{\"gateway\":\"$gw_ref\",\"payment\":\"$pay_ref\"},\"transaction\":{\"created\":\"$created\"},\"metadata\":{\"udf1\":\"plan:pro_monthly\",\"udf2\":\"user:$uid\",\"udf3\":\"email:$DEMO_EMAIL\"}}"
  local hashstring="<computed-at-runtime>"
  if [[ "$DRY_RUN" == false ]] && command -v python3 &>/dev/null; then
    # Mirror of backend/routers/billing.py::_verify_tap_hashstring (charge recipe).
    # Uses TAP_API_KEY when set (never echoed); falls back to "unsigned-demo".
    local key="${TAP_API_KEY:-}"
    if [[ -z "$key" && -f "$DEMO_ENV_FILE" ]]; then
      key="$(grep -E '^TAP_API_KEY=' "$DEMO_ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\"''')"
    fi
    if [[ -n "$key" ]]; then
      hashstring="$(TAP_DEMO_KEY="$key" python3 -c "
import hashlib, hmac, os
s = 'x_id$charge' + 'x_amount$amount' + 'x_currency$currency' + 'x_gateway_reference$gw_ref' + 'x_payment_reference$pay_ref' + 'x_status$status' + 'x_created$created'
print(hmac.new(os.environ['TAP_DEMO_KEY'].encode(), s.encode(), hashlib.sha256).hexdigest())
" 2>/dev/null)"
      [[ -z "$hashstring" ]] && hashstring="<compute-failed>"
    else
      hashstring="unsigned-demo"
    fi
  fi

  subtitle "Request"
  cmd_label 'curl -s -X POST http://localhost:8756/api/billing/webhook \
    -H "Content-Type: application/json" \
    -H "hashstring: <hmac-of-fields-with-secret-api-key>" \
    -d '"'"'{"id":"<charge_id>","object":"charge","status":"CAPTURED",...}'"'"''
  do_curl POST "$BASE_URL/api/billing/webhook" \
    -H "Content-Type: application/json" \
    -H "hashstring: $hashstring" \
    -d "$body"

  subtitle "How hashstring verification works"
  info "The server verifies Tap's hashstring header (HMAC-SHA256, Secret API Key):"
  printf '  1. Concatenate x_id, x_amount (currency-rounded), x_currency,\n'
  printf '     x_gateway_reference, x_payment_reference, x_status, x_created\n'
  printf '  2. Compute HMAC-SHA256 with TAP_API_KEY (sk_live_*/sk_test_*) as the key\n'
  printf '  3. Compare against the `hashstring` request header (compare_digest)\n'
  printf '  4. Reject mismatches with 401; unsigned posts 401 only when no API key is configured\n'
  printf '\n'
}

# ── 6. Subscription Status ────────────────────────────────────────────
section_6() {
  section "6" "Subscription Status — GET /api/billing/subscription"

  if [[ "$DRY_RUN" == false ]]; then
    if ensure_demo_user; then
      local token="$DEMO_TOKEN"
    else
      local token=""
    fi
  else
    local token=""
  fi

  subtitle "Request"
  if [[ -n "$token" && "$DRY_RUN" == false ]]; then
    cmd_label "curl -s -X GET $BASE_URL/api/billing/subscription \\"
    cmd_label "  -H \"Authorization: Bearer \${JWT_TOKEN}\""
    do_curl GET "$BASE_URL/api/billing/subscription" \
      -H "Authorization: Bearer $token"
  else
    cmd_label "curl -s -X GET $BASE_URL/api/billing/subscription \\"
    cmd_label "  -H \"Authorization: Bearer <JWT_TOKEN>\""
    do_curl GET "$BASE_URL/api/billing/subscription" \
      -H "Authorization: Bearer <JWT_TOKEN>"
  fi

  subtitle "What you get"
  info "Returns the current user's subscription tier and status:"
  printf '  {"tier":"pro_monthly","status":"active","charge_id":"ch_123","plan":{"id":"pro_monthly","name":"Pro Monthly (Hosted)","price":29,"currency":"USD","interval":"month"}}\n'
  printf '  (fresh account → {"tier":"free","status":"active","runs_limit":10,"plan":{...}})\n'
  printf '\n'
}

# ── Summary ────────────────────────────────────────────────────────────
section_summary() {
  section "S" "Endpoint Summary"

  printf "\n${BOLD}leastgen-hosted API Endpoints:${RESET}\n\n"

  printf "  ${GREEN}GET${RESET}    /api/billing/plans\n"
  printf "           Token packs (\$49/\$199/\$699) + subscription plans (\$0/\$29/\$290)\n\n"

  printf "  ${GREEN}GET${RESET}    /api/cache/stats\n"
  printf "           Semantic / template / document cache statistics\n\n"

  printf "  ${GREEN}POST${RESET}   /api/cache/lookup\n"
  printf "           Semantic similarity matching for cached responses\n\n"

  printf "  ${GREEN}POST${RESET}   /api/cache/store\n"
  printf "           Store a response for future cache lookups\n\n"

  printf "  ${GREEN}POST${RESET}   /api/billing/webhook\n"
  printf "           HMAC-SHA256 webhook signature verification (Tap gateway)\n\n"

  printf "  ${GREEN}GET${RESET}    /api/billing/subscription\n"
  printf "           User's current subscription tier and status\n\n"

  print_separator
  printf "\n${DIM}leastgen-hosted — API Demo Script${RESET}\n"
  printf "${DIM}Token billing + semantic caching showcase${RESET}\n\n"
}

# ── Argument Parsing ───────────────────────────────────────────────────
SECTIONS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --no-jq)
      USE_JQ=false
      shift
      ;;
    --help|-h)
      cat <<EOF
leastgen-hosted — API Demo Script

Usage:
  ./api-demo.sh [OPTIONS] [SECTIONS...]

Options:
  --base-url URL      Override the API base URL (default: http://localhost:8756)
  --dry-run           Print commands without executing them
  --no-jq             Skip jq pretty-printing
  --help, -h          Show this help message

Sections:
  1  Token Packs & Subscription Plans  (GET /api/billing/plans)
  2  Cache Statistics                  (GET /api/cache/stats)
  3  Cache Lookup                      (POST /api/cache/lookup)
  4  Cache Store                       (POST /api/cache/store)
  5  Webhook Verification              (POST /api/billing/webhook)
  6  Subscription Status               (GET /api/billing/subscription)
  s  Summary

Examples:
  ./api-demo.sh                    # Run all sections live
  ./api-demo.sh --dry-run          # Preview commands without executing
  ./api-demo.sh --base-url https://api.example.com  # Custom URL
  ./api-demo.sh 1 2 5              # Run only plans, cache stats, and webhook
EOF
      exit 0
      ;;
    --section|-s)
      SECTIONS+=("$2")
      shift 2
      ;;
    *)
      SECTIONS+=("$1")
      shift
      ;;
  esac
done

# ── Main ───────────────────────────────────────────────────────────────
print_banner
probe_cache
if [[ "$CACHE_AVAILABLE" == "0" ]]; then
  warn "No /api/cache/* on this server — cache sections (2/3/4) will SKIP"
fi

if [[ ${#SECTIONS[@]} -eq 0 ]]; then
  section_1
  section_2
  section_3
  section_4
  section_5
  section_6
  section_summary
else
  for sec in "${SECTIONS[@]}"; do
    case "$sec" in
      1) section_1 ;;
      2) section_2 ;;
      3) section_3 ;;
      4) section_4 ;;
      5) section_5 ;;
      6) section_6 ;;
      s|S|summary) section_summary ;;
      *) warn "Unknown section: $sec" ;;
    esac
  done
fi

printf "\n${GREEN}${BOLD}Demo complete.${RESET}\n\n"
