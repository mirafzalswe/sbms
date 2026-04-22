#!/usr/bin/env bash
# ============================================================
# One-shot deploy to remote VPS
# Usage:
#   ./deploy.sh                       # деплой на 164.92.174.249 (root)
#   SERVER=1.2.3.4 USER=ubuntu ./deploy.sh
# ============================================================
set -euo pipefail

SERVER="${SERVER:-164.92.174.249}"
USER="${USER:-root}"
APP_DIR="/opt/sbms-test"
DOMAIN="${DOMAIN:-}"            # опционально: ваш домен для Nginx
PORT="${PORT:-5000}"

echo "==> Deploy to ${USER}@${SERVER}:${APP_DIR} (port ${PORT})"

if [[ ! -f .env ]]; then
    echo "ERROR: .env не найден. Скопируй .env.example и заполни:"
    echo "    cp .env.example .env && nano .env"
    exit 1
fi

# --- 1. Копируем проект rsync'ом ---
echo "==> Syncing files via rsync..."
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '.DS_Store' \
    --exclude 'node_modules' \
    --exclude 'test_history' \
    --exclude '*.pyc' \
    ./ "${USER}@${SERVER}:${APP_DIR}/"

# --- 2. Ставим зависимости и поднимаем сервис ---
echo "==> Installing & starting service on remote..."
ssh "${USER}@${SERVER}" bash -s <<REMOTE
set -euo pipefail

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx curl

cd ${APP_DIR}

# venv
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

mkdir -p test_history

# systemd unit
cat > /etc/systemd/system/sbms-test.service <<'UNIT'
[Unit]
Description=UCELL SBMS Test Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn server:app --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile -
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sbms-test
systemctl restart sbms-test
sleep 2
systemctl --no-pager status sbms-test | head -20

# firewall
if command -v ufw >/dev/null 2>&1; then
    ufw allow ${PORT}/tcp || true
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
fi

# Nginx reverse proxy (если задан DOMAIN или просто на :80)
cat > /etc/nginx/sites-available/sbms-test <<NGINX
server {
    listen 80;
    server_name ${DOMAIN:-_};
    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/sbms-test /etc/nginx/sites-enabled/sbms-test
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo
echo "===================================="
echo "  DEPLOY OK"
echo "  App:    http://${SERVER}:${PORT}/"
echo "  Nginx:  http://${SERVER}/"
if [ -n "${DOMAIN}" ]; then
    echo "  Domain: http://${DOMAIN}/"
fi
echo "  Logs:   journalctl -u sbms-test -f"
echo "===================================="
REMOTE

echo
echo "==> Проверяю доступность снаружи..."
sleep 2
curl -s -o /dev/null -w "HTTP %{http_code} (dashboard)\n" "http://${SERVER}/" || true
curl -s "http://${SERVER}/api/config" || true
echo
echo "Готово. Открой в браузере: http://${SERVER}/"
