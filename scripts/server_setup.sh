#!/bin/bash
# One-time server bootstrap — run on a fresh EC2 instance
set -e

APP_DIR="$HOME/anjo"
DOMAIN="${DOMAIN:-your-domain.com}"  # Set DOMAIN env var or pass via bootstrap workflow

echo "==> Installing system packages"
sudo apt-get update -q
sudo apt-get install -y -q python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx
sudo apt-get clean
sudo rm -rf /var/lib/apt/lists/*

echo "==> Disk space"
df -h /

echo "==> Cleaning old pip cache and venv"
rm -rf ~/.cache/pip "$APP_DIR/.venv"

echo "==> Setting up Python environment"
cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing CPU-only PyTorch (avoids 1.5GB CUDA download)"
pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

echo "==> Installing remaining dependencies"
pip install --no-cache-dir -e .

echo "==> Writing .env"
if [ ! -f .env ]; then
  ANJO_SECRET_VAL=$(openssl rand -hex 32)
  cat > .env <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
ANJO_SECRET=${ANJO_SECRET_VAL}
ANJO_ADMIN_SECRET=${ANJO_ADMIN_SECRET}
RESEND_API_KEY=${RESEND_API_KEY}
ANJO_BASE_URL=https://${DOMAIN}
ANJO_ENV=production
PADDLE_SANDBOX=false
EOF
  echo ".env created"
else
  echo ".env already exists — updating ANJO_ADMIN_SECRET"
  sed -i "s|^ANJO_ADMIN_SECRET=.*|ANJO_ADMIN_SECRET=${ANJO_ADMIN_SECRET}|" .env
fi

echo "==> Creating systemd service"
sudo tee /etc/systemd/system/anjo.service > /dev/null <<EOF
[Unit]
Description=Anjo
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn anjo.dashboard.app:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable anjo
sudo systemctl restart anjo

echo "==> Configuring nginx"
sudo tee /etc/nginx/sites-available/anjo > /dev/null <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
        proxy_buffering off;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/anjo /etc/nginx/sites-enabled/anjo
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "==> Obtaining SSL certificate"
sudo certbot --nginx -d ${DOMAIN} \
  --non-interactive --agree-tos --email admin@${DOMAIN} \
  --redirect || echo "WARNING: SSL cert failed — site running on HTTP for now"

sudo systemctl status anjo --no-pager
echo "==> Done — https://${DOMAIN} is live"
