#!/bin/bash

# echo ""
# echo "Deleting service pods"
# echo ""
# kubectl delete -f k8s/

# echo ""
# echo "Deleting Redis clusters"
# echo ""
# helm delete order-redis-cluster
# helm delete stock-redis-cluster
# helm delete payment-redis-cluster

# echo ""
# echo "Stopping Kafka"
# echo ""
# kubectl delete -f ./helm-config/kafka-helm-values.yaml -n kafka
# kubectl delete -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka
# kubectl delete namespace kafka

# echo ""
# echo "Deleting pvc of services"
# echo ""
# kubectl delete pvc --selector app.kubernetes.io/instance=order-redis-cluster
# kubectl delete pvc --selector app.kubernetes.io/instance=stock-redis-cluster
# kubectl delete pvc --selector app.kubernetes.io/instance=payment-redis-cluster
# kubectl delete pvc --selector app.kubernetes.io/instance=kafka -n kafka

echo ""
echo "Stopping minikube..."
echo ""
minikube stop

echo ""
echo "Deleting minikube cluster..."
echo ""
minikube delete --all

cpus=8
memory=7000
echo ""
echo "Starting minikube with $cpus CPUs and $memory of memory..."
echo ""
minikube start --cpus=$cpus --memory=$memory

echo ""
echo "Enabling ingress..."
echo ""
minikube addons enable ingress

eval $(minikube docker-env)

echo ""
echo "Building docker images..."
echo ""
docker build -t order:latest ./order &
docker build -t stock:latest ./stock &
docker build -t payment:latest ./payment &
wait

echo ""
echo "Setting up Kafka cluster..."
echo ""
kubectl create namespace kafka

echo ""
echo "Installing Strimzi operator..."
echo ""
kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka

echo ""
echo "Waiting for Strimzi operator to be ready..."
echo "[CAN TAKE A WHILE]"
echo ""
kubectl wait --for=condition=Available deployment/strimzi-cluster-operator --timeout=300s -n kafka

echo ""
echo "Deploying Kafka cluster..."
echo "[CAN TAKE A WHILE]"
echo ""
kubectl apply -f ./helm-config/kafka-helm-values.yaml -n kafka

echo ""
echo "Waiting for Kafka cluster to be ready..."
echo "[CAN TAKE A WHILE]"
echo ""
kubectl wait kafka/my-cluster --for=condition=Ready --timeout=600s -n kafka

echo ""
echo "Verifying Kafka topics..."
echo ""
kubectl wait --for=condition=Ready --timeout=300s kafkatopic --all -n kafka

echo ""
echo "Deploying other services..."
echo ""
./deploy-charts-minikube.sh
kubectl apply -f k8s/

echo ""
echo "Waiting for services to be ready..."
echo "[CAN TAKE A WHILE]"
echo ""
kubectl wait --for=condition=Available deployment --all --timeout=300s

echo ""
echo "Cluster is ready. Access via:"
echo ""
minikube service ingress-nginx-controller -n ingress-nginx --url