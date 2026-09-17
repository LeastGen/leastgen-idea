# Production Deployment & Security Hardening Checklist
**Target Platforms:** Alibaba Cloud ECS (Ubuntu 22.04 LTS / Debian 12), Bare Metal, Linux Cloud VMs  
**Applications:** LeastGen Labs (`think-fast`) & LeastGen Labs (`leastgen-hosted`)

---

## 1. Secrets & Credentials Management

| # | Check | Verification Method | Status |
|---|-------|---------------------|:------:|
| 1.1 | **Rotate Tap Gateway Keys** | Ensure `TAP_API_KEY` begins with `sk_live_`, not `sk_test_`. Verify in `/etc/leastgen/leastgen.env`. | [ ] |
| 1.2 | **Set Cryptographic JWT Secret** | Generate using `openssl rand -hex 32`. Confirm `OPENRESEARCH_JWT_SECRET` is static across restarts. | [ ] |
| 1.3 | **Enforce Webhook Signature Verification** | Set `TAP_WEBHOOK_SECRET` in production env. Verify webhook returns 401 when signature header is missing or tampered. | [ ] |
| 1.4 | **Restrict Environment File Permissions** | Run `chmod 640 /etc/leastgen/leastgen.env && chown root:leastgen /etc/leastgen/leastgen.env`. Verify non-root/non-app users cannot read it. | [ ] |
| 1.5 | **No Secrets in Source Control** | Verify `.gitignore` includes `.env`, `.env.production`, `.kinox/`, `*.db`, `*.pem`, `*.key`. | [ ] |

---

## 2. Alibaba Cloud ECS & Network Security

| # | Check | Verification Method | Status |
|---|-------|---------------------|:------:|
| 2.1 | **ECS Security Group Inbound Rules** | Allow ONLY TCP `22` (SSH), `80` (HTTP), `443` (HTTPS) from `0.0.0.0/0`. Deny all other inbound ports. | [ ] |
| 2.2 | **Block Port 8756 Externally** | Ensure port `8756` is NOT exposed in ECS Security Group or UFW. Uvicorn must listen on `127.0.0.1:8756`. | [ ] |
| 2.3 | **SSH Hardening** | Disable password-based SSH authentication (`PasswordAuthentication no` in `/etc/ssh/sshd_config`). Use SSH keys only. | [ ] |
| 2.4 | **UFW Host Firewall** | Enable UFW: `ufw default deny incoming`, `ufw allow 22/tcp`, `ufw allow 80/tcp`, `ufw allow 443/tcp`. | [ ] |
| 2.5 | **Fail2ban Protection** | Ensure `fail2ban` is running to protect against SSH brute force and Nginx repeated abuse. | [ ] |

---

## 3. Nginx Reverse Proxy & TLS Configuration

| # | Check | Verification Method | Status |
|---|-------|---------------------|:------:|
| 3.1 | **TLS 1.2 & TLS 1.3 Only** | Verify `ssl_protocols TLSv1.2 TLSv1.3;`. Older SSLv3, TLSv1.0, and TLSv1.1 must be disabled. | [ ] |
| 3.2 | **HTTP to HTTPS 301 Redirect** | Confirm `curl -I http://yourdomain.com` returns `HTTP/1.1 301 Moved Permanently` to `https://`. | [ ] |
| 3.3 | **HTTP Strict Transport Security (HSTS)** | Verify header: `Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"`. | [ ] |
| 3.4 | **Security Response Headers** | Check for `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `X-XSS-Protection: 1; mode=block`. | [ ] |
| 3.5 | **Certbot Automated Renewal** | Run `certbot renew --dry-run` to test automated renewal hook via systemd timer. | [ ] |
| 3.6 | **SSE / Streaming LLM Buffer Bypass** | Confirm `proxy_buffering off;` and `proxy_read_timeout 600s;` in Nginx for real-time LLM token generation. | [ ] |
| 3.7 | **API Rate Limiting** | Test rate limit zones: 5 req/min on `/api/auth/`, 2 req/min on `/api/pipeline/start`, 15 req/s on general API. | [ ] |

---

## 4. Application Sandboxing & Systemd

| # | Check | Verification Method | Status |
|---|-------|---------------------|:------:|
| 4.1 | **Dedicated Service User** | Service runs as unprivileged user `leastgen` with shell `/usr/sbin/nologin`. Never run as `root`. | [ ] |
| 4.2 | **NoNewPrivileges** | Set `NoNewPrivileges=true` in systemd service to prevent SUID privilege escalation. | [ ] |
| 4.3 | **Filesystem Protection** | Enable `ProtectSystem=full` and `ProtectHome=true`. Restrict writes to `ReadWritePaths`. | [ ] |
| 4.4 | **Private Temporary Files** | Set `PrivateTmp=true` to isolate `/tmp` and `/var/tmp` namespaces. | [ ] |
| 4.5 | **Process Restart Policy** | Verify `Restart=always`, `RestartSec=5s`, and burst limits in `leastgen.service`. | [ ] |
| 4.6 | **File Descriptor Limits** | Ensure `LimitNOFILE=65535` is set in systemd service to handle concurrent HTTP connections. | [ ] |

---

## 5. Database Integrity & Backup Strategy

| # | Check | Verification Method | Status |
|---|-------|---------------------|:------:|
| 5.1 | **SQLite WAL Mode** | Verify database executes in Write-Ahead Logging mode (`PRAGMA journal_mode=WAL;`). | [ ] |
| 5.2 | **Database File Permissions** | Ensure `/opt/leastgen/data/ideaflow.db` is owned by `leastgen:leastgen` with `chmod 600` or `640`. | [ ] |
| 5.3 | **Automated Daily Backups** | Configure cron job using `.backup` command in sqlite3: `sqlite3 /opt/leastgen/data/ideaflow.db ".backup '/opt/leastgen/backups/backup-\$(date +\%F).db'"`. | [ ] |
| 5.4 | **Offsite Snapshot Storage** | Sync database backups to Alibaba Cloud OSS (Object Storage Service) or cold storage. | [ ] |

---

## 6. Verification Commands Quick Reference

```bash
# 1. Check systemd service status and sandboxing
systemctl status leastgen.service
systemd-analyze security leastgen.service

# 2. Check Nginx configuration syntax and reload
nginx -t && systemctl reload nginx

# 3. Test HTTP to HTTPS redirect
curl -I http://research.example.com

# 4. Test live health check
curl -s https://research.example.com/api/health | jq .

# 5. Test Rate Limiting on Auth
for i in {1..7}; do curl -s -o /dev/null -w "%{http_code}\n" https://research.example.com/api/auth/login; done

# 6. View real-time application logs
journalctl -u leastgen.service -f --output=cat
```
