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
section_2() {
  section "2" "Cache Statistics — GET /api/cache/stats"

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
section_3() {
  section "3" "Semantic Cache Lookup — POST /api/cache/lookup"

  subtitle "Request"
  cmd_label 'curl -s -X POST http://localhost:8756/api/cache/lookup \
    -H "Content-Type: application/json" \
    -d '"'"'{"query":"efficient speculative decoding for LLM"}'"'"''
  do_curl POST "$BASE_URL/api/cache/lookup" \
    -H "Content-Type: application/json" \
    -d '{"query":"efficient speculative decoding for LLM"}'

  subtitle "What you get"
  info "Returns a cached match if semantic similarity exceeds the threshold:"
  printf '  {"hit":true,"cached_response":"...","similarity":0.94,"tokens_saved":1200}\n'
  printf '\n'
}

# ── 4. Cache Store ────────────────────────────────────────────────────
section_4() {
  section "4" "Cache Store — POST /api/cache/store"

  subtitle "Request"
  cmd_label 'curl -s -X POST http://localhost:8756/api/cache/store \
    -H "Content-Type: application/json" \
    -d '"'"'{"query":"efficient speculative decoding","response":"...","tokens_used":1500}'"'"''
  do_curl POST "$BASE_URL/api/cache/store" \
    -H "Content-Type: application/json" \
    -d '{"query":"efficient speculative decoding","response":"...","tokens_used":1500}'

  subtitle "What you get"
  info "Stores a response for future semantic cache lookups:"
  printf '  {"stored":true,"entry_id":"cache_abc123","ttl_seconds":86400}\n'
  printf '\n'
}

# ── 5. Webhook Verification ───────────────────────────────────────────
section_5() {
  section "5" "Webhook Signature Verification — POST /api/billing/webhook"

  subtitle "Request"
  cmd_label 'curl -s -X POST http://localhost:8756/api/billing/webhook \
    -H "Content-Type: application/json" \
    -H "X-Tap-Signature: <hmac-sha256>" \
    -d '"'"'{"id":"ch_123","status":"CAPTURED",...}'"'"''
  do_curl POST "$BASE_URL/api/billing/webhook" \
    -H "Content-Type: application/json" \
    -H "X-Tap-Signature: <hmac-sha256>" \
    -d '{"id":"ch_123","status":"CAPTURED","response":{"code":"000","message":"Success"},"metadata":{"udf1":"plan:pro_monthly","udf2":"user:usr_abc123","udf3":"email:demo@novalabs.io"}}'

  subtitle "How signature verification works"
  info "The server verifies webhook signatures using HMAC-SHA256:"
  printf '  1. Concatenate the raw request body\n'
  printf '  2. Compute HMAC-SHA256 with TAP_WEBHOOK_SECRET as the key\n'
  printf '  3. Compare the computed digest against the X-Tap-Signature header\n'
  printf '  4. Reject requests with mismatched signatures (401)\n'
  printf '\n'
}

# ── 6. Subscription Status ────────────────────────────────────────────
section_6() {
  section "6" "Subscription Status — GET /api/billing/subscription"

  local token="${JWT_TOKEN:-}"
  if [[ -z "$token" && "$DRY_RUN" == false ]]; then
    local auth_resp
    # NOTE: dummy demo-only credentials for local demo script — not real accounts
    auth_resp=$(curl -s -X POST "$BASE_URL/api/auth/login" \
      -H "Content-Type: application/json" \
      -d '{"email":"demo@leastgen.com","password":"password123"}' 2>/dev/null)
    token=$(echo "$auth_resp" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
    if [[ -z "$token" ]]; then
      auth_resp=$(curl -s -X POST "$BASE_URL/api/auth/signup" \
        -H "Content-Type: application/json" \
        -d '{"email":"demo@leastgen.com","password":"password123","name":"Demo User"}' 2>/dev/null)
      token=$(echo "$auth_resp" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
    fi
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
