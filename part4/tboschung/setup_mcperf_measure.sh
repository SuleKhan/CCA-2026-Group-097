#!/bin/bash
set -e  # Exit immediately if any command fails

# ssh -o StrictHostKeyChecking=no -i ~/.ssh/cloud-computing ubuntu@

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

echo "=== Setting up memcache-perf-dynamic ==="
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

./mcperf -s INTERNAL_MEMCACHED_IP --loadonly

# ./mcperf -s 10.0.16.4 -a 10.0.16.6 --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 2 --scan 5000:125000:10000

# ./mcperf -s INTERNAL_MEMCACHED_IP --loadonly
# ./mcperf -s 10.0.16.6 --loadonly

#  ./mcperf -s INTERNAL_MEMCACHED_IP -a INTERNAL_AGENT_IP --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1800 --qps_interval 15 --qps_min 5000 --qps_max 110000
#  ./mcperf -s 10.0.16.4 -a 10.0.16.3 --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 100 --qps_interval 15 --qps_min 5000 --qps_max 110000


# ./mcperf -s 10.0.16.3 -a 10.0.16.5 --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 10 --scan 5000:125000:10000