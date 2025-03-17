import logging
import os
import atexit
import random
import uuid
import requests
from collections import defaultdict
from threading import Thread

from kafka import KafkaProducer, KafkaConsumer

from msgspec import msgpack, Struct
from flask import Flask, jsonify, abort, Response

import redis

STOCK_UPDATE_REQUESTED = "StockUpdateRequested"
STOCK_UPDATED = "StockUpdated"
PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"

DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"
GATEWAY_URL = os.environ["GATEWAY_URL"]

app = Flask("order-service")


KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda m: msgpack.encode(m)
)

def publish(event: str, data: dict):
    producer.send(event, data)
    producer.flush()

def close_producer():
    producer.close()

atexit.register(close_producer)

def event_listener():
    consumer = KafkaConsumer(
        STOCK_UPDATED,
        PAYMENT_COMPLETED,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        value_deserializer=lambda m: msgpack.decode(m)
    )
    for message in consumer:
        topic = message.topic
        data = message.value
        if topic == STOCK_UPDATED:
            handle_stock_updated(data)
        elif topic == PAYMENT_COMPLETED:
            handle_payment_completed(data)

def handle_stock_updated(data: dict):
    app.logger.info("Received STOCK_UPDATED event: %s", data)

def handle_payment_completed(data: dict):
    app.logger.info("Received PAYMENT_COMPLETED event: %s", data)

db: redis.Redis = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    db=int(os.environ["REDIS_DB"]),
)

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
    event_data = {
        "order_id": order_id,
        "user_id": order_entry.user_id,
        "total_cost": order_entry.total_cost,
        "items": order_entry.items,
        "paid": order_entry.paid,
    }

    saga_state: dict[str, bool] = {
        "stock_reserved": False,
        "payment_processed": False,
        "order_confirmed": False,
    }

    """
    app.logger.info("[ORDER]: Initiating checkout for order: %s", order_id)
    # Publish checkout-related events (here, sending a stock update request)    """
    publish(STOCK_UPDATE_REQUESTED, event_data)
    app.logger.info("[ORDER]: Checkout initiated for order: %s", order_id)
    return jsonify({"status": "checkout initiated", "order_id": order_id}), 200


    # get the quantity per item
    items_quantities: dict[str, int] = defaultdict(int)
    for item_id, quantity in order_entry.items:
        items_quantities[item_id] += quantity
    """
    try:
        # The removed items will contain the items that we already have successfully subtracted stock from
        # for rollback purposes.
        # RESERVE STOCK
        app.logger.info(f"Reserving stock for {order_id}")
        for item_id, quantity in items_quantities.items():
            publish(STOCK_UPDATE_REQUESTED, {"item_id": item_id, "quantity": -quantity})
            print(f"Stock update requested for {item_id} with quantity {quantity}")
            stock_reply = send_post_request(f"{GATEWAY_URL}/stock/subtract/{item_id}/{quantity}")
            if stock_reply.status_code != 200:
                app.logger.debug(f"Out of stock on item_id: {item_id}")
                raise Exception(f"Out of stock on item_id: {item_id}")
                # If one item does not have enough stock we need to rollback
                #rollback_stock(removed_items)
                #abort(400, f'Out of stock on item_id: {item_id}')
            removed_items.append((item_id, quantity))
        saga_state["stock_reserved"] = True
        app.logger.info(f"Stock reserved for {order_id}")
        
        # PAYMENT
        app.logger.info(f"Processing payment for {order_id}")
        user_reply = send_post_request(f"{GATEWAY_URL}/payment/pay/{order_entry.user_id}/{order_entry.total_cost}")
        if user_reply.status_code != 200:
            app.logger.debug("User out of credit")
            raise Exception("User out of credit")
            # If the user does not have enough credit we need to rollback all the item stock subtractions
            #rollback_stock(removed_items)
            #abort(400, "User out of credit")
        saga_state["payment_processed"] = True
        app.logger.info(f"Payment processed for {order_id}")

        # CONFIRM ORDER
        app.logger.info(f"Confirming order for {order_id}")
        order_entry.paid = True
        db.set(order_id, msgpack.encode(order_entry))
        saga_state["order_confirmed"] = True
        app.logger.info("Checkout successful")
        return Response("Checkout successful", status=200)
    #try:
    #    db.set(order_id, msgpack.encode(order_entry))"
    except Exception as e:
        app.logger.debug(f"Checkout failed for {order_id}, {str(e)}")

        if saga_state["stock_reserved"]:
            rollback_stock(removed_items)

        if saga_state["payment_processed"]:
            rollback_payment(order_entry.user_id, order_entry.total_cost)
        
        return abort(400, str(e))"
    """


    

if __name__ == "__main__":
    Thread(target=event_listener, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
else:
    from logging import getLogger
    gunicorn_logger = getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
