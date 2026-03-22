#!/bin/zsh
set -e  # Exit immediately if any command fails

# ssh -R 2222:localhost:22 -i ~/.ssh/cloud-computing ubuntu@34.38.233.180

# kubectl get pods -o wide
#  -> MEMCACHED IP
MEMCACHED_IP=100.96.4.2

# kubectl get nodes -o wide
# INTERNAL_AGENT_IP <- Internal IP of the client-agent node 
INTERNAL_AGENT_IP=10.0.16.3 


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

echo "=== Setting up memcache-perf ==="
cd ~

if [ -d "memcache-perf/.git" ]; then
    echo "Repo exists, updating..."
    cd memcache-perf
    git fetch --all
else
    echo "Cloning fresh repo..."
    git clone https://github.com/shaygalon/memcache-perf.git
    cd memcache-perf
fi

echo "=== Checking out specific commit 0afbe9b ==="
git checkout 0afbe9b

echo "=== Building memcache-perf ==="
make

echo "✅ Setup and build completed successfully!"

echo "Using MEMCACHED_IP=$MEMCACHED_IP"
echo "Using INTERNAL_AGENT_IP=$INTERNAL_AGENT_IP"

echo "=== Loading memcached database (load-only) ==="
./mcperf -s "$MEMCACHED_IP" --loadonly


#  THIS COMMAND STARTS A RUN
# replace IP's before running

# ./mcperf -s MEMCACHED_IP -a INTERNAL_AGENT_IP --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 5 -w 2 --scan 5000:80000:5000

# launch the agent too (run this on agent ssh)
# ./mcperf -T 8 -A