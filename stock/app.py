import logging
import os
import atexit
import uuid
from collections import defaultdict
from threading import Thread

from kafka import KafkaProducer, KafkaConsumer
import redis

from msgspec import msgpack, Struct
from flask import Flask, jsonify, abort, Response

from common.lock_factory import create_lock

STOCK_RESERVATION_REQUESTED = "StockReservationRequested"
STOCK_RESERVATION_FAILED = "StockReservationFailed"
STOCK_RESERVATION_SUCCEEDED = "StockReservationSucceeded"

STOCK_UPDATE_REQUESTED = "StockUpdateRequested"
STOCK_UPDATED = "StockUpdateSucceeded"
STOCK_UPDATE_FAILED = "StockUpdateFailed"

DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"

app = Flask("stock-service")

db: redis.Redis = redis.Redis(host=os.environ['REDIS_HOST'],
                              port=int(os.environ['REDIS_PORT']),
                              password=os.environ['REDIS_PASSWORD'],
                              db=int(os.environ['REDIS_DB']))

def close_db_connection() -> None:
    db.close()

def publish(event: str, data: dict, key: str) -> None:
    producer.send(event, data, key=key)
    producer.flush()

def close_producer() -> None:
    producer.close()

def close_consumer() -> None:
    consumer.close()

atexit.register(close_db_connection)
atexit.register(close_producer)
atexit.register(close_consumer)

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda m: msgpack.encode(m),
    key_serializer=lambda m: str(m).encode('utf-8')
)

consumer = KafkaConsumer(
        STOCK_UPDATE_REQUESTED,
        STOCK_RESERVATION_REQUESTED,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        value_deserializer=lambda m: msgpack.decode(m),
        key_deserializer=lambda k: k.decode('utf-8') if k else None
    )

def event_listener() -> None:
    for message in consumer:
        topic: str = message.topic
        data: dict = message.value
        if topic == STOCK_RESERVATION_REQUESTED:
            handle_stock_reservation_request(data)
        elif topic == STOCK_UPDATE_REQUESTED:
            handle_stock_update_request(data)

def handle_stock_reservation_request(data: dict) -> None:
    order_id: str = data.get("order_id")

    with StockLock.for_reservation(db, order_id) as lock:
        if not lock.locked:
            app.logger.warning(f"Failed to acquire lock for reservation {order_id}")
            publish(STOCK_RESERVATION_FAILED,
                    {"order_data": data, "reason": "Resource contention"},
                    order_id)
            return

        app.logger.info("Stock reservation requested for %s", order_id)
        order_entry: dict = data.get("order_entry")
        reserved_items: dict[str, int] = {}
        reservation_success: bool = True
        failure_reason: str = "Unknown error"
    
        item_quantities = defaultdict(int)
        for item_id, quantity in order_entry["items"]:
            item_quantities[item_id] += quantity

        # First check if all items have sufficient stock
        for item_id, quantity in item_quantities.items():
            try:
                item_entry = get_item_from_db(item_id)
                if item_entry.stock < quantity:
                    reservation_success = False
                    failure_reason = f"Insufficient stock for item {item_id}: requested {quantity}, available {item_entry.stock}"
                    app.logger.error(failure_reason)
                    break
                reserved_items[item_id] = quantity
            except Exception as e:
                reservation_success = False
                failure_reason = f"Error checking item {item_id}: {str(e)}"
                app.logger.error(failure_reason)
                break

        if reservation_success:
            # If all checks passed, actually reserve the stock
            try:
                for item_id, quantity in reserved_items.items():
                    item_entry = get_item_from_db(item_id)
                    item_entry.stock -= quantity
                    db.set(item_id, msgpack.encode(item_entry))
                    app.logger.debug(f"Reserved {quantity} units of item {item_id}, remaining stock: {item_entry.stock}")

                # Send success event
                app.logger.info(f"Stock reservation succeeded for order {order_id}")
                publish(STOCK_RESERVATION_SUCCEEDED, {"order_data": data, "reserved_items": reserved_items}, order_id)

            except Exception as e:
                failure_reason = f"Error during stock reservation: {str(e)}"
                app.logger.error(failure_reason)
                publish(STOCK_RESERVATION_FAILED, {"order_data": data, "reason": failure_reason}, order_id)
        else:
            # Send failure event with reason
            publish(STOCK_RESERVATION_FAILED, {"order_data": data, "reason": failure_reason}, order_id)


def handle_stock_update_request(data: dict) -> None:
    order_id: str = data.get("order_id")

    with StockLock.for_reservation(db, order_id) as lock:
        if not lock.locked:
            app.logger.warning(f"Failed to acquire lock for reservation {order_id}")
            publish(STOCK_RESERVATION_FAILED,
                    {"order_data": data, "reason": "Resource contention"},
                    order_id)
            return

        app.logger.info(f"Stock update requested: {data}")
        order_id = data.get("order_id")
        #TODO
        publish(STOCK_UPDATED, data, order_id)


class StockValue(Struct):
    stock: int
    price: int


def get_item_from_db(item_id: str) -> StockValue | None:
    # get serialized data
    try:
        entry: bytes = db.get(item_id)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    # deserialize data if it exists else return null
    entry: StockValue | None = msgpack.decode(entry, type=StockValue) if entry else None
    if entry is None:
        # if item does not exist in the database; abort
        abort(400, f"Item: {item_id} not found!")
    return entry


@app.post('/item/create/<price>')
def create_item(price: int):
    key = str(uuid.uuid4())
    app.logger.debug(f"Item: {key} created")
    value = msgpack.encode(StockValue(stock=0, price=int(price)))
    try:
        db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'item_id': key})


@app.post('/batch_init/<n>/<starting_stock>/<item_price>')
def batch_init_users(n: int, starting_stock: int, item_price: int):
    n = int(n)
    starting_stock = int(starting_stock)
    item_price = int(item_price)
    kv_pairs: dict[str, bytes] = {f"{i}": msgpack.encode(StockValue(stock=starting_stock, price=item_price))
                                  for i in range(n)}
    try:
        db.mset(kv_pairs)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for stock successful"})


@app.get('/find/<item_id>')
def find_item(item_id: str):
    item_entry: StockValue = get_item_from_db(item_id)
    return jsonify(
        {
            "stock": item_entry.stock,
            "price": item_entry.price
        }
    )


@app.post('/add/<item_id>/<amount>')
def add_stock(item_id: str, amount: int):
    with StockLock.for_item(db, item_id) as lock:
        if not lock.locked:
            app.logger.warning(f"Failed to acquire lock for item {item_id}")
            abort(503, "Service temporarily unavailable, please retry")
        try:
            item_entry: StockValue = get_item_from_db(item_id)
            # update stock, serialize and update database
            item_entry.stock += int(amount)
            try:
                db.set(item_id, msgpack.encode(item_entry))
            except redis.exceptions.RedisError:
                return abort(400, DB_ERROR_STR)
            return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)
        except Exception as e:
            app.logger.error(f"Error in add_stock: {str(e)}")
            raise


@app.post('/subtract/<item_id>/<amount>')
def remove_stock(item_id: str, amount: int):
    with StockLock.for_item(db, item_id) as lock:
        if not lock.locked:
            app.logger.warning(f"Failed to acquire lock for item {item_id}")
            abort(503, "Service temporarily unavailable, please retry")
        try:
            item_entry: StockValue = get_item_from_db(item_id)
            # update stock, serialize and update database
            item_entry.stock -= int(amount)
            app.logger.debug(f"Item: {item_id} stock updated to: {item_entry.stock}")
            if item_entry.stock < 0:
                abort(400, f"Item: {item_id} stock cannot get reduced below zero!")
            try:
                db.set(item_id, msgpack.encode(item_entry))
            except redis.exceptions.RedisError:
                return abort(400, DB_ERROR_STR)
            return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)
        except Exception as e:
            app.logger.error(f"Error in remove_stock: {str(e)}")
            raise


if __name__ == '__main__':
    Thread(target=event_listener, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, debug=True)
else:
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
    Thread(target=event_listener, daemon=True).start()
