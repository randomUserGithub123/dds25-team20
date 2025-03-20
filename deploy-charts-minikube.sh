#!/usr/bin/env bash

helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

helm install -f helm-config/order-redis-helm-values.yaml order-redis-cluster bitnami/redis-cluster
helm install -f helm-config/stock-redis-helm-values.yaml stock-redis-cluster bitnami/redis-cluster
helm install -f helm-config/payment-redis-helm-values.yaml payment-redis-cluster bitnami/redis-cluster