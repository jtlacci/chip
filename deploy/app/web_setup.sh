#!/bin/bash
# The web worker for the browser brick (server/web_worker.py). Run as root on the
# app server; bootstrap.sh runs it, and it is safe to run again.
#
# - Playwright and Chromium go in /opt/chip-web, owned by root.
# - chip-web.service runs the worker as "chipweb", a user that can read none of
#   CHIP's data or secrets (/var/lib/chip and /etc/chip are inaccessible to it).
# - The kernel blocks the worker's traffic to private, loopback, link-local
#   (the cloud metadata address too) and multicast addresses. Only the local DNS
#   stub (127.0.0.53) is allowed. A page cannot reach the server or its network.
# - The CHIP server talks to it on /run/chip-web/web.sock (group chipweb).
set -euo pipefail
WEB=/opt/chip-web
PLAYWRIGHT_VERSION=1.49.1

export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q python3-venv
id chipweb >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin chipweb
usermod -aG chipweb chip
install -d -m 755 -o root -g root "$WEB"
[ -x "$WEB/venv/bin/python" ] || python3 -m venv "$WEB/venv"
"$WEB/venv/bin/pip" install -q "playwright==$PLAYWRIGHT_VERSION"
PLAYWRIGHT_BROWSERS_PATH="$WEB/browsers" "$WEB/venv/bin/python" -m playwright install --with-deps chromium
chmod -R a+rX "$WEB"

cat > /etc/systemd/system/chip-web.service <<EOF
[Unit]
Description=CHIP web worker (the browser brick's browser)
After=network-online.target
Wants=network-online.target

[Service]
User=chipweb
Group=chipweb
Environment=CHIP_WEB_SOCKET=/run/chip-web/web.sock
Environment=CHIP_WEB_PYTHON=$WEB/venv/bin/python
Environment=PLAYWRIGHT_BROWSERS_PATH=$WEB/browsers
RuntimeDirectory=chip-web
RuntimeDirectoryMode=0750
ExecStart=$WEB/venv/bin/python /opt/chip/repo/server/web_worker.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
InaccessiblePaths=/var/lib/chip /etc/chip
IPAddressAllow=127.0.0.53
IPAddressDeny=localhost link-local multicast 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 172.16.0.0/12 192.168.0.0/16 fc00::/7
MemoryMax=1200M
TasksMax=200

[Install]
WantedBy=multi-user.target
EOF

grep -q '^CHIP_WEB_SOCKET=' /etc/chip/env || echo 'CHIP_WEB_SOCKET=/run/chip-web/web.sock' >> /etc/chip/env
systemctl daemon-reload
systemctl enable chip-web.service
systemctl restart chip-web.service
systemctl restart chip.service   # the CHIP server's user is now in the group chipweb
systemctl is-active --quiet chip-web && echo "web worker running" || { echo "web worker did not start"; exit 1; }
