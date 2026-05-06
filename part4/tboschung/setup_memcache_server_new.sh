#!/bin/bash
set -euo pipefail

# set up reverse tunnel again

MEMCACHED_INTERNAL_IP="10.0.16.5"

echo "=== Updating system ==="
sudo apt update

echo "=== Installing memcached ==="
sudo apt install -y memcached libmemcached-tools

CONFIG_FILE="/etc/memcached.conf"

echo "=== Backing up memcached config (once) ==="
if [ ! -f "${CONFIG_FILE}.bak" ]; then
  sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
fi

echo "=== Configuring memcached ==="
sudo sed -i 's/^-m .*/-m 1024/' "$CONFIG_FILE"
sudo sed -i "s/^-l .*/-l ${MEMCACHED_INTERNAL_IP}/" "$CONFIG_FILE"

if grep -q "^-t" "$CONFIG_FILE"; then
  sudo sed -i 's/^-t .*/-t 8/' "$CONFIG_FILE"
else
  echo "-t 8" | sudo tee -a "$CONFIG_FILE" > /dev/null
fi

echo "=== Restarting memcached ==="
sudo systemctl restart memcached
sudo systemctl status memcached --no-pager -l || true

echo "=== Installing Python tooling ==="
sudo apt install -y python3-venv python3-pip

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install docker psutil
sudo apt install python3-docker
sudo apt install python3-psutil

echo "=== Installing Docker (clean + safe check) ==="

# Install Docker
sudo apt install -y docker.io

# Enable but DO NOT assume it starts correctly
sudo systemctl enable docker

echo "=== Starting Docker safely ==="
sudo systemctl reset-failed docker || true
sudo systemctl start docker

echo "=== Checking Docker status ==="
if ! systemctl is-active --quiet docker; then
  echo "❌ Docker failed to start. Showing logs:"
  journalctl -u docker.service --no-pager -n 80
  exit 1
fi

docker --version
docker ps || true

echo "=== Adding user to docker group (requires re-login to take effect) ==="
sudo usermod -aG docker "$USER"

echo "NOTE: Run 'newgrp docker' or re-login manually if needed"