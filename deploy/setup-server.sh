#!/usr/bin/env bash
# One-time server setup (Ubuntu/Debian), run as root after the `deploy` user exists:
#   Docker + Compose plugin, 2 GB swap, UFW (22/80/443 only), unattended security upgrades,
#   /opt/docagent, backup/log directories, cron and logrotate. Safe to re-run.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
DEPLOY_USER="${DEPLOY_USER:-deploy}"
export DEBIAN_FRONTEND=noninteractive

# --- Docker Engine + Compose plugin (official apt repository) ---
if ! command -v docker >/dev/null; then
  . /etc/os-release
  apt-get update -q
  apt-get install -yq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/$ID ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
usermod -aG docker "$DEPLOY_USER"
# Rotate container logs by default too (compose also sets this per service).
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
  systemctl restart docker
fi

# --- 2 GB swap ---
if ! swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -q -w vm.swappiness=10
echo 'vm.swappiness=10' > /etc/sysctl.d/99-docagent-swap.conf

# --- Firewall: SSH, HTTP, HTTPS only ---
apt-get install -yq ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp # HTTP/3
ufw --force enable

# --- Unattended security upgrades ---
apt-get install -yq unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
systemctl enable --now unattended-upgrades

# --- App directories, cron, logs ---
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 750 /opt/docagent
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 700 /var/backups/docagent
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 750 /var/log/docagent
cat > /etc/logrotate.d/docagent <<'CONF'
/var/log/docagent/*.log {
  weekly
  rotate 8
  compress
  missingok
  notifempty
}
CONF
if [ -f /opt/docagent/deploy/cron ]; then
  install -m 644 /opt/docagent/deploy/cron /etc/cron.d/docagent
fi
echo "setup done"
