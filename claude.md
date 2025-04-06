# Testing the Fault Tolerance Implementation

Testing your fault-tolerance implementation requires a combination of:
1. Deploying your enhanced system to Kubernetes
2. Verifying the high-availability configurations are working
3. Simulating failures to ensure recovery
4. Running your existing tests under failure conditions

Here's a comprehensive set of commands to test everything:

## 1. Initial Setup and Deployment

First, deploy your enhanced system:

```bash
# Start minikube with enough resources
minikube start --cpus=4 --memory=8192

# Enable ingress addon
minikube addons enable ingress

# Set docker environment to use minikube's docker daemon
eval $(minikube docker-env)

# Build local Docker images
docker build -t order:latest ./order
docker build -t stock:latest ./stock
docker build -t user:latest ./payment

# Create namespace for Kafka
kubectl create namespace kafka

# Install Strimzi operator for Kafka
kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka

# Apply the updated Kafka configuration
kubectl apply -f ./strimzi-kafka-config/kafka-helm-values.yaml -n kafka

# Deploy Redis clusters with your updated configurations
./deploy-charts-minikube.sh

# Apply your enhanced K8s configurations
kubectl apply -f k8s/

# Get minikube IP for testing
GATEWAY_URL=$(minikube ip)
echo "Gateway URL: $GATEWAY_URL"
```

## 2. Verify Deployment Status

Verify everything is running correctly:

```bash
# Check pod status
kubectl get pods
kubectl get pods -n kafka

# Check service status
kubectl get svc

# Check for statefulsets
kubectl get statefulsets 

# Check Kafka status
kubectl describe kafka my-cluster -n kafka

# Check Redis cluster status
kubectl exec -it <order-redis-pod-name> -- redis-cli -a redis cluster info

# Check the logs of your services
kubectl logs -l component=order
kubectl logs -l component=stock
kubectl logs -l component=payment
```

## 3. Run Initial Tests

Run your existing tests to verify everything works normally:

```bash
# Update test URLs to point to minikube IP
sed -i "s|http://localhost:8000|http://$GATEWAY_URL|g" test/urls.json

# Run your microservices test
cd test
python test_microservices.py

# Run the consistency test (if applicable with minikube)
python run_consistency_test.py
```

## 4. Simulate Failures and Test Recovery

Now, simulate failures to test your fault tolerance:

### 4.1. Test Stock Service Failure and Recovery

```bash
# Find stock pods
kubectl get pods -l component=stock

# Kill one of the stock pods
kubectl delete pod <stock-pod-name> --force

# Check that new pod is automatically created
kubectl get pods -l component=stock

# While the pod is restarting, run a checkout test
cd test
python -c "
import utils as tu
user = tu.create_user()
user_id = user['user_id']
tu.add_credit_to_user(user_id, 15)
item = tu.create_item(5)
item_id = item['item_id']
tu.add_stock(item_id, 10)
order = tu.create_order(user_id)
order_id = order['order_id']
tu.add_item_to_order(order_id, item_id, 1)
checkout_response = tu.checkout_order(order_id)
print(f'Checkout response: {checkout_response.status_code}, {checkout_response.text}')
"
```

### 4.2. Test Redis Failure

```bash
# Find redis pods
kubectl get pods | grep redis

# Kill one of the redis nodes
kubectl delete pod <redis-pod-name> --force

# Immediately run a checkout test to see if it still works
cd test
python -c "
import utils as tu
user = tu.create_user()
user_id = user['user_id']
tu.add_credit_to_user(user_id, 15)
item = tu.create_item(5)
item_id = item['item_id']
tu.add_stock(item_id, 10)
order = tu.create_order(user_id)
order_id = order['order_id']
tu.add_item_to_order(order_id, item_id, 1)
checkout_response = tu.checkout_order(order_id)
print(f'Checkout response: {checkout_response.status_code}, {checkout_response.text}')
"
```

### 4.3. Test Kafka Failure

```bash
# Find Kafka pods
kubectl get pods -n kafka

# Delete one of the Kafka broker pods
kubectl delete pod <kafka-broker-pod-name> -n kafka --force

# Immediately try to checkout an order
cd test
python -c "
import utils as tu
user = tu.create_user()
user_id = user['user_id']
tu.add_credit_to_user(user_id, 15)
item = tu.create_item(5)
item_id = item['item_id']
tu.add_stock(item_id, 10)
order = tu.create_order(user_id)
order_id = order['order_id']
tu.add_item_to_order(order_id, item_id, 1)
checkout_response = tu.checkout_order(order_id)
print(f'Checkout response: {checkout_response.status_code}, {checkout_response.text}')
"
```

### 4.4. Test Multiple Components Failing

```bash
# Kill multiple components simultaneously
kubectl delete pod <stock-pod-name> --force &
kubectl delete pod <redis-pod-name> --force &
kubectl delete pod <kafka-broker-pod-name> -n kafka --force &

# Wait a moment for chaos to happen
sleep 5

# Run multiple checkout operations in parallel
cd test
python -c "
import utils as tu
import concurrent.futures
import time

def create_and_checkout():
    user = tu.create_user()
    user_id = user['user_id']
    tu.add_credit_to_user(user_id, 15)
    item = tu.create_item(5)
    item_id = item['item_id']
    tu.add_stock(item_id, 10)
    order = tu.create_order(user_id)
    order_id = order['order_id']
    tu.add_item_to_order(order_id, item_id, 1)
    checkout_response = tu.checkout_order(order_id)
    return checkout_response.status_code, checkout_response.text

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    future_to_checkout = {executor.submit(create_and_checkout): i for i in range(20)}
    for future in concurrent.futures.as_completed(future_to_checkout):
        checkout_id = future_to_checkout[future]
        try:
            status_code, text = future.result()
            print(f'Checkout {checkout_id} completed with status {status_code}: {text}')
        except Exception as e:
            print(f'Checkout {checkout_id} generated an exception: {e}')
"
```

## 5. Run Load Test Under Failure Conditions

```bash
# Start load test
cd test/stress-test
python init_orders.py

# In another terminal, start Locust
cd test/stress-test
locust -f locustfile.py --host="http://$GATEWAY_URL"

# Visit http://localhost:8089 in your browser to control the load test

# While the load test is running, kill pods one by one
kubectl delete pod <stock-pod-name> --force
# Wait a bit...
kubectl delete pod <redis-pod-name> --force
# Wait a bit...
kubectl delete pod <kafka-broker-pod-name> -n kafka --force
```

## 6. Check Logs and State After Tests

After running these tests, check logs and state to verify everything recovered properly:

```bash
# Check logs for all services
kubectl logs -l component=order --tail=200
kubectl logs -l component=stock --tail=200
kubectl logs -l component=payment --tail=200

# Check Redis cluster state
kubectl exec -it <order-redis-pod-name> -- redis-cli -a redis cluster info

# Check Kafka topics
kubectl exec -it <kafka-pod-name> -n kafka -- bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

# Verify data consistency
cd test
python -c "
import utils as tu
import random

# Check random items and users
for _ in range(5):
    item_id = random.randint(0, 10)
    try:
        item = tu.find_item(str(item_id))
        print(f'Item {item_id}: {item}')
    except:
        print(f'Item {item_id} not found')

for _ in range(5):
    user_id = random.randint(0, 10)
    try:
        user = tu.find_user(str(user_id))
        print(f'User {user_id}: {user}')
    except:
        print(f'User {user_id} not found')
"
```

## 7. Analyze Results

If your system is truly fault-tolerant:

1. During pod failures, most checkout operations should either:
   - Complete successfully
   - Fail gracefully with appropriate error messages (not with 5xx errors)

2. After pods recover, all operations should return to normal

3. There should be no data inconsistencies (e.g., deducted credit without reducing stock)

4. The system shouldn't lose track of order states (check your logs for any "hanging" orders)

If your system passes these tests, your fault-tolerance implementation is working as expected! The key indicators of success are consistent system behavior during failures and proper recovery after services are restored.