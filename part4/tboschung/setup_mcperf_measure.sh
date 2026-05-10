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

lient-agent-z8dk            Ready    node            64m   v1.31.5   10.0.16.5     34.62.183.137   Ubuntu 24.04.1 LTS   6.8.0-1021-gcp   containerd://1.7.29
client-measure-5wzr          Ready    node            64m   v1.31.5   10.0.16.3     34.38.93.213    Ubuntu 24.04.1 LTS   6.8.0-1021-gcp   containerd://1.7.29
master-europe-west1-b-9whq   Ready    control-plane   66m   v1.31.5   10.0.16.6     34.53.176.22    Ubuntu 24.04.1 LTS   6.8.0-1021-gcp   containerd://1.7.29
memcache-server-5gqv         Ready    node            64m   v1.31.5   10.0.16.4     34.77.140.213   Ubuntu 24.04.1 LTS   6.8.0-1021-gcp   containerd://2.2.1


# ./mcperf -s 10.0.16.4 --loadonly 
# ./mcperf -s 10.0.16.4  -a 10.0.16.45 --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1800 --qps_interval 15 --qps_min 5000 --qps_max 110000