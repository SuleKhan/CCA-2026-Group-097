#!/bin/zsh

PROJECT=$(gcloud config get-value project)

echo "Using project: $PROJECT"

kops create -f part1.yaml

kops create secret --name part1.k8s.local sshpublickey admin -i ~/.ssh/cloud-computing.pub

kops update cluster --name part1.k8s.local --yes --admin

echo "waiting for cluster to be ready"
kops validate cluster --wait 10m

kubectl get nodes -o wide


# memcache setup
echo "setting up memcache now"
kubectl create -f memcache-t1-cpuset.yaml

kubectl expose pod some-memcached --name some-memcached-11211 --type LoadBalancer --port 11211 --protocol TCP

sleep 60

kubectl get service some-memcached-11211

kubectl get pods -o wide