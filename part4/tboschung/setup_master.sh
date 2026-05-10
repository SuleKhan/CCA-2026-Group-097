sudo apt-get update
sudo apt-get install -y python3 python3-pip
sudo apt install python3-fabric

MEMCACHED_IP=34.77.140.213 

ssh -i ~/.ssh/master_node_key ubuntu@${MEMCACHED_IP} "hostname && pidof memcached"
# ssh -i ~/.ssh/master_node_key ubuntu@${AGENT_IP} "hostname"
# ssh -i ~/.ssh/master_node_key ubuntu@measure-server "hostname"

ssh -i ~/.ssh/master_node_key ubuntu@${MEMCACHED_IP} \
  "PID=\$(pidof memcached | awk '{print \$1}'); sudo -n taskset -a -cp 0 \$PID"

# If you get a password error, configure passwordless sudo for just taskset on the memcache server:
# sudo visudo
# ubuntu ALL=(ALL) NOPASSWD: /usr/bin/taskset
# then test again


ssh -i ~/.ssh/master_node_key ubuntu@$34.77.140.213 "PID=\$(pidof memcached | awk '{print \$1}'); sudo -n taskset -a -cp 0 \$PID"