## RUNNING THE PROJECT
If you want to run the default tests, `test/test_microservices.py`, then adjust URL in the beginning of the `test/utils.py` accordingly.

### docker-compose

#### Requirements
- [docker](https://docs.docker.com/engine/install/)
- [docker-compose](https://docs.docker.com/compose/install/)

#### Steps to run:
1. `sudo docker-compose down -v`
2. `sudo docker system prune -af --volumes`
3. `sudo docker-compose up --build --remove-orphans`

### minikube

#### Requirements
- [minikube](https://minikube.sigs.k8s.io/docs/start/?arch=%2Flinux%2Fx86-64%2Fstable%2Fbinary+download)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [helm](https://helm.sh/docs/intro/install/)
- [docker](https://docs.docker.com/engine/install/)

#### Steps to run:
1. `minikube start --cpus=4 --memory=8192`
2. `minikube addons enable ingress`
3. Build local Docker images:
   1. `eval $(minikube docker-env)`
   2. For each service: 
      1. `docker build -t order:latest ./order`
      2. `docker build -t stock:latest ./stock`
      3. `docker build -t user:latest ./payment`
4. `kubectl create namespace kafka`
5. Strimzi: `kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka`
6. Kafka: `kubectl apply -f ./strimzi-kafka-config/kafka-helm-values.yaml -n kafka`
7. Deploy Redis with the helm script: `./deploy-charts-minikube.sh`
8. Apply deployments of service configs: `kubectl apply -f k8s/`
9. If `GATEWAY_URL` needed: `minikube ip`

#### DELETING
1. `kubectl delete -f k8s/`
2. For each service: 
   1. `helm delete order-redis-cluster`
   2. `helm delete stock-redis-cluster`
   3. `helm delete payment-redis-cluster`
3. For each service: 
   1. `kubectl delete pvc --selector app.kubernetes.io/instance=order-redis`
   2. `kubectl delete pvc --selector app.kubernetes.io/instance=stock-redis`
   3. `kubectl delete pvc --selector app.kubernetes.io/instance=payment-redis`
4. `minikube stop`
5. `minikube delete --all`

# Web-scale Data Management Project Template

Basic project structure with Python's Flask and Redis. 
**You are free to use any web framework in any language and any database you like for this project.**

### Project structure

* `env`
    Folder containing the Redis env variables for the docker-compose deployment
    
* `helm-config` 
   Helm chart values for Redis and ingress-nginx
        
* `k8s`
    Folder containing the kubernetes deployments, apps and services for the ingress, order, payment and stock services.
    
* `order`
    Folder containing the order application logic and dockerfile. 
    
* `payment`
    Folder containing the payment application logic and dockerfile. 

* `stock`
    Folder containing the stock application logic and dockerfile. 

* `test`
    Folder containing some basic correctness tests for the entire system. (Feel free to enhance them)

### Deployment types:

#### docker-compose (local development)

After coding the REST endpoint logic run `docker-compose up --build` in the base folder to test if your logic is correct
(you can use the provided tests in the `\test` folder and change them as you wish). 

***Requirements:*** You need to have docker and docker-compose installed on your machine. 

K8s is also possible, but we do not require it as part of your submission. 

#### minikube (local k8s cluster)

This setup is for local k8s testing to see if your k8s config works before deploying to the cloud. 
First deploy your database using helm by running the `deploy-charts-minicube.sh` file (in this example the DB is Redis 
but you can find any database you want in https://artifacthub.io/ and adapt the script). Then adapt the k8s configuration files in the
`\k8s` folder to mach your system and then run `kubectl apply -f .` in the k8s folder. 

***Requirements:*** You need to have minikube (with ingress enabled) and helm installed on your machine.

#### kubernetes cluster (managed k8s cluster in the cloud)

Similarly to the `minikube` deployment but run the `deploy-charts-cluster.sh` in the helm step to also install an ingress to the cluster. 

***Requirements:*** You need to have access to kubectl of a k8s cluster.
