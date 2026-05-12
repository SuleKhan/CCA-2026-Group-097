sudo apt-get update
sudo apt-get install -y python3 python3-pip
sudo apt install python3-fabric

MEMCACHED_IP=35.241.228.86 
# MEASURE_IP=35.190.198.73
# AGENT_IP=34.62.22.103 

# need to run this first:
#   ./setup_server_ssh.sh ubuntu@34.62.213.156 ubuntu@35.241.228.86 
#   ./setup_server_ssh.sh ubuntu@master_ip ubuntu@memcache_ip

#not anymore:
#   ./setup_server_ssh.sh ubuntu@master_ip ubuntu@measure_ip
#   ./setup_server_ssh.sh ubuntu@master_ip ubuntu@agent_ip

ssh -i ~/.ssh/master_node_key ubuntu@${MEMCACHED_IP} "hostname && pidof memcached"
# ssh -i ~/.ssh/master_node_key ubuntu@${AGENT_IP} "hostname"
# ssh -i ~/.ssh/master_node_key ubuntu@${MEASUSRE_IP} "hostname"

# If you get a password error, configure passwordless sudo for just taskset on the memcache server:
# sudo visudo
# ubuntu ALL=(ALL) NOPASSWD: /usr/bin/taskset
# then test again


ssh -i ~/.ssh/master_node_key ubuntu@${MEMCACHED_IP} "PID=\$(pidof memcached | awk '{print \$1}'); sudo -n taskset -a -cp 0 \$PID"