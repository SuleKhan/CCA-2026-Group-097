#!/bin/bash
set -e

# ssh -o StrictHostKeyChecking=no -i ~/.ssh/cloud-computing ubuntu@

# copy controller:
# scp -o StrictHostKeyChecking=no -i ~/.ssh/cloud-computing measurements_1.py ubuntu@10.0.16.4:~

#MEMCACHED_INTERNAL_IP=$1
MEMCACHED_INTERNAL_IP="10.0.16.3"

# Update & install
sudo apt update
sudo apt install -y memcached libmemcached-tools

CONFIG_FILE="/etc/memcached.conf"

# Backup once (safe practice)
if [ ! -f "${CONFIG_FILE}.bak" ]; then
  sudo cp $CONFIG_FILE ${CONFIG_FILE}.bak
fi

# Set memory to 1024 MB
sudo sed -i 's/^-m .*/-m 1024/' $CONFIG_FILE

sudo sed -i "s/^-l .*/-l ${MEMCACHED_INTERNAL_IP}/" $CONFIG_FILE

# Set default of 1 thread (add if not present)
if grep -q "^-t" $CONFIG_FILE; then
  echo SET MEMCACHE TO USE THREE THREADS
  sudo sed -i 's/^-t .*/-t 3/' $CONFIG_FILE
else
  echo "-t 3" | sudo tee -a $CONFIG_FILE
fi

# Restart service
sudo systemctl restart memcached

# Verify
sudo systemctl status memcached --no-pager

sudo apt update
sudo apt install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install docker psutil
sudo apt install -y docker.io
sudo systemctl start docker
sudo systemctl enable docker
docker --version
sudo usermod -aG docker $USER
newgrp docker
docker ps
source venv/bin/activate