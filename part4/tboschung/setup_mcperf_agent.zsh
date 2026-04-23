#!/bin/zsh

# ssh -o StrictHostKeyChecking=no -i ~/.ssh/cloud-computing ubuntu@

set -e  # Exit immediately if any command fails

echo "=== Updating package lists ==="
sudo apt-get update

echo "=== Installing required dependencies ==="
sudo apt-get install -y libevent-dev libzmq3-dev git make g++

echo "=== Updating sources list for build dependencies ==="
sudo sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources

echo "=== Updating package lists again ==="
sudo apt-get update

echo "=== Installing build dependencies for memcached ==="
sudo apt-get build-dep -y memcached

echo "=== Cloning memcache-perf-dynamic repository ==="
cd ~
if [ -d "memcache-perf-dynamic/.git" ]; then
    echo "Repo exists, updating..."
    cd memcache-perf-dynamic
    git fetch --all
else
    echo "Cloning fresh repo..."
    git clone https://github.com/eth-easl/memcache-perf-dynamic.git
    cd memcache-perf-dynamic
fi

echo "=== Building memcache-perf-dynamic ==="
make

echo "✅ Setup and build completed successfully!"

echo "=== Launching mcperf agent ==="
echo "Starting client-agent with 8 threads..."
./mcperf -T 8 -A
