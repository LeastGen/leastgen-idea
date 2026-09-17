# Alibaba Cloud ECS Production Deployment Guide
**Applications:** LeastGen Labs (`think-fast`) & LeastGen Labs (`leastgen-hosted`)  
**Target OS:** Ubuntu 22.04 LTS / Debian 12 (64-bit)  
**Architecture:** FastAPI + Uvicorn (Systemd) + Nginx (Reverse Proxy & SSL) + SQLite WAL

---

## 1. Prerequisites & Alibaba Cloud ECS Provisioning

### 1.1 Recommended Instance Specifications
- **Instance Type:** Alibaba Cloud ECS Compute-Optimized (e.g., `ecs.c7.large` or `ecs.e-c2m2.large`):
  - **vCPU:** 2 or 4 vCPUs
  - **Memory:** 4GB RAM minimum (2GB supported with automated 2GB swap file)
  - **Storage:** 40GB+ ESSD System Disk
  - **OS:** Ubuntu 22.04 64-bit (or Debian 12)
- **Networking:**
  - Allocate an **Elastic IP (EIP)** and associate it with the ECS instance.
  - Set DNS `A` records:
    - `research.yourdomain.com` -> `<ECS_PUBLIC_IP>`
    - `api.leastgen.com` -> `<ECS_PUBLIC_IP>`

### 1.2 Security Group Configuration
In the Alibaba Cloud ECS Console under **Network & Security -> Security Groups**:
Configure Inbound Rules:
| Protocol | Port Range | Source | Purpose |
|----------|------------|--------|---------|
| TCP | `22` | `0.0.0.0/0` (or your bastion IP) | Secure Shell (SSH) |
| TCP | `80` | `0.0.0.0/0` | HTTP & Let's Encrypt ACME |
| TCP | `443` | `0.0.0.0/0` | HTTPS (Encrypted Traffic) |

> **IMPORTANT:** Do NOT open port `8756` or `8757` in the Security Group. The API servers bind locally to `127.0.0.1` and are accessed exclusively through Nginx.

---

## 2. Automated One-Command Deployment

The repository includes a battle-tested automated deployment script `deploy/alibaba-deploy.sh`.

### 2.1 Deploy LeastGen Labs (Default)
SSH into your ECS instance:
```bash
ssh root@<YOUR_ECS_IP>

# Clone repository or copy project files to server
git clone https://github.com/KhalidAlnujaidi/leastgen.git /tmp/leastgen
cd /tmp/leastgen

# Run deployment automation
sudo bash deploy/alibaba-deploy.sh \
    --app leastgen \
    --domain research.yourdomain.com \
    --ssl \
    --email admin@yourdomain.com
```

### 2.2 Deploy LeastGen Labs
```bash
sudo bash deploy/alibaba-deploy.sh \
    --app leastgen \
    --domain api.leastgen.com \
    --port 8757 \
    --ssl \
    --email admin@yourdomain.com
```

### 2.3 Dry Run Mode
To verify planned actions without altering system state:
```bash
sudo bash deploy/alibaba-deploy.sh --dry-run --domain research.yourdomain.com
```

---

## 3. Manual Step-by-Step Deployment Walkthrough

If preferred or for custom infrastructure management, follow these manual steps.

### Step 1: Install System Packages
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    python3 python3-pip python3-venv python3-dev \
    build-essential git curl jq sqlite3 \
    nginx certbot python3-certbot-nginx ufw fail2ban
```

### Step 2: Create Dedicated Service User & Directories
```bash
# Create system user without interactive shell
sudo useradd --system --shell /usr/sbin/nologin --home-dir /opt/leastgen leastgen

# Create directory hierarchy
sudo mkdir -p /opt/leastgen /opt/leastgen/data /opt/leastgen/ideaspark_run /opt/leastgen/scoop_runs /var/log/leastgen /etc/leastgen

# Transfer code into /opt/leastgen
sudo cp -R . /opt/leastgen/

# Set ownership
sudo chown -R leastgen:leastgen /opt/leastgen /var/log/leastgen /etc/leastgen
sudo chmod 750 /opt/leastgen /var/log/leastgen /etc/leastgen
```

### Step 3: Setup Python Virtual Environment
```bash
cd /opt/leastgen
sudo -u leastgen python3 -m venv .venv
sudo -u leastgen /opt/leastgen/.venv/bin/pip install --upgrade pip wheel
sudo -u leastgen /opt/leastgen/.venv/bin/pip install -r requirements.txt
sudo -u leastgen /opt/leastgen/.venv/bin/pip install uvicorn fastapi pyjwt pydantic
```

### Step 4: Configure Production Secrets
```bash
# Copy production env template
sudo cp /opt/leastgen/deploy/.env.production.example /etc/leastgen/leastgen.env

# Generate secure 64-char JWT secret
JWT_SECRET=$(openssl rand -hex 32)
sudo sed -i "s|REPLACE_WITH_OPENSSL_RAND_HEX_32_OUTPUT|${JWT_SECRET}|" /etc/leastgen/leastgen.env

# Edit /etc/leastgen/leastgen.env to set:
# - OPENROUTER_API_KEY
# - TAP_API_KEY (sk_live_...)
# - TAP_WEBHOOK_SECRET
# - CORS_ORIGINS
sudo nano /etc/leastgen/leastgen.env

# Lock down permissions
sudo chown root:leastgen /etc/leastgen/leastgen.env
sudo chmod 640 /etc/leastgen/leastgen.env
```

### Step 5: Install Systemd Service Unit
```bash
sudo cp /opt/leastgen/deploy/leastgen.service /etc/systemd/system/leastgen.service
sudo systemctl daemon-reload
sudo systemctl enable leastgen.service
sudo systemctl start leastgen.service

# Verify service is running:
sudo systemctl status leastgen.service
curl -s http://127.0.0.1:8756/api/health | jq .
```

### Step 6: Configure Nginx Reverse Proxy
```bash
# Copy Nginx config
sudo cp /opt/leastgen/deploy/nginx.conf /etc/nginx/sites-available/leastgen.conf

# Replace placeholder domain name with your actual domain
sudo sed -i "s|research.example.com|research.yourdomain.com|g" /etc/nginx/sites-available/leastgen.conf

# Enable site
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/leastgen.conf /etc/nginx/sites-enabled/leastgen.conf

# Test configuration syntax
sudo nginx -t
sudo systemctl reload nginx
```

### Step 7: Obtain Let's Encrypt SSL Certificate
```bash
sudo certbot --nginx -d research.yourdomain.com --agree-tos --email admin@yourdomain.com --non-interactive
```

### Step 8: Configure UFW Host Firewall
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8756/tcp
sudo ufw enable
```

---

## 4. Key Performance & Reliability Settings

### 4.1 Server-Sent Events (SSE) & LLM Token Streaming
Streaming LLM tokens requires that intermediate reverse proxies do **not** buffer responses:
```nginx
location ~ ^/api/(pipeline/start|scoop-check/start) {
    proxy_pass http://leastgen_upstream;
    proxy_http_version 1.1;

    # Disable proxy buffering so chunks arrive immediately in browser
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;

    # Extended timeout for deep 12-phase pipeline runs
    proxy_connect_timeout 60s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;
}
```

### 4.2 Rate Limiting Protection
Configured in `deploy/nginx.conf`:
- **Auth routes (`/api/auth/`):** Max 5 requests/minute to prevent brute-force login attempts.
- **Pipeline start (`/api/pipeline/start`):** Max 2 requests/minute to prevent OpenRouter cost spikes.
- **General API:** Max 15 requests/second with burst allowance of 30.

---

## 5. Maintenance & Operations Cheat Sheet

| Task | Command |
|------|---------|
| View real-time logs | `journalctl -u leastgen.service -f` |
| View Nginx access log | `tail -f /var/log/nginx/access.log` |
| Restart backend service | `sudo systemctl restart leastgen.service` |
| Reload Nginx cleanly | `sudo nginx -t && sudo systemctl reload nginx` |
| Backup SQLite database | `sqlite3 /opt/leastgen/data/ideaflow.db ".backup '/opt/leastgen/data/backup-$(date +%F).db'"` |
| Update application code | `sudo bash /opt/leastgen/deploy/alibaba-deploy.sh --update` |
| Test SSL auto-renewal | `sudo certbot renew --dry-run` |
| Check memory & swap | `free -h` |
