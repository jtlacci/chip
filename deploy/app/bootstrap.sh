#!/bin/bash
# CHIP's web app server: run once by cloud-init as root (EC2 user data).
#
# - Caddy serves HTTPS for $HOST (a certificate from Let's Encrypt) and passes
#   every request to the CHIP server on 127.0.0.1:8000.
# - The CHIP server runs from a clone of main, as the user "chip".
# - chip-deploy.timer pulls main every 2 minutes and restarts the server when
#   main changed. main only changes through a pull request whose checks pass.
# - Secrets come from SSM Parameter Store (/chip/...) through the instance role.
#   They are files readable only by "chip", never in the repository.
set -euo pipefail
exec > >(tee -a /var/log/chip-bootstrap.log) 2>&1

REGION="@@REGION@@"
HOST="@@HOST@@"
REPO="https://github.com/jtlacci/chip.git"
APP=/opt/chip
DATA=/var/lib/chip
CONF=/etc/chip

say() { echo "[chip $(date -u +%FT%TZ)] $*"; }
ssm_get() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text 2>/dev/null || true; }
status() { aws ssm put-parameter --region "$REGION" --name /chip/app/status --type String --value "$1" --overwrite >/dev/null || true; say "status: $1"; }

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 nodejs npm caddy unzip curl
if ! command -v aws >/dev/null; then
  curl -sSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscli.zip
  unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscli.zip
fi
status "installing"

id chip >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin chip
install -d -m 700 -o chip -g chip "$DATA" "$DATA/secrets" "$DATA/capture"
install -d -m 750 -o root -g chip "$CONF"
install -d -m 755 -o chip -g chip "$APP"
[ -d "$APP/repo/.git" ] || sudo -u chip git clone -q "$REPO" "$APP/repo"
(cd "$APP/repo/bricks/web/app" && sudo -u chip node build.js)

umask 077
CLIENT_ID="$(ssm_get /chip/google/client-id)"
ssm_get /chip/google/client-secret > "$CONF/google-client-secret"
TOKEN="$(ssm_get /chip/google/refresh-token)"
if [ -n "$TOKEN" ] && [ "$TOKEN" != "None" ]; then printf '%s' "$TOKEN" > "$DATA/secrets/google-refresh-token"; fi
unset TOKEN
chown chip:chip "$CONF/google-client-secret" "$DATA/secrets"/* 2>/dev/null || true
cat > "$CONF/env" <<EOF
CHIP_PUBLIC_URL=https://$HOST
CHIP_SECURE=1
CHIP_DB=$DATA/chip.db
CHIP_GOOGLE_CLIENT_ID=$CLIENT_ID
CHIP_GOOGLE_CLIENT_SECRET_FILE=$CONF/google-client-secret
CHIP_GOOGLE_TOKEN_FILE=$DATA/secrets/google-refresh-token
CHIP_SSM_REGION=$REGION
BRICK_CAPTURE_DIR=$DATA/capture
EOF
chown root:chip "$CONF/env" && chmod 640 "$CONF/env"
umask 022

cat > /etc/caddy/Caddyfile <<EOF
$HOST {
  encode gzip
  reverse_proxy 127.0.0.1:8000
}
EOF
systemctl restart caddy

cat > /etc/systemd/system/chip.service <<EOF
[Unit]
Description=CHIP web app
After=network-online.target
Wants=network-online.target

[Service]
User=chip
Group=chip
WorkingDirectory=$APP/repo
EnvironmentFile=$CONF/env
ExecStart=/usr/bin/python3 server/serve.py --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$DATA $APP
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/chip-deploy <<'EOF'
#!/bin/bash
# Pull main; when it changed, rebuild the app and restart the server.
set -euo pipefail
cd /opt/chip/repo
sudo -u chip git fetch -q origin main
if [ "$(sudo -u chip git rev-parse HEAD)" != "$(sudo -u chip git rev-parse origin/main)" ]; then
  sudo -u chip git reset -q --hard origin/main
  (cd bricks/web/app && sudo -u chip node build.js)
  systemctl restart chip
  echo "deployed $(sudo -u chip git rev-parse --short HEAD)"
fi
EOF
chmod 755 /usr/local/bin/chip-deploy
cat > /etc/systemd/system/chip-deploy.service <<EOF
[Unit]
Description=Deploy CHIP's main branch
[Service]
Type=oneshot
ExecStart=/usr/local/bin/chip-deploy
EOF
cat > /etc/systemd/system/chip-deploy.timer <<EOF
[Unit]
Description=Deploy CHIP's main branch every 2 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now chip.service chip-deploy.timer
sleep 5
systemctl is-active --quiet chip && status "running at https://$HOST" || status "error: the CHIP server did not start"
