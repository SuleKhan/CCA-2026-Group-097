#!/bin/zsh

#  ssh -i ~/.ssh/cloud-computing ubuntu@34.89.236.52

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

echo "=== Cloning memcache-perf repository ==="
cd ~
git clone https://github.com/shaygalon/memcache-perf.git
cd memcache-perf

echo "=== Checking out specific commit 0afbe9b ==="
git checkout 0afbe9b

echo "=== Building memcache-perf ==="
make

echo "✅ Setup and build completed successfully!"

echo "=== Launching the mcperf memcached client load agent with 8 threads ==="
./mcperf -T 8 -A