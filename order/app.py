import os, sys
import atexit
import uuid
import random
import asyncio
from collections import defaultdict

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from quart import Quart, jsonify, abort, Response
import redis
from msgspec import msgpack, Struct
import aiohttp

from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from circuitbreaker import circuit

import json
from datetime import datetime


MAX_RETRIES = 3
BACKOFF_MULTIPLIER = 1

STOCK_UPDATE_REQUESTED = "StockUpdateRequested"
STOCK_UPDATED = "StockUpdateSucceeded"
STOCK_UPDATE_FAILED = "StockUpdateFailed"

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

ORDER_COMPLETED = "OrderCompleted"

DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"

order_events = {}
order_status = {}

app = Quart("order-service")

GATEWAY_URL = os.environ["GATEWAY_URL"]
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")

db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=False,
)


def close_db_connection() -> None:
    asyncio.create_task(db.close())


atexit.register(close_db_connection)


class OrderValue(Struct):
    paid: bool
    items: list[tuple[str, int]]
    user_id: str
    total_cost: int


async def get_order_from_db(order_id: str) -> OrderValue | None:
    try:
        entry: bytes = await db.get(order_id)
        return msgpack.decode(entry, type=OrderValue) if entry else None
    except redis.exceptions.RedisError:
        abort(400, DB_ERROR_STR)
    entry: OrderValue | None = msgpack.decode(entry, type=OrderValue) if entry else None
    if entry is None:
        abort(400, f"Order: {order_id} not found!")
    return entry


async def acquire_redis_lock(order_id: str):
    timeout = 10
    retry_interval = 0.1

    lock_key = str("lock_" + order_id)
    lock_value = str(uuid.uuid4())

    while True:
        try:

            result = await db.set(lock_key, lock_value, nx=True, ex=timeout)
            if result:
                return True
            else:
                await asyncio.sleep(retry_interval)
        except redis.exceptions.RedisError as e:
            return False


async def release_redis_lock(order_id: str):
    lock_key = str("lock_" + order_id)
    try:
        await db.delete(lock_key)
        return True
    except redis.exceptions.RedisError as e:
        return False


producer = None


async def create_kafka_producer():
    global producer
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m),
    )
    await producer.start()


async def close_kafka_producer():
    global producer
    if producer:
        await producer.stop()


async def consume_order_status_updates():
    consumer = AIOKafkaConsumer(
        "ORDER_STATUS_UPDATE",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda m: msgpack.decode(m),
    )
    await consumer.start()
    try:
        async for message in consumer:

            order_id = message.value["order_id"]
            status = message.value["status"]

            if order_id in order_events:
                order_status[order_id] = status.upper()
                order_events[order_id].set()
    finally:
        await consumer.stop()


@app.before_serving
async def startup():
    await create_kafka_producer()
    asyncio.create_task(consume_order_status_updates())


@app.after_serving
async def shutdown():
    await close_kafka_producer()


@app.post("/create/<user_id>")
async def create_order(user_id: str):
    key = str(uuid.uuid4())
    value = msgpack.encode(
        OrderValue(paid=False, items=[], user_id=user_id, total_cost=0)
    )
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"order_id": key})


@app.post("/batch_init/<int:n>/<int:n_items>/<int:n_users>/<int:item_price>")
async def batch_init_users(n: int, n_items: int, n_users: int, item_price: int):
    def generate_entry() -> OrderValue:
        user_id = random.randint(0, n_users - 1)
        item1_id = random.randint(0, n_items - 1)
        item2_id = random.randint(0, n_items - 1)
        value = OrderValue(
            paid=False,
            items=[(f"{item1_id}", 1), (f"{item2_id}", 1)],
            user_id=f"{user_id}",
            total_cost=2 * item_price,
        )
        return value

    tasks = [db.set(f"{i}", msgpack.encode(generate_entry())) for i in range(n)]

    try:
        await asyncio.gather(*tasks)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)

    return jsonify({"msg": "Batch init for orders successful"})


@app.get("/find/<order_id>")
async def find_order(order_id: str):
    order_entry = await get_order_from_db(order_id)
    return jsonify(
        {
            "order_id": order_id,
            "paid": order_entry.paid,
            "items": order_entry.items,
            "user_id": order_entry.user_id,
            "total_cost": order_entry.total_cost,
        }
    )


@app.post("/addItem/<order_id>/<item_id>/<quantity>")
async def add_item(order_id: str, item_id: str, quantity: int):
    order_entry = await get_order_from_db(order_id)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{GATEWAY_URL}/stock/find/{item_id}") as resp:
                if resp.status != 200:
                    abort(400, f"Item: {item_id} does not exist!")
                item_json = await resp.json()
        except Exception as e:
            abort(400, REQ_ERROR_STR)

    order_entry.items.append((item_id, int(quantity)))
    order_entry.total_cost += int(quantity) * item_json["price"]
    try:
        await db.set(order_id, msgpack.encode(order_entry))
    except redis.exceptions.RedisError:
        abort(400, DB_ERROR_STR)
    return Response(
        f"Item: {item_id} added to order: {order_id}; price updated to: {order_entry.total_cost}",
        status=200,
    )


@app.post("/checkout/<order_id>")
async def checkout(order_id: str):
    state_manager = OrderState(order_id)
    await acquire_redis_lock(order_id)

    try:
        # Load order and save initial state
        order_entry = await get_order_from_db(order_id)
        await state_manager.save_state("STARTED", order_entry)

        items_quantities = defaultdict(int)
        for item_id, quantity in order_entry.items:
            items_quantities[item_id] += quantity

        order_status[order_id] = "PENDING"
        order_events[order_id] = asyncio.Event()

        # Save state before stock update
        await state_manager.save_state("STOCK_UPDATING", order_entry)

        # Request stock update
        await producer.send(
            "STOCK",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "user_id": order_entry.user_id,
                "total_cost": order_entry.total_cost,
                "event_type": STOCK_UPDATE_REQUESTED,
            },
        )

        await order_events[order_id].wait()

        if order_status[order_id] == "FAILED":
            await state_manager.save_state("FAILED", order_entry)
            await rollback_order(order_id)
            await release_redis_lock(order_id)
            abort(400, "Checkout failed")

        elif order_status[order_id] == "COMPLETED":
            order_entry.paid = True
            await db.set(order_id, msgpack.encode(order_entry))
            await state_manager.save_state("COMPLETED", order_entry)
            
            await release_redis_lock(order_id)
            return Response(f"Order: {order_id} completed", status=200)

    except Exception as e:
        await state_manager.save_state("ERROR", order_entry)
        await rollback_order(order_id)
        raise e
    finally:
        if order_id in order_status:
            del order_status[order_id]
        if order_id in order_events:
            del order_events[order_id]

    app.logger.info("[ORDER]: Published 'STOCK' topic for order: %s", order_id)

    await order_events[order_id].wait()

    if order_status[order_id] == "FAILED":
        await release_redis_lock(order_id)
        del order_status[order_id]
        del order_events[order_id]
        abort(400, "User out of credit")
    elif order_status[order_id] == "COMPLETED":
        order_entry.paid = True
        try:
            await db.set(order_id, msgpack.encode(order_entry))
        except redis.exceptions.RedisError:
            await release_redis_lock(order_id)
            del order_status[order_id]
            del order_events[order_id]
            return abort(400, DB_ERROR_STR)
        app.logger.debug("Checkout successful")

        await release_redis_lock(order_id)
        del order_status[order_id]
        del order_events[order_id]

        return Response(f"Order: {order_id} completed", status=200)

async def rollback_order(order_id: str):
    """Rollback an order to its previous state"""
    state_manager = OrderState(order_id)
    previous_state = await state_manager.load_state()
    
    if not previous_state:
        app.logger.error(f"No state found for order {order_id}")
        return

    try:
        # Revert stock changes
        if previous_state["status"] in ["STOCK_UPDATING", "PAYMENT_PROCESSING"]:
            order_data = previous_state["order"]
            for item_id, quantity in order_data["items"]:
                await producer.send(
                    "STOCK",
                    {
                        "order_id": order_id,
                        "items_quantities": {item_id: quantity},
                        "event_type": "ROLLBACK_STOCK"
                    }
                )

        # Revert payment if needed
        if previous_state["status"] == "PAYMENT_PROCESSING":
            order_data = previous_state["order"]
            await producer.send(
                "PAYMENT",
                {
                    "order_id": order_id,
                    "user_id": order_data["user_id"],
                    "amount": order_data["total_cost"],
                    "event_type": "ROLLBACK_PAYMENT"
                }
            )

        await state_manager.save_state("ROLLED_BACK", order_data)
        
    except Exception as e:
        app.logger.error(f"Rollback failed for order {order_id}: {str(e)}")
        raise

@app.route('/health')
async def health_check():
    try:
        # Check Redis connection
        await db.ping()
        db_status = "healthy"
    except redis.exceptions.RedisError:
        db_status = "unhealthy"
    
    # Check Kafka connection
    try:
        kafka_status = "healthy" if producer and producer.is_connected() else "unhealthy"
    except:
        kafka_status = "unhealthy"
    
    status = all([db_status == "healthy", kafka_status == "healthy"])
    return jsonify({
        "status": "healthy" if status else "unhealthy",
        "dependencies": {
            "redis": db_status,
            "kafka": kafka_status
        }
    }), 200 if status else 500

@app.route('/ready')
async def readiness_check():
    return jsonify({"status": "ready"}), 200

@circuit(failure_threshold=5, recovery_timeout=60)
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=BACKOFF_MULTIPLIER, min=1, max=10)
)
async def call_stock_service(session, item_id: str) -> dict:
    try:
        async with session.get(f"{GATEWAY_URL}/stock/find/{item_id}") as resp:
            if resp.status != 200:
                raise Exception(f"Stock service error: {resp.status}")
            return await resp.json()
    except Exception as e:
        app.logger.error(f"Stock service error: {str(e)}")
        raise

async def handle_service_error(operation: str, error: Exception, context: dict):
    app.logger.error(
        f"{operation}_failed",
        error=str(error),
        error_type=type(error).__name__,
        context=context
    )
    
    # Implement compensating transaction if needed
    if operation == "payment":
        await rollback_payment(context["user_id"], context["amount"])
    elif operation == "stock":
        await revert_stock_change(context["item_id"], context["amount"])
        
    # Alert monitoring
    await alert_monitoring_service(
        service=operation,
        error_type=type(error).__name__,
        context=context
    )

class OrderState:
    def __init__(self, order_id: str, state_dir: str = "/app/state"):
        self.order_id = order_id
        self.state_file = f"{state_dir}/{order_id}.json"
        os.makedirs(state_dir, exist_ok=True)

    async def save_state(self, status: str, order_entry: OrderValue):
        """Save order state"""
        try:
            state = {
                "status": status,
                "timestamp": datetime.utcnow().isoformat(),
                "order": {
                    "paid": order_entry.paid,
                    "items": order_entry.items,
                    "user_id": order_entry.user_id,
                    "total_cost": order_entry.total_cost
                }
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            app.logger.error(f"Failed to save state: {str(e)}")

    async def load_state(self) -> dict:
        """Load order state"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            return None
        except Exception as e:
            app.logger.error(f"Failed to load state: {str(e)}")
            return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
