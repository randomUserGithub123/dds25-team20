import logging
import os
import atexit
import uuid
from threading import Thread
from kafka import KafkaConsumer, KafkaProducer
import redis

from msgspec import msgpack, Struct
from flask import Flask, jsonify, abort, Response

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

DB_ERROR_STR = "DB error"

app = Flask("payment-service")

KAFKA_BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS"
)

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda m: msgpack.encode(m),
    key_serializer=lambda m: str(m).encode('utf-8')
)

consumer = KafkaConsumer(
        PAYMENT_REQUESTED,
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
        if topic == PAYMENT_REQUESTED:
            app.logger.info(f"Received payment request: {data}")
            handle_payment_request(data)

def publish(event: str, data: dict, key: str) -> None:
    producer.send(event, data, key=key)
    producer.flush()

db: redis.Redis = redis.Redis(host=os.environ['REDIS_HOST'],
                              port=int(os.environ['REDIS_PORT']),
                              password=os.environ['REDIS_PASSWORD'],
                              db=int(os.environ['REDIS_DB']))


def close_db_connection() -> None:
    db.close()

def close_producer() -> None:
    producer.close()

def close_consumer() -> None:
    consumer.close()

atexit.register(close_db_connection)
atexit.register(close_producer)
atexit.register(close_consumer)

def handle_payment_request(data: dict) -> None:
    app.logger.info(f"Payment request received: {data}")
    
    order_id: str = data.get("order_id")
    order_entry: dict = data.get("order_entry")
    user_id: str = order_entry.get("user_id")
    amount: int = order_entry.get("total_cost")
  
    if not order_id or not user_id or amount is None:
        app.logger.error(f"Invalid payment request data: {data}")
        publish(PAYMENT_FAILED, {"order_data": data, "reason": "Invalid payment request data"}, order_id)
        handle_payment_failure(data)
    
    app.logger.info(f"Processing payment for order {order_id}, user {user_id}, amount {amount}")
    
    try:
        user_entry = get_user_from_db(user_id)

        # Check if user has sufficient credit
        if user_entry.credit < int(amount):
            app.logger.error(f"Insufficient credit for user {user_id}: has {user_entry.credit}, needs {amount}")
            publish(PAYMENT_FAILED, {"order_data": data, "reason": f"Insufficient credit: has {user_entry.credit}, needs {amount}"}, order_id)
            handle_payment_failure(data)
            return
        
        # Process payment
        user_entry.credit -= int(amount)
        db.set(user_id, msgpack.encode(user_entry))
        
        app.logger.info(f"Payment successful for order {order_id}, remaining credit: {user_entry.credit}")
        
        # Publish success event
        publish(PAYMENT_COMPLETED, {"order_data": data, "remaining_credit": user_entry.credit}, order_id)
        
    except Exception as e:
        app.logger.error(f"Error processing payment: {str(e)}")
        publish(PAYMENT_FAILED, {"order_id": order_id, "reason": f"Error processing payment: {str(e)}"}, order_id)
        handle_payment_failure(data)
    

def handle_payment_failure(data: dict):
    #TODO
    pass

class UserValue(Struct):
    credit: int


def get_user_from_db(user_id: str) -> UserValue | None:
    try:
        # get serialized data
        entry: bytes = db.get(user_id)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    # deserialize data if it exists else return null
    entry: UserValue | None = msgpack.decode(entry, type=UserValue) if entry else None
    if entry is None:
        # if user does not exist in the database; abort
        abort(400, f"User: {user_id} not found!")
    return entry


@app.post('/create_user')
def create_user():
    key = str(uuid.uuid4())
    value = msgpack.encode(UserValue(credit=0))
    try:
        db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'user_id': key})


@app.post('/batch_init/<n>/<starting_money>')
def batch_init_users(n: int, starting_money: int):
    n = int(n)
    starting_money = int(starting_money)
    kv_pairs: dict[str, bytes] = {f"{i}": msgpack.encode(UserValue(credit=starting_money))
                                  for i in range(n)}
    try:
        db.mset(kv_pairs)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for users successful"})


@app.get('/find_user/<user_id>')
def find_user(user_id: str):
    user_entry: UserValue = get_user_from_db(user_id)
    return jsonify(
        {
            "user_id": user_id,
            "credit": user_entry.credit
        }
    )


@app.post('/add_funds/<user_id>/<amount>')
def add_credit(user_id: str, amount: int):
    user_entry: UserValue = get_user_from_db(user_id)
    # update credit, serialize and update database
    user_entry.credit += int(amount)
    try:
        db.set(user_id, msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"User: {user_id} credit updated to: {user_entry.credit}", status=200)


@app.post('/pay/<user_id>/<amount>')
def remove_credit(user_id: str, amount: int):
    app.logger.debug(f"Removing {amount} credit from user: {user_id}")
    user_entry: UserValue = get_user_from_db(user_id)
    # update credit, serialize and update database
    user_entry.credit -= int(amount)
    if user_entry.credit < 0:
        abort(400, f"User: {user_id} credit cannot get reduced below zero!")
    try:
        db.set(user_id, msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"User: {user_id} credit updated to: {user_entry.credit}", status=200)


if __name__ == '__main__':
    # Thread(target=event_listener, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, debug=True)
else:
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
    # Thread(target=event_listener, daemon=True).start()
