# CS4550 Distributed Data Systems Project

Team 20

## Architecture

We've built an event-driven microservices architecture. For handling distributed transactions, we used an orchestration-based SAGA pattern. The `Order` service acts as the orchestrator. If something fails, it just compensates by reversing the previous steps. The flow for checking out an order looks roughly like this:
Order (`/checkout`) calls Stock with (`StockUpdateRequested`) and it responds either with `StockUpdated` or `StockUpdateFailed` then the Order service calls Payment with (`PaymentRequested`) and it responds either with `PaymentCompleted` or `PaymentFailed`. If both succeed, the Order service finishes the order checkout.

Moreover, each service has its own Redis cluster (6 nodes per cluster). To keep things isolated inside the database, we used redis locks (a key-value pair inside the Redis database). Also, we used Kafka as the event bus. Topics include `STOCK`, `PAYMENT`, `PAYMENT_PROCESSING`, `STOCK_PROCESSING`, and `ORDER_STATUS_UPDATE`. Kafka also stores the event states, and the service are designed to be idempotent.

Finally, everything runs on Kubernetes. The pods handle load balancing between services, and if a pod crashes, Kubernetes automatically spins up a new one to keep things running smoothly.

## Contributions

* Cem: (@zaturcem) architecture, fault tolerance
* Ema: (@esujster) architecture document, architecture, payment fault tolerance.
* Grgur: (@GrgurDuj) architecture document, SAGA, Kafka
* Marko: (@l33tl4ng, @randomUserGithub123) architecture, Redis Cluster, Kafka, SAGA, switch from Flask to Quart, minikube config
* Simon (@simonbiennier): architecture, minikube configuration, enhance and run consistency/stress tests, write scripts, write README

## Requirements
- [docker](https://docs.docker.com/engine/install/)
- [minikube](https://minikube.sigs.k8s.io/docs/start/?arch=%2Flinux%2Fx86-64%2Fstable%2Fbinary+download)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [helm](https://helm.sh/docs/intro/install/)

## Startup
To start the minikube cluster, run:
```bash
./run_minikube.sh
```

> IMPORTANT: Take ingress' url (top one) and put it in the `test/urls.json`.

> There is a `.gif`, `run_minikube_gif.gif`, which shows how to run the script. Beware, that after running the script it might still take a while for all the pods to startup, as shown in the `.gif` with the couple of failed tests before it actually started.

> OPTIONAL - Docker: `run_docker_gif.gif` shows how to run the project with Docker

## Testing
* Install python 3.8 or greater (tested with 3.10 on Windows 11)
* Install the required packages in the `test` folder using: `pip install -r requirements.txt`
* Change the URLs and ports in the `urls.json` file with the one provided by ingress

> Note: For Windows users you might also need to install pywin32

### Consistency test

In the provided consistency test we first populate the databases with 1 item with 100 stock that costs 1 credit
and 1000 users that have 1 credit.

Then we concurrently send 1000 checkouts of 1 item with random user/item combinations.
If everything goes well only ~10% of the checkouts will succeed, and the expected state should be 0 stock in the item
items and 100 credits subtracted across different users.

Finally, the measurements are done in two phases:
1) Using logs to see whether the service sent the correct message to the clients
2) Querying the database to see if the actual state remained consistent

#### Running
* Run script `run_consistency_test.py`

#### Interpreting results

Wait for the script to finish and check how many inconsistencies you have in both the payment and stock services

### Stress test

To run the stress test you have to:

1) Open a terminal and navigate to the `stress-test` folder.

2) Run the `init_orders.py` to initialize the databases with the following data:

```txt
NUMBER_0F_ITEMS = 100_000
ITEM_STARTING_STOCK = 1_000_000
ITEM_PRICE = 1
NUMBER_OF_USERS = 100_000
USER_STARTING_CREDIT = 1_000_000
NUMBER_OF_ORDERS = 100_000
```

3) Run script: `locust -f locustfile.py --host="localhost"`

> Note: you can also set the --processes flag to increase the amount of locust worker processes.

4) Go to `http://localhost:8089/` to use the Locust.io UI.


To change the weight (task frequency) of the provided scenarios you can change the weights in the `tasks` definition (line 358)
With our locust file each user will make one request between 1 and 15 seconds (you can change that in line 356).

> You can also create your own scenarios as you like (https://docs.locust.io/en/stable/writing-a-locustfile.html)


#### Using the Locust UI
Fill in an appropriate number of users that you want to test with.
The hatch rate is how many users will spawn per second
(locust suggests that you should use less than 100 in local mode).

#### Stress test with Kubernetes

If you want to scale the `stress-test` to a Kubernetes clust you can follow the guide from
Google's [Distributed load testing using Google Kubernetes Engine](https://cloud.google.com/architecture/distributed-load-testing-using-gke)
and [original repo](https://github.com/GoogleCloudPlatform/distributed-load-testing-using-kubernetes).

## Project structure

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
    Folder containing some basic correctness tests for the entire system.
