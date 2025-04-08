echo "Stopping minikube..."
minikube stop

echo "Deleting minikube cluster..."
minikube delete

cpus=8
memory=7000
echo "Starting minikube with $cpus CPUs and $memory of memory..."
minikube start --cpus=$cpus --memory=$memory

echo "Enabling ingress..."
minikube addons enable ingress

eval $(minikube docker-env)

echo "Building docker images..."
docker build -t order:latest ./order
docker build -t stock:latest ./stock
docker build -t payment:latest ./payment

echo "Creating Kafka namespace..."
kubectl create namespace kafka
kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka
kubectl apply -f ./strimzi-kafka-config/kafka-helm-values.yaml -n kafka
./deploy-charts-minikube.sh

echo "Applying k8s configs..."
kubectl apply -f k8s/

echo "Forwarding ingress to localhost..."
# kubectl port-forward service/ingress-nginx-controller 8000:80 -n ingress-nginx
minikube service ingress-nginx-controller -n ingress-nginx --url
