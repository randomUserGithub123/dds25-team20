import os, sys
import atexit
import uuid
import asyncio

from quart import Quart, jsonify, abort, Response
import redis
from msgspec import msgpack, Struct

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

DB_ERROR_STR = "DB error"

app = Quart("payment-service")

db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=False,
)


def close_db_connection() -> None:
    asyncio.create_task(db.close())


atexit.register(close_db_connection)


class UserValue(Struct):
    credit: int


async def get_user_from_db(user_id: str) -> UserValue | None:
    try:
        entry: bytes = await db.get(user_id)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    entry: UserValue | None = msgpack.decode(entry, type=UserValue) if entry else None
    if entry is None:
        abort(400, f"User: {user_id} not found!")
    return entry


async def acquire_redis_lock(user_id: str):
    timeout = 10
    retry_interval = 0.1

    lock_key = str("lock_" + user_id)
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


async def release_redis_lock(user_id: str):
    lock_key = str("lock_" + user_id)
    try:
        await db.delete(lock_key)
        return True
    except redis.exceptions.RedisError as e:
        return False


@app.post("/create_user")
async def create_user():
    key = str(uuid.uuid4())
    value = msgpack.encode(UserValue(credit=0))
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"user_id": key})


@app.post("/batch_init/<n>/<starting_money>")
async def batch_init_users(n: int, starting_money: int):
    n = int(n)
    starting_money = int(starting_money)

    tasks = [
        db.set(f"{i}", msgpack.encode(UserValue(credit=starting_money)))
        for i in range(n)
    ]

    try:
        await asyncio.gather(*tasks)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)

    return jsonify({"msg": "Batch init for users successful"})


@app.get("/find_user/<user_id>")
async def find_user(user_id: str):
    user_entry = await get_user_from_db(user_id)
    return jsonify({"user_id": user_id, "credit": user_entry.credit})

@app.post("/add_funds/<user_id>/<amount>")
async def add_credit(user_id: str, amount: int):
    user_entry = await get_user_from_db(user_id)
    user_entry.credit += int(amount)
    try:
        await db.set(user_id, msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(
        f"User: {user_id} credit updated to: {user_entry.credit}", status=200
    )


@app.post("/pay/<user_id>/<amount>")
async def remove_credit(user_id: str, amount: int):
    await acquire_redis_lock(user_id)

    app.logger.debug(f"Removing {amount} credit from user: {user_id}")
    user_entry = await get_user_from_db(user_id)

    user_entry.credit -= int(amount)

    if user_entry.credit < 0:
        await release_redis_lock(user_id)
        abort(400, f"User: {user_id} credit cannot get reduced below zero!")
    try:
        await db.set(user_id, msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        await release_redis_lock(user_id)
        return abort(400, DB_ERROR_STR)

    await release_redis_lock(user_id)
    return Response(
        f"User: {user_id} credit updated to: {user_entry.credit}", status=200
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
