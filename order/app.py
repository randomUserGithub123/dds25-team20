import os, sys
import atexit
import uuid
import asyncio
from collections import defaultdict

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from quart import Quart, jsonify, abort, Response, request
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

# db = redis.asyncio.Redis(
#     host=os.environ["REDIS_HOST"],
#     port=int(os.environ["REDIS_PORT"]),
#     password=os.environ["REDIS_PASSWORD"],
#     db=int(os.environ["REDIS_DB"])
# )

db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=False
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

producer = None

async def create_kafka_producer():
    global producer
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m)
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
        value_deserializer=lambda m: msgpack.decode(m)
    )
    await consumer.start()
    try:
        async for message in consumer:

            order_id = message.value["order_id"]
            status = message.value["status"]

            if(
                order_id in order_events
            ):
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
    value = msgpack.encode(OrderValue(paid=False, items=[], user_id=user_id, total_cost=0))
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"order_id": key})

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
            "event_type": STOCK_UPDATE_REQUESTED
        }
    )

    app.logger.info("[ORDER]: Published 'STOCK' topic for order: %s", order_id)

    await order_events[order_id].wait()

    if(
        order_status[order_id] == "FAILED"
    ):
        del order_status[order_id]
        del order_events[order_id]
        abort(400, "User out of credit")
    elif(
        order_status[order_id] == "COMPLETED"
    ):
        order_entry.paid = True
        try:
            await db.set(order_id, msgpack.encode(order_entry))
        except redis.exceptions.RedisError:
            del order_status[order_id]
            del order_events[order_id]
            return abort(400, DB_ERROR_STR)
        app.logger.debug("Checkout successful")

        del order_status[order_id]
        del order_events[order_id]

        return Response(
            f"Order: {order_id} completed", 
            status=200
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)