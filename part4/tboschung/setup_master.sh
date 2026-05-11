sudo apt-get update
sudo apt-get install -y python3 python3-pip
sudo apt install python3-fabric

MEMCACHED_IP=34.62.208.119 
MEASURE_IP=35.205.70.192
AGENT_IP=34.14.112.126

# need to run this first:
#   ./setup_server_ssh.sh ubuntu@master_ip ubuntu@memcache_ip
#   ./setup_server_ssh.sh ubuntu@master_ip ubuntu@measure_ip
#   ./setup_server_ssh.sh ubuntu@master_ip ubuntu@agent_ip

ssh -i ~/.ssh/master_node_key ubuntu@${MEMCACHED_IP} "hostname && pidof memcached"
ssh -i ~/.ssh/master_node_key ubuntu@${AGENT_IP} "hostname"
ssh -i ~/.ssh/master_node_key ubuntu@${MEASUSRE_IP} "hostname"

# If you get a password error, configure passwordless sudo for just taskset on the memcache server:
# sudo visudo
# ubuntu ALL=(ALL) NOPASSWD: /usr/bin/taskset
# then test again


ssh -i ~/.ssh/master_node_key ubuntu@${MEMCACHED_IP} "PID=\$(pidof memcached | awk '{print \$1}'); sudo -n taskset -a -cp 0 \$PID"