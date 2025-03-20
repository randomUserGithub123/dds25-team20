import os, sys
import atexit
import uuid
import asyncio

from quart import Quart, jsonify, abort, Response
import redis
from msgspec import msgpack, Struct

lock = asyncio.Lock()

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

DB_ERROR_STR = "DB error"

app = Quart("payment-service")

db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=False
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

@app.post('/create_user')
async def create_user():
    key = str(uuid.uuid4())
    value = msgpack.encode(UserValue(credit=0))
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'user_id': key})

@app.post('/batch_init/<n>/<starting_money>')
async def batch_init_users(
    n: int, 
    starting_money: int
):
    n = int(n)
    starting_money = int(starting_money)
    kv_pairs = {f"{i}": msgpack.encode(UserValue(credit=starting_money)) for i in range(n)}
    try:
        await db.mset(kv_pairs)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for users successful"})

@app.get('/find_user/<user_id>')
async def find_user(
    user_id: str
):
    user_entry = await get_user_from_db(user_id)
    return jsonify({
        "user_id": user_id, 
        "credit": user_entry.credit}
    )

@app.post('/add_funds/<user_id>/<amount>')
async def add_credit(
    user_id: str, 
    amount: int
):
    user_entry = await get_user_from_db(user_id)
    user_entry.credit += int(amount)
    try:
        await db.set(user_id, msgpack.encode(user_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"User: {user_id} credit updated to: {user_entry.credit}", status=200)

@app.post('/pay/<user_id>/<amount>')
async def remove_credit(
    user_id: str, 
    amount: int
):
    async with lock:
        app.logger.debug(f"Removing {amount} credit from user: {user_id}")
        user_entry = await get_user_from_db(user_id)

        before = user_entry.credit

        user_entry.credit -= int(amount)

        print(
            f"Before: {before}; After: {user_entry.credit}"
        )
        sys.stdout.flush()

        if user_entry.credit < 0:
            abort(400, f"User: {user_id} credit cannot get reduced below zero!")
        try:
            await db.set(user_id, msgpack.encode(user_entry))
        except redis.exceptions.RedisError:
            return abort(400, DB_ERROR_STR)
        return Response(f"User: {user_id} credit updated to: {user_entry.credit}", status=200)

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)