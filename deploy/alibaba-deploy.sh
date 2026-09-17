#!/usr/bin/env bash
# ==============================================================================
# Alibaba Cloud Production Deployment Script
# Nova Labs (think-fast) & LeastGen Labs (leastgen-hosted)
# Target OS: Ubuntu 22.04 LTS / Debian 11 / Debian 12 (Alibaba Cloud ECS)
# ==============================================================================
set -euo pipefail

# ── Color Output Helpers ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_step()    { echo -e "\n${CYAN}${BOLD}==> $*${NC}"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Defaults & Configuration ──────────────────────────────────────────────────
APP_NAME="nova"
APP_USER="nova"
APP_DIR="/opt/nova"
APP_PORT="8756"
DOMAIN_NAME=""
USE_ALICLOUD_MIRROR="auto"
SETUP_SWAP="true"
SETUP_NGINX="true"
SETUP_FIREWALL="true"
SETUP_SSL="false"
CERTBOT_EMAIL=""
DRY_RUN="false"
UPDATE_ONLY="false"
SKIP_DEPS="false"
WORKERS="2"

# Detect script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Usage / Help ──────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Alibaba Cloud Production Deployment Automation

Usage:
  sudo bash deploy/alibaba-deploy.sh [OPTIONS]

Options:
  --app <name>             Application flavor: 'nova' or 'leastgen' (default: nova)
  --user <username>        System user to run service (default: matches app name)
  --dir <path>             Deployment directory (default: /opt/<app>)
  --port <port>            Internal application port (default: 8756)
  --domain <domain>        Public domain name for Nginx server block & SSL
  --ssl                    Attempt Let's Encrypt SSL certificate issuance via Certbot
  --email <email>          Email address for Let's Encrypt notifications
  --mirror <true|false|auto> Use Alibaba Cloud (Aliyun) apt & PyPI mirrors (default: auto)
  --workers <count>        Number of Uvicorn workers (default: 2)
  --no-swap                Skip automatic swap file creation
  --no-nginx               Skip Nginx installation and configuration
  --no-firewall            Skip UFW firewall configuration
  --skip-deps              Skip apt package installation (faster re-runs)
  --update                 Quick code & venv update mode (preserves configs)
  --dry-run                Print planned actions without modifying system
  -h, --help               Display this help message

Examples:
  # Deploy Nova Labs on Alibaba Cloud ECS with custom domain
  sudo bash deploy/alibaba-deploy.sh --app nova --domain research.example.com --ssl --email admin@example.com

  # Deploy LeastGen Labs
  sudo bash deploy/alibaba-deploy.sh --app leastgen --domain api.leastgen.com

  # Fast update of existing deployment
  sudo bash deploy/alibaba-deploy.sh --app nova --update
EOF
    exit 0
}

# ── Parse Arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app)
            APP_NAME="$2"
            shift 2
            ;;
        --user)
            APP_USER="$2"
            shift 2
            ;;
        --dir)
            APP_DIR="$2"
            shift 2
            ;;
        --port)
            APP_PORT="$2"
            shift 2
            ;;
        --domain)
            DOMAIN_NAME="$2"
            shift 2
            ;;
        --ssl)
            SETUP_SSL="true"
            shift
            ;;
        --email)
            CERTBOT_EMAIL="$2"
            shift 2
            ;;
        --mirror)
            USE_ALICLOUD_MIRROR="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --no-swap)
            SETUP_SWAP="false"
            shift
            ;;
        --no-nginx)
            SETUP_NGINX="false"
            shift
            ;;
        --no-firewall)
            SETUP_FIREWALL="false"
            shift
            ;;
        --skip-deps)
            SKIP_DEPS="true"
            shift
            ;;
        --update)
            UPDATE_ONLY="true"
            shift
            ;;
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Sync defaults if app is leastgen
if [[ "$APP_NAME" == "leastgen" && "$APP_USER" == "nova" ]]; then
    APP_USER="leastgen"
fi
if [[ "$APP_NAME" == "leastgen" && "$APP_DIR" == "/opt/nova" ]]; then
    APP_DIR="/opt/leastgen"
fi

# ── Preflight Checks ──────────────────────────────────────────────────────────
check_privileges() {
    log_step "Checking execution privileges..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Root check bypassed"
        return
    fi
    if [[ "$EUID" -ne 0 ]]; then
        log_error "This script must be run as root or via sudo: sudo bash $0"
        exit 1
    fi
    log_success "Running with root privileges."
}

check_os() {
    log_step "Checking operating system compatibility..."
    if [[ ! -f /etc/os-release ]]; then
        log_warn "Unknown Linux distribution (/etc/os-release not found). Proceeding cautiously."
        return
    fi

    # shellcheck source=/dev/null
    source /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VERSION="${VERSION_ID:-unknown}"

    log_info "Detected OS: ${NAME:-Linux} ${VERSION:-} (ID: ${OS_ID}, Version: ${OS_VERSION})"

    if [[ "$OS_ID" != "ubuntu" && "$OS_ID" != "debian" && "$OS_ID" != "alinux" ]]; then
        log_warn "This script is optimized for Ubuntu 20.04/22.04/24.04, Debian 11/12, or Alibaba Cloud Linux 3."
    else
        log_success "Operating system supported."
    fi
}

detect_alicloud() {
    log_step "Detecting Alibaba Cloud ECS environment..."
    IS_ALICLOUD="false"

    # Check Alibaba Cloud ECS metadata server (100.100.100.200 is private to Alibaba Cloud VPC)
    if curl -s --connect-timeout 1 http://100.100.100.200/latest/meta-data/instance-id &>/dev/null; then
        IS_ALICLOUD="true"
        INSTANCE_ID=$(curl -s --connect-timeout 2 http://100.100.100.200/latest/meta-data/instance-id || echo "unknown")
        REGION_ID=$(curl -s --connect-timeout 2 http://100.100.100.200/latest/meta-data/region-id || echo "unknown")
        log_success "Alibaba Cloud ECS detected! (Instance: ${INSTANCE_ID}, Region: ${REGION_ID})"
    else
        log_info "Non-Alibaba Cloud or private metadata inaccessible. Running standard cloud configuration."
    fi

    if [[ "$USE_ALICLOUD_MIRROR" == "auto" ]]; then
        if [[ "$IS_ALICLOUD" == "true" ]]; then
            USE_ALICLOUD_MIRROR="true"
        else
            USE_ALICLOUD_MIRROR="false"
        fi
    fi
    log_info "Alibaba Cloud mirrors: ${USE_ALICLOUD_MIRROR}"
}

# ── Mirror Configuration ──────────────────────────────────────────────────────
configure_mirrors() {
    if [[ "$USE_ALICLOUD_MIRROR" != "true" ]]; then
        return
    fi
    log_step "Configuring Alibaba Cloud mirrors for accelerated package downloads..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would configure Aliyun apt and PyPI mirrors"
        return
    fi

    # Configure PyPI mirror globally
    mkdir -p /root/.pip /home/"${APP_USER}"/.pip 2>/dev/null || true
    cat <<EOF | tee /root/.pip/pip.conf > /dev/null
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
timeout = 60
EOF
    log_success "Configured Aliyun PyPI mirror at https://mirrors.aliyun.com/pypi/simple/"
}

# ── Swap Setup ────────────────────────────────────────────────────────────────
configure_swap() {
    if [[ "$SETUP_SWAP" != "true" ]]; then
        log_info "Skipping swap configuration (--no-swap specified)."
        return
    fi
    log_step "Checking memory and swap allocation..."
    
    if [[ ! -f /proc/meminfo ]]; then
        log_info "No /proc/meminfo found (non-Linux or container). Skipping swap detection."
        return
    fi

    TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_RAM_MB=$((TOTAL_RAM_KB / 1024))
    EXISTING_SWAP_KB=$(grep SwapTotal /proc/meminfo | awk '{print $2}')

    log_info "Total RAM: ${TOTAL_RAM_MB}MB, Existing Swap: $((EXISTING_SWAP_KB / 1024))MB"

    # If swap is under 1GB and RAM is <= 4GB, add 2GB swap file
    if [[ "$EXISTING_SWAP_KB" -lt 1048576 && "$TOTAL_RAM_MB" -le 4096 ]]; then
        log_warn "Low memory detected on cloud instance. Provisioning 2GB swap file for build stability..."
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "[DRY-RUN] Would create 2GB swap at /swapfile"
            return
        fi

        if [[ ! -f /swapfile ]]; then
            fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
            chmod 600 /swapfile
            mkswap /swapfile
            swapon /swapfile
            if ! grep -q "/swapfile" /etc/fstab; then
                echo "/swapfile none swap sw 0 0" >> /etc/fstab
            fi
            sysctl vm.swappiness=20 > /dev/null
            log_success "2GB swapfile active and added to /etc/fstab."
        else
            swapon /swapfile 2>/dev/null || true
            log_info "Existing /swapfile activated."
        fi
    else
        log_info "Adequate RAM or existing swap detected. No additional swap needed."
    fi
}

# ── System Dependencies ───────────────────────────────────────────────────────
install_dependencies() {
    if [[ "$SKIP_DEPS" == "true" || "$UPDATE_ONLY" == "true" ]]; then
        log_info "Skipping system dependencies installation."
        return
    fi

    log_step "Installing system dependencies and runtime tools..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would install: python3, python3-venv, python3-pip, build-essential, nginx, ufw, certbot..."
        return
    fi

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y

    PACKAGES=(
        python3
        python3-pip
        python3-venv
        python3-dev
        build-essential
        git
        curl
        wget
        jq
        sqlite3
        logrotate
        fail2ban
        ca-certificates
    )

    if [[ "$SETUP_NGINX" == "true" ]]; then
        PACKAGES+=(nginx)
    fi

    if [[ "$SETUP_FIREWALL" == "true" ]]; then
        PACKAGES+=(ufw)
    fi

    if [[ "$SETUP_SSL" == "true" ]]; then
        PACKAGES+=(certbot python3-certbot-nginx)
    fi

    apt-get install -y "${PACKAGES[@]}"
    log_success "System packages installed successfully."
}

# ── User & Directory Setup ────────────────────────────────────────────────────
setup_user_and_dirs() {
    log_step "Creating dedicated system user and runtime directories..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create user ${APP_USER} and dirs ${APP_DIR}, /var/log/${APP_NAME}, /etc/${APP_NAME}"
        return
    fi

    # Create service system user if absent
    if ! id -u "${APP_USER}" &>/dev/null; then
        useradd --system --shell /usr/sbin/nologin --home-dir "${APP_DIR}" --comment "${APP_NAME} service user" "${APP_USER}"
        log_success "Created system user: ${APP_USER}"
    else
        log_info "System user ${APP_USER} already exists."
    fi

    # Create required directory hierarchy
    mkdir -p "${APP_DIR}"
    mkdir -p "${APP_DIR}/data"
    mkdir -p "${APP_DIR}/ideaspark_run"
    mkdir -p "${APP_DIR}/scoop_runs"
    mkdir -p "/var/log/${APP_NAME}"
    mkdir -p "/etc/${APP_NAME}"

    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    chown -R "${APP_USER}:${APP_USER}" "/var/log/${APP_NAME}"
    chmod 750 "${APP_DIR}"
    chmod 750 "/var/log/${APP_NAME}"
    chmod 750 "/etc/${APP_NAME}"
    log_success "Directory structure initialized under ${APP_DIR}."
}

# ── Code Deployment ───────────────────────────────────────────────────────────
sync_application_code() {
    log_step "Syncing application codebase into ${APP_DIR}..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would copy code from ${REPO_ROOT} to ${APP_DIR}"
        return
    fi

    # Exclude venvs, git metadata, and local development caches
    if command -v rsync &>/dev/null; then
        rsync -a --delete \
            --exclude '.venv' \
            --exclude '.git' \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.agent-teams' \
            --exclude '.worktrees' \
            --exclude '.mnemon' \
            --exclude 'ideaspark_run/*' \
            --exclude 'scoop_runs/*' \
            "${REPO_ROOT}/" "${APP_DIR}/"
    else
        cp -R "${REPO_ROOT}/." "${APP_DIR}/"
        rm -rf "${APP_DIR}/.venv" "${APP_DIR}/.git" "${APP_DIR}/.agent-teams" 2>/dev/null || true
    fi

    # Ensure runtime writable directories exist and are owned by app user
    mkdir -p "${APP_DIR}/data" "${APP_DIR}/ideaspark_run" "${APP_DIR}/scoop_runs"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    log_success "Application code synced to ${APP_DIR}."
}

# ── Python Virtual Environment ────────────────────────────────────────────────
setup_virtualenv() {
    log_step "Configuring Python virtual environment in ${APP_DIR}/.venv..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create venv at ${APP_DIR}/.venv and install requirements"
        return
    fi

    if [[ ! -d "${APP_DIR}/.venv" ]]; then
        python3 -m venv "${APP_DIR}/.venv"
        log_success "Created Python virtual environment."
    fi

    # Upgrade pip and install wheel
    "${APP_DIR}/.venv/bin/pip" install --upgrade pip setuptools wheel --quiet

    # Install application dependencies
    if [[ -f "${APP_DIR}/requirements.txt" ]]; then
        log_info "Installing Python dependencies from requirements.txt..."
        "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
    fi

    # Ensure uvicorn, fastapi, pydantic, pyjwt, requests, httpx are present
    "${APP_DIR}/.venv/bin/pip" install --upgrade uvicorn fastapi pydantic pyjwt requests httpx --quiet

    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/.venv"
    log_success "Python virtual environment configured."
}

# ── Environment & Secrets Setup ───────────────────────────────────────────────
setup_environment_file() {
    log_step "Setting up production environment file (/etc/${APP_NAME}/${APP_NAME}.env)..."
    ENV_FILE="/etc/${APP_NAME}/${APP_NAME}.env"
    LOCAL_ENV_FILE="${APP_DIR}/.env.production"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would create environment file ${ENV_FILE}"
        return
    fi

    if [[ ! -f "$ENV_FILE" ]]; then
        log_info "Generating initial production environment configuration..."
        GENERATED_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)

        cat <<EOF > "$ENV_FILE"
# ==============================================================================
# Production Environment Configuration for ${APP_NAME}
# Generated on: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# ==============================================================================
APP_NAME=${APP_NAME}
APP_ENV=production
HOST=127.0.0.1
PORT=${APP_PORT}
WORKERS=${WORKERS}
DEBUG=false
LOG_LEVEL=info

# ── Authentication & Security ─────────────────────────────────────────────────
# Generated cryptographic secret for JWT authentication tokens
OPENRESEARCH_JWT_SECRET=${GENERATED_JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=72

# ── AI / LLM Provider ─────────────────────────────────────────────────────────
# Required: Insert your live OpenRouter API Key below
OPENROUTER_API_KEY=sk-or-v1-REPLACE_WITH_LIVE_KEY

# ── Tap Payment Gateway ───────────────────────────────────────────────────────
TAP_API_KEY=sk_live_REPLACE_WITH_TAP_KEY
TAP_MERCHANT_ID=REPLACE_WITH_TAP_MERCHANT_ID
TAP_WEBHOOK_SECRET=REPLACE_WITH_TAP_WEBHOOK_SECRET
TAP_POST_URL=http://localhost:${APP_PORT}/api/billing/webhook

# ── Database & Storage ────────────────────────────────────────────────────────
DATABASE_PATH=${APP_DIR}/data/ideaflow.db
PROJECT_ROOT=${APP_DIR}
RUN_DIR=${APP_DIR}/ideaspark_run

# ── CORS Origins (Comma-separated, never '*' in production) ───────────────────
CORS_ORIGINS=http://localhost:${APP_PORT},http://127.0.0.1:${APP_PORT}
EOF
        if [[ -n "$DOMAIN_NAME" ]]; then
            sed -i "s|CORS_ORIGINS=.*|CORS_ORIGINS=https://${DOMAIN_NAME},http://${DOMAIN_NAME}|g" "$ENV_FILE"
            sed -i "s|TAP_POST_URL=.*|TAP_POST_URL=https://${DOMAIN_NAME}/api/billing/webhook|g" "$ENV_FILE"
        fi
        log_success "Generated new production secret & config at ${ENV_FILE}"
    else
        log_info "Preserving existing configuration at ${ENV_FILE}"
    fi

    # Set strict file permissions (read/write only by root and app user)
    chown root:"${APP_USER}" "$ENV_FILE"
    chmod 640 "$ENV_FILE"

    # Link into app dir for compatibility
    ln -sf "$ENV_FILE" "$LOCAL_ENV_FILE"
}

# ── Systemd Service Installation ──────────────────────────────────────────────
install_systemd_service() {
    log_step "Installing and configuring systemd unit (${APP_NAME}.service)..."
    SERVICE_PATH="/etc/systemd/system/${APP_NAME}.service"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would install systemd service to ${SERVICE_PATH}"
        return
    fi

    # Prefer template file from repository if present, otherwise render inline
    TEMPLATE_FILE="${APP_DIR}/deploy/${APP_NAME}.service"
    if [[ ! -f "$TEMPLATE_FILE" ]]; then
        TEMPLATE_FILE="${APP_DIR}/deploy/systemd/${APP_NAME}.service.template"
    fi

    if [[ -f "$TEMPLATE_FILE" ]]; then
        sed -e "s|{{APP_NAME}}|${APP_NAME}|g" \
            -e "s|{{APP_USER}}|${APP_USER}|g" \
            -e "s|{{APP_DIR}}|${APP_DIR}|g" \
            -e "s|{{APP_PORT}}|${APP_PORT}|g" \
            -e "s|{{WORKERS}}|${WORKERS}|g" \
            "$TEMPLATE_FILE" > "$SERVICE_PATH"
    else
        # Render default hardened systemd unit
        cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=${APP_NAME^} Labs Production API Server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=-/etc/${APP_NAME}/${APP_NAME}.env

ExecStart=${APP_DIR}/.venv/bin/uvicorn backend.main:app \\
    --host 127.0.0.1 \\
    --port ${APP_PORT} \\
    --workers ${WORKERS} \\
    --proxy-headers \\
    --forwarded-allow-ips="*"

ExecReload=/bin/kill -HUP \$MAINPID

# Process Lifecycle & Restart Policies
Restart=always
RestartSec=5s
StartLimitIntervalSec=120s
StartLimitBurst=5
TimeoutStopSec=30s

# Resource Limits
LimitNOFILE=65535
LimitNPROC=4096
TasksMax=4096

# Security Hardening & Sandboxing
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectControlGroups=true
ReadWritePaths=${APP_DIR}/data ${APP_DIR}/ideaspark_run ${APP_DIR}/scoop_runs /var/log/${APP_NAME}

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${APP_NAME}-api

[Install]
WantedBy=multi-user.target
EOF
    fi

    chmod 644 "$SERVICE_PATH"
    systemctl daemon-reload
    systemctl enable "${APP_NAME}.service"
    systemctl restart "${APP_NAME}.service"
    log_success "Systemd unit ${APP_NAME}.service installed and started."
}

# ── Nginx Reverse Proxy Setup ─────────────────────────────────────────────────
configure_nginx() {
    if [[ "$SETUP_NGINX" != "true" ]]; then
        log_info "Skipping Nginx setup (--no-nginx specified)."
        return
    fi
    log_step "Configuring Nginx reverse proxy with SSE streaming and rate limiting..."

    SERVER_NAME="${DOMAIN_NAME:-_}"
    NGINX_CONF_AVAIL="/etc/nginx/sites-available/${APP_NAME}.conf"
    NGINX_CONF_ENABLE="/etc/nginx/sites-enabled/${APP_NAME}.conf"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would configure Nginx server block at ${NGINX_CONF_AVAIL}"
        return
    fi

    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled

    cat <<EOF > "$NGINX_CONF_AVAIL"
# ==============================================================================
# Nginx Reverse Proxy Configuration for ${APP_NAME}
# Optimized for Fast APIs, Server-Sent Events (SSE), and LLM Streaming
# ==============================================================================

# Rate Limiting Definitions (injected into http or top-level context)
limit_req_zone \$binary_remote_addr zone=${APP_NAME}_api:10m rate=15r/s;
limit_req_zone \$binary_remote_addr zone=${APP_NAME}_auth:10m rate=5r/m;
limit_req_zone \$binary_remote_addr zone=${APP_NAME}_pipeline:10m rate=2r/m;

upstream ${APP_NAME}_backend {
    server 127.0.0.1:${APP_PORT} max_fails=3 fail_timeout=10s;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};

    client_max_body_size 50M;
    client_body_buffer_size 128k;

    # Certbot challenge endpoint
    location /.well-known/acme-challenge/ {
        root /var/www/html;
        allow all;
    }

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;

    # Static Assets Caching
    location ~* \.(?:css|js|jpg|jpeg|gif|png|ico|cur|gz|svg|svgz|mp4|ogg|ogv|webm|htc|woff2|woff)$ {
        proxy_pass http://${APP_NAME}_backend;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        expires 7d;
        add_header Cache-Control "public, no-transform";
    }

    # Auth Endpoints (Brute force protection)
    location /api/auth/ {
        limit_req zone=${APP_NAME}_auth burst=5 nodelay;
        proxy_pass http://${APP_NAME}_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Pipeline Execution Endpoints (Rate limiting for heavy LLM operations)
    location /api/pipeline/start {
        limit_req zone=${APP_NAME}_pipeline burst=2 nodelay;
        proxy_pass http://${APP_NAME}_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # SSE / LLM Streaming parameters
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # General API & SPA Reverse Proxy
    location / {
        limit_req zone=${APP_NAME}_api burst=30 nodelay;

        proxy_pass http://${APP_NAME}_backend;
        proxy_http_version 1.1;

        # WebSocket & Connection Upgrade headers
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Standard Proxy Headers
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;

        # Essential for SSE / LLM Streaming without buffering delay
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;

        # Extended timeouts for research pipeline operations (10 minutes)
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
    }
}
EOF

    # Remove default welcome site if present
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

    # Enable site
    ln -sf "$NGINX_CONF_AVAIL" "$NGINX_CONF_ENABLE"

    # Test Nginx syntax and reload
    if nginx -t; then
        systemctl reload nginx
        log_success "Nginx reverse proxy active for ${SERVER_NAME}."
    else
        log_error "Nginx configuration syntax check failed! Check ${NGINX_CONF_AVAIL}."
    fi
}

# ── SSL / Certbot Setup ───────────────────────────────────────────────────────
configure_ssl() {
    if [[ "$SETUP_SSL" != "true" || -z "$DOMAIN_NAME" ]]; then
        return
    fi
    log_step "Obtaining Let's Encrypt SSL certificate via Certbot for ${DOMAIN_NAME}..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would run certbot --nginx -d ${DOMAIN_NAME}"
        return
    fi

    CERTBOT_ARGS=(--nginx -d "$DOMAIN_NAME" --non-interactive --agree-tos)
    if [[ -n "$CERTBOT_EMAIL" ]]; then
        CERTBOT_ARGS+=(--email "$CERTBOT_EMAIL")
    else
        CERTBOT_ARGS+=(--register-unsafely-without-email)
    fi

    if certbot "${CERTBOT_ARGS[@]}"; then
        log_success "SSL certificate successfully provisioned and configured in Nginx!"
        # Add monthly renewal check
        systemctl enable certbot.timer 2>/dev/null || true
    else
        log_warn "Certbot automated setup encountered an error. Please verify domain DNS points to this IP."
    fi
}

# ── Firewall & Security Hardening ─────────────────────────────────────────────
configure_firewall() {
    if [[ "$SETUP_FIREWALL" != "true" ]]; then
        log_info "Skipping firewall configuration (--no-firewall specified)."
        return
    fi
    log_step "Configuring UFW host firewall..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would allow SSH (22), HTTP (80), HTTPS (443) and enable UFW"
        return
    fi

    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp comment 'SSH'
    ufw allow 80/tcp comment 'HTTP Nginx'
    ufw allow 443/tcp comment 'HTTPS Nginx'
    # Block direct external access to internal backend port
    ufw deny "${APP_PORT}"/tcp comment 'Internal Backend Port' || true

    # Enable without prompt
    echo "y" | ufw enable
    log_success "UFW firewall enabled: ports 22, 80, 443 permitted."
}

# ── Verification & Healthcheck ────────────────────────────────────────────────
verify_service() {
    log_step "Verifying application service health..."
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY-RUN] Would curl http://127.0.0.1:${APP_PORT}/api/health"
        return
    fi

    local ATTEMPTS=10
    local WAIT_SEC=2
    local HEALTH_URL="http://127.0.0.1:${APP_PORT}/api/health"

    for ((i=1; i<=ATTEMPTS; i++)); do
        if curl -s -f "$HEALTH_URL" &>/dev/null; then
            log_success "Service responded successfully on ${HEALTH_URL}!"
            return 0
        fi
        log_info "Waiting for service startup (${i}/${ATTEMPTS})..."
        sleep "$WAIT_SEC"
    done

    log_warn "Service did not respond within $((ATTEMPTS * WAIT_SEC))s. Check logs: journalctl -u ${APP_NAME}.service -n 50"
}

# ── Summary Report ────────────────────────────────────────────────────────────
print_summary() {
    echo -e "
================================================================================
${GREEN}${BOLD}Alibaba Cloud Deployment Completed Successfully!${NC}
================================================================================
Application:     ${APP_NAME} (${APP_USER})
Install Path:    ${APP_DIR}
Port:            ${APP_PORT} (Internal) / 80 & 443 (Nginx)
Config File:     /etc/${APP_NAME}/${APP_NAME}.env
Log Directory:   /var/log/${APP_NAME}
Systemd Unit:    systemctl status ${APP_NAME}.service

${BOLD}Useful Management Commands:${NC}
  • View live logs:     journalctl -u ${APP_NAME}.service -f
  • Restart service:   systemctl restart ${APP_NAME}.service
  • Nginx reload:       nginx -t && systemctl reload nginx
  • Health check:      curl -s http://127.0.0.1:${APP_PORT}/api/health | jq .

${BOLD}Important Next Steps:${NC}
  1. Configure your live API keys in: /etc/${APP_NAME}/${APP_NAME}.env
     (Set OPENROUTER_API_KEY, TAP_API_KEY, TAP_WEBHOOK_SECRET)
  2. Reload service after updating credentials:
     systemctl restart ${APP_NAME}.service
  3. Ensure Alibaba Cloud Security Group permits inbound TCP on:
     - Port 22 (SSH)
     - Port 80 (HTTP)
     - Port 443 (HTTPS)
================================================================================
"
}

# ── Main Routine ──────────────────────────────────────────────────────────────
main() {
    log_info "Initiating deployment for: ${APP_NAME}..."
    check_privileges
    check_os
    detect_alicloud
    configure_mirrors
    configure_swap
    install_dependencies
    setup_user_and_dirs
    sync_application_code
    setup_virtualenv
    setup_environment_file
    install_systemd_service
    configure_nginx
    configure_ssl
    configure_firewall
    verify_service
    print_summary
}

main "$@"
