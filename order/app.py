import os
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


async def close_db_connection() -> None:
    asyncio.create_task(await db.close())


atexit.register(close_db_connection)


class OrderValue(Struct):
    paid: bool
    items: list[tuple[str, int]]
    user_id: str
    total_cost: int


LOG_FOLDER = "/data/order"
os.makedirs(LOG_FOLDER, exist_ok=True)
LOG_FILE = os.path.join(LOG_FOLDER, "order_log.csv")
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w") as f:
        f.write("order_id,status\n")


def write_to_log(order_id: str, status: str):
    with open(LOG_FILE, "a") as f:
        f.write(f"{order_id},{status}\n")


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
    await acquire_redis_lock(order_id)

    app.logger.debug(f"Checking out {order_id}")
    order_entry = await get_order_from_db(order_id)
    app.logger.info("[ORDER]: Initiating checkout for order: %s", order_id)

    items_quantities = defaultdict(int)
    for item_id, quantity in order_entry.items:
        items_quantities[item_id] += quantity

    order_status[order_id] = "PENDING"
    order_events[order_id] = asyncio.Event()

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

    app.logger.info("[ORDER]: Published 'STOCK' topic for order: %s", order_id)

    await order_events[order_id].wait()

    if order_status[order_id] == "FAILED":
        await release_redis_lock(order_id)
        del order_status[order_id]
        del order_events[order_id]
        print(f"[ORDER] Checkout for order {order_id} FAILED")
        write_to_log(order_id, "FAILED")
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
        print(f"[ORDER] Checkout for order {order_id} SUCCESS")
        write_to_log(order_id, "SUCCESS")

        await release_redis_lock(order_id)
        del order_status[order_id]
        del order_events[order_id]

        return Response(f"Order: {order_id} completed", status=200)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
