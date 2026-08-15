#!/usr/bin/env bash
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Khaabo â€” VPS bootstrap for Oracle Cloud Always Free (Ubuntu 22.04/24.04).
#
# Run this once as root on a fresh VM. It installs Docker + compose, creates
# 1GB of swap so Celery's memory spikes don't OOM-kill anything, sets up the
# firewall to only expose 22/80/443, and adds fail2ban for SSH brute-force.
#
# After this finishes, you can `git clone` the repo, fill `.env`, and run:
#   docker compose -f docker-compose.yml -f docker-compose.free.yml up -d
#
# Usage:
#   sudo bash scripts/prod-bootstrap.sh
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (try: sudo bash $0)" >&2
    exit 1
fi

echo "==> updating apt + base toolkit"
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg ufw fail2ban unattended-upgrades

# â”€â”€ Docker + compose v2 (from Docker's repo, not Ubuntu's stale one) â”€â”€â”€â”€â”€â”€â”€â”€
echo "==> installing docker"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Use lsb_release if available, fall back to a known-good codename.
CODENAME=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME:-jammy}}")
cat >/etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable
EOF

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Allow the default `ubuntu` user to use docker without sudo.
usermod -aG docker ubuntu || true

echo "==> configuring default runtime + logging limits"
mkdir -p /etc/docker
cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "8m", "max-file": "3" },
  "live-restore": true,
  "userland-proxy": false
}
JSON
systemctl restart docker
systemctl enable docker

# â”€â”€ Swap (essential: 1GB VM cannot run Celery + uvicorn + Caddy without it) â”€
if [[ ! -f /swapfile ]]; then
    echo "==> creating 1GB swap"
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >>/etc/fstab
    # Kernel: prefer keeping app memory, only swap under pressure.
    echo 'vm.swappiness=10' >>/etc/sysctl.d/99-khaabo.conf
else
    echo "==> swap already present, skipping"
fi

# â”€â”€ Firewall â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "==> firewall (ufw)"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp        comment 'SSH'
ufw allow 80/tcp        comment 'HTTP (Caddy ACME + redirect)'
ufw allow 443/tcp       comment 'HTTPS'
ufw allow 443/udp       comment 'HTTP/3 (Caddy auto)'
ufw --force enable

# â”€â”€ SSH hardening â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "==> sshd: disable root login, disable password auth"
SSHD=/etc/ssh/sshd_config
if ! grep -q '^PermitRootLogin no' "$SSHD"; then
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' "$SSHD"
fi
if ! grep -q '^PasswordAuthentication no' "$SSHD"; then
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD"
fi
systemctl reload ssh

# â”€â”€ fail2ban: ship SSH rate-limit + banned addresses â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "==> fail2ban"
cat >/etc/fail2ban/jail.local <<'JAIL'
[sshd]
enabled = true
port    = 22
maxretry = 5
bantime = 1h
findtime = 10m
JAIL
systemctl restart fail2ban
systemctl enable fail2ban

# â”€â”€ Auto security updates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "==> unattended-upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null

# â”€â”€ khaabo deploy directory scaffold â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo "==> /opt/khaabo"
mkdir -p /opt/khaabo
# The bootstrap only creates the directory; clone/upload the repo onto it.
# This serves as a reminder of the canonical path used by deploy.yml.

echo
echo "âœ… bootstrap done. As the 'ubuntu' user, run:"
echo "   cd /opt/khaabo && git clone <repo> . && cp .env.example .env"
echo "   # fill .env, then:"
echo "   docker compose -f docker-compose.yml -f docker-compose.free.yml up -d"
