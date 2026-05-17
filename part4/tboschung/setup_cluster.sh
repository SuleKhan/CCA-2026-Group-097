echo "setting up cluster"

export CLUSTER_NAME="part4.k8s.local"
export PROJECT_ID="cca-26-project"   # adjust if this is not exact
export KOPS_STATE_STORE="gs://cca-eth-2026-group-097-tboschung/"

# gsutil mb gs://cca-eth-2026-group-097-tboschung/

echo "setting up cluster"
PROJECT=$(gcloud config get-value project)
kops create -f part4.yaml
kops update cluster --name part4.k8s.local --yes --admin
echo "Waiting for cluster to be ready"
kops validate cluster --wait 10m
echo "cluster is ready"
kubectl get nodes -o wide

# kops delete cluster --name part4.k8s.local --yes