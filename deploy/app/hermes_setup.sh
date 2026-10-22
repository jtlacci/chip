#!/bin/bash
# The Hermes agent for CHIP's chat. Run as root on the app server; safe to run again.
#
# - Hermes (NousResearch/hermes-agent, pinned to HERMES_COMMIT) goes in /opt/chip-hermes,
#   owned by root. Its home (config, sessions) is /var/lib/chip-hermes, owned by "hermes".
# - chip-hermes.service runs `hermes gateway run` with only the API server, on
#   127.0.0.1:8642, behind a key (/etc/chip/hermes-key for the CHIP server).
# - The "hermes" user can read none of CHIP's data or secrets; the kernel blocks its
#   traffic to private, link-local (the cloud metadata address) and multicast
#   addresses. It reaches CHIP's tools only through the tool socket
#   (/run/chip-tools/tools.sock, group chiptools).
# - Its tools: CHIP's tools and the web. No terminal, files or code (config.yaml).
#
# This installs and starts Hermes; CHIP uses it only with CHIP_AGENT_ENGINE=hermes in
# /etc/chip/env (pass --use to set it).
set -euo pipefail
HERMES_COMMIT=41dfcf2c1bd7146d4ad46aea08b7dd007900602e
OPT=/opt/chip-hermes
HOME_DIR=/var/lib/chip-hermes
APP=/opt/chip/repo
CONF=/etc/chip
REGION="$(grep '^CHIP_SSM_REGION=' $CONF/env | cut -d= -f2)"
ssm_get() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text 2>/dev/null || true; }
say() { echo "[hermes $(date -u +%FT%TZ)] $*"; }

say "users"
getent group chiptools >/dev/null || groupadd --system chiptools
id hermes >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin hermes
usermod -aG chiptools hermes
usermod -aG chiptools chip

say "code ($HERMES_COMMIT)"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q python3-venv git >/dev/null
install -d -m 755 -o root -g root "$OPT"
[ -x "$OPT/uv/bin/uv" ] || { python3 -m venv "$OPT/uv" && "$OPT/uv/bin/pip" install -q "uv==0.11.32"; }
if [ ! -d "$OPT/hermes-agent/.git" ]; then
  git clone -q https://github.com/NousResearch/hermes-agent.git "$OPT/hermes-agent"
fi
git -C "$OPT/hermes-agent" fetch -q origin "$HERMES_COMMIT" 2>/dev/null || git -C "$OPT/hermes-agent" fetch -q origin
git -C "$OPT/hermes-agent" checkout -q --force "$HERMES_COMMIT"
# The lock file needs Python 3.14: uv installs its own copy under $OPT/python.
export UV_CACHE_DIR="$OPT/uv-cache" UV_PYTHON_INSTALL_DIR="$OPT/python"
"$OPT/uv/bin/uv" python install -q 3.14
(cd "$OPT/hermes-agent" && "$OPT/uv/bin/uv" sync -q --frozen --no-dev --managed-python --python 3.14 \
   --extra messaging --extra mcp --extra anthropic --extra firecrawl)
chmod -R a+rX "$OPT"
PYTHON="$OPT/hermes-agent/.venv/bin/python"

say "home and secrets"
install -d -m 700 -o hermes -g hermes "$HOME_DIR"
sed -e "s|@@PYTHON@@|$PYTHON|" -e "s|@@APP@@|$APP|" "$APP/deploy/app/hermes/config.yaml" > "$HOME_DIR/config.yaml"
cp "$APP/deploy/app/hermes/SOUL.md" "$HOME_DIR/SOUL.md"
umask 077
[ -s "$CONF/hermes-key" ] || python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$CONF/hermes-key"
chown chip:chip "$CONF/hermes-key"; chmod 600 "$CONF/hermes-key"
FIRECRAWL="$(ssm_get /chip/firecrawl/key)"; [ "$FIRECRAWL" = "None" ] && FIRECRAWL=""
{
  echo "ANTHROPIC_API_KEY=$(cat "$CONF/anthropic-key")"
  echo "API_SERVER_KEY=$(cat "$CONF/hermes-key")"
  [ -n "$FIRECRAWL" ] && echo "FIRECRAWL_API_KEY=$FIRECRAWL"
  true
} > "$HOME_DIR/.env"
unset FIRECRAWL
chown -R hermes:hermes "$HOME_DIR"
umask 022

say "services"
cat > /etc/systemd/system/chip-hermes.service <<EOF
[Unit]
Description=CHIP's agent (Hermes, API server only)
After=network-online.target chip.service
Wants=network-online.target

[Service]
User=hermes
Group=hermes
WorkingDirectory=$OPT/hermes-agent
Environment=HERMES_HOME=$HOME_DIR
Environment=API_SERVER_ENABLED=true
Environment=API_SERVER_HOST=127.0.0.1
Environment=API_SERVER_PORT=8642
ExecStart=$PYTHON $OPT/hermes-agent/hermes gateway run
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=$HOME_DIR
InaccessiblePaths=/var/lib/chip /etc/chip
IPAddressAllow=127.0.0.0/8
IPAddressDeny=link-local multicast 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 172.16.0.0/12 192.168.0.0/16 fc00::/7
MemoryMax=1500M
TasksMax=300

[Install]
WantedBy=multi-user.target
EOF
# The CHIP server serves the tools on /run/chip-tools/tools.sock.
install -d -m 755 /etc/systemd/system/chip.service.d
cat > /etc/systemd/system/chip.service.d/tools.conf <<EOF
[Service]
RuntimeDirectory=chip-tools
RuntimeDirectoryMode=0755
EOF
for line in CHIP_TOOLS_SOCKET=/run/chip-tools/tools.sock CHIP_TOOLS_GROUP=chiptools \
            CHIP_HERMES_KEY_FILE=$CONF/hermes-key CHIP_HERMES_URL=http://127.0.0.1:8642; do
  grep -q "^${line%%=*}=" $CONF/env || echo "$line" >> $CONF/env
done
if [ "${1:-}" = "--use" ]; then
  grep -q '^CHIP_AGENT_ENGINE=' $CONF/env && sed -i 's/^CHIP_AGENT_ENGINE=.*/CHIP_AGENT_ENGINE=hermes/' $CONF/env \
    || echo "CHIP_AGENT_ENGINE=hermes" >> $CONF/env
fi
# Deploys restart Hermes too (it starts chip_mcp.py from the repo).
grep -q chip-hermes /usr/local/bin/chip-deploy || sed -i \
  's|^  systemctl restart chip$|  systemctl restart chip\n  if systemctl is-enabled --quiet chip-hermes 2>/dev/null; then systemctl restart chip-hermes; fi|' \
  /usr/local/bin/chip-deploy
systemctl daemon-reload
systemctl enable chip-hermes.service >/dev/null
systemctl restart chip.service
systemctl restart chip-hermes.service
for i in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:8642/health >/dev/null && break; sleep 2; done
curl -s -m 2 http://127.0.0.1:8642/health >/dev/null && say "running" || { say "Hermes did not start"; journalctl -u chip-hermes -n 20 --no-pager; exit 1; }
