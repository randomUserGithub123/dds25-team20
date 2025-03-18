import os
import atexit
import uuid
import time
import requests
from collections import defaultdict

from kafka import KafkaProducer, KafkaConsumer
import redis

from msgspec import msgpack, Struct
from flask import Flask, jsonify, abort, Response


STOCK_RESERVATION_REQUESTED = "StockReservationRequested"
STOCK_RESERVATION_FAILED = "StockReservationFailed"
STOCK_RESERVATION_SUCCEEDED = "StockReservationSucceeded"

STOCK_UPDATE_REQUESTED = "StockUpdateRequested"
STOCK_UPDATED = "StockUpdateSucceeded"
STOCK_UPDATE_FAILED = "StockUpdateFailed"

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

ORDER_COMPLETED = "OrderCompleted"

DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"
GATEWAY_URL = os.environ["GATEWAY_URL"]

app = Flask("order-service")

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

db: redis.Redis = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    db=int(os.environ["REDIS_DB"]),
)

def close_db_connection() -> None:
    db.close()

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda m: msgpack.encode(m),
    key_serializer=lambda m: str(m).encode('utf-8')
)

consumer = KafkaConsumer(
        STOCK_RESERVATION_FAILED,
        STOCK_RESERVATION_SUCCEEDED,
        STOCK_UPDATED,
        STOCK_UPDATE_FAILED,
        PAYMENT_COMPLETED,
        PAYMENT_FAILED,
        ORDER_COMPLETED,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        value_deserializer=lambda m: msgpack.decode(m),
        key_deserializer=lambda m: m.decode('utf-8') if m else None
    )

def publish(event: str, data: dict) -> None:
    producer.send(event, data)
    producer.flush()

def close_producer() -> None:
    producer.close()

def close_consumer() -> None:
    consumer.close()

atexit.register(close_producer)
atexit.register(close_db_connection)
atexit.register(close_consumer)

def consume_event(order_id: str, success_topic: str, failure_topic: str, timeout: int = 22) -> bool | None:
    # TODO implement fallback after timeout
    start_time = time.time()
    app.logger.info("Consuming events for order: %s", order_id)
    while time.time() - start_time < timeout:
        messages = consumer.poll(timeout_ms=1)
        for topic_partition, message_list in messages.items():
            for message in message_list:
                if message.key == order_id:
                    if message.topic == success_topic:
                        return True
                    if message.topic == failure_topic:
                        return False
            time.sleep(0.1)
    return None

class OrderValue(Struct):
    paid: bool
    items: list[tuple[str, int]]
    user_id: str
    total_cost: int

def get_order_from_db(order_id: str) -> OrderValue | None:
    try:
        entry: bytes = db.get(order_id)
    except redis.exceptions.RedisError:
        abort(400, DB_ERROR_STR)
    entry: OrderValue | None = msgpack.decode(entry, type=OrderValue) if entry else None
    if entry is None:
        abort(400, f"Order: {order_id} not found!")
    return entry

@app.post("/create/<user_id>")
def create_order(user_id: str):
    key = str(uuid.uuid4())
    value = msgpack.encode(OrderValue(paid=False, items=[], user_id=user_id, total_cost=0))
    try:
        db.set(key, value)
    except redis.exceptions.RedisError:
        abort(400, DB_ERROR_STR)
    return jsonify({"order_id": key})

@app.post("/addItem/<order_id>/<item_id>/<quantity>")
def add_item(order_id: str, item_id: str, quantity: int):
    order_entry: OrderValue = get_order_from_db(order_id)
    # Validate item and get price from stock service via gateway
    item_reply = requests.get(f"{GATEWAY_URL}/stock/find/{item_id}")
    if item_reply.status_code != 200:
        abort(400, f"Item: {item_id} does not exist!")
    item_json: dict = item_reply.json()
    order_entry.items.append((item_id, int(quantity)))
    order_entry.total_cost += int(quantity) * item_json["price"]
    try:
        db.set(order_id, msgpack.encode(order_entry))
    except redis.exceptions.RedisError:
        abort(400, DB_ERROR_STR)
    return Response(
        f"Item: {item_id} added to order: {order_id}; price updated to: {order_entry.total_cost}",
        status=200,
    )

@app.post("/checkout/<order_id>")
def checkout(order_id: str):
    app.logger.debug(f"Checking out {order_id}")
    order_entry: OrderValue = get_order_from_db(order_id)
    order_data: dict = {
        "order_id": order_id,
        "order_entry": order_entry
    }
    app.logger.info("[ORDER]: Initiating checkout for order: %s", order_id)

    # get the quantity per item
    items_quantities: dict[str, int] = defaultdict(int)
    for item_id, quantity in order_entry.items:
        items_quantities[item_id] += quantity
    
    # publish stock reservation request
    publish(STOCK_RESERVATION_REQUESTED, order_data)
    app.logger.info("Stock reservation requested for %s", order_id)

    stock_reservation_status: bool | None = consume_event(order_id, STOCK_RESERVATION_SUCCEEDED, STOCK_RESERVATION_FAILED)
    app.logger.info("Stock reservation status: %s", stock_reservation_status)

    if stock_reservation_status is None:
        app.logger.error("Stock reservation timed out for %s", order_id)
        abort(500, "Stock reservation timed out")
    elif not stock_reservation_status:
        app.logger.error("Stock reservation failed for %s", order_id)
        handle_stock_reservation_failed(order_data)
        abort(400, "Stock reservation failed")
    elif stock_reservation_status:
        # If it succeeded, initiate payment
        app.logger.info("Stock reservation succeeded for %s, initiating payment", order_id)
        publish(PAYMENT_REQUESTED, order_data)

    payment_status: bool | None = consume_event(order_id, PAYMENT_COMPLETED, PAYMENT_FAILED)

    if payment_status is None:
        app.logger.error("Payment timed out for %s", order_id)
        abort(500, "Payment timed out")
    elif not payment_status:
        app.logger.error("Payment failed for %s", order_id)
        handle_payment_failed(order_data)
        abort(400, "Payment failed")
    elif payment_status:
        # If payment succeeded, publish stock update request
        app.logger.info("Payment succeeded for %s, initiating stock update", order_id)
        publish(STOCK_UPDATE_REQUESTED, order_data)

    stock_update_status: bool | None = consume_event(order_id, STOCK_UPDATED, STOCK_UPDATE_FAILED)

    if stock_update_status is None:
        app.logger.error("Stock update timed out for %s", order_id)
        abort(500, "Stock update timed out")
    elif not stock_update_status:
        app.logger.error("Stock update failed for %s", order_id)
        handle_stock_update_failed(order_id)
        abort(400, "Stock update failed")
    elif stock_update_status:
        app.logger.info("Stock update succeeded for %s", order_id)
        publish(ORDER_COMPLETED, order_data)
        return Response(f"Order: {order_id} completed", status=200)

    

def handle_stock_reservation_failed(order_id: str):
    #TODO
    pass

def handle_payment_failed(order_id: str):
    #TODO
    pass

def handle_stock_update_failed(order_id: str):
    #TODO
    pass

def handle_stock_updated(data: dict):
    app.logger.info("Received STOCK_UPDATED event: %s", data)

def handle_payment_completed(data: dict):
    app.logger.info("Received PAYMENT_COMPLETED event: %s", data)


    

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
else:
    from logging import getLogger
    gunicorn_logger = getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
