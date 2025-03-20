import os
import atexit
import uuid
import asyncio

from quart import Quart, jsonify, abort, Response
import redis
from msgspec import msgpack, Struct

DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"

app = Quart("stock-service")

db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=False
)

def close_db_connection() -> None:
    asyncio.create_task(db.close())

atexit.register(close_db_connection)

class StockValue(Struct):
    stock: int
    price: int

async def get_item_from_db(item_id: str) -> StockValue | None:
    try:
        entry: bytes = await db.get(item_id)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    entry: StockValue | None = msgpack.decode(entry, type=StockValue) if entry else None
    if entry is None:
        abort(400, f"Item: {item_id} not found!")
    return entry

@app.post('/item/create/<price>')
async def create_item(
    price: int
):
    key = str(uuid.uuid4())
    app.logger.debug(f"Item: {key} created")
    value = msgpack.encode(StockValue(stock=0, price=int(price)))
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({'item_id': key})

@app.post('/batch_init/<n>/<starting_stock>/<item_price>')
async def batch_init_users(
    n: int, 
    starting_stock: int, 
    item_price: int
):
    n = int(n)
    starting_stock = int(starting_stock)
    item_price = int(item_price)
    kv_pairs: dict[str, bytes] = {f"{i}": msgpack.encode(StockValue(stock=starting_stock, price=item_price))
                                  for i in range(n)}
    try:
        await db.mset(kv_pairs)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"msg": "Batch init for stock successful"})

@app.get('/find/<item_id>')
async def find_item(item_id: str):
    item_entry = await get_item_from_db(item_id)
    return jsonify({"stock": item_entry.stock, "price": item_entry.price})

@app.post('/add/<item_id>/<amount>')
async def add_stock(item_id: str, amount: int):
    item_entry: StockValue = await get_item_from_db(item_id)
    item_entry.stock += int(amount)
    try:
        await db.set(item_id, msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)

@app.post('/subtract/<item_id>/<amount>')
async def remove_stock(item_id: str, amount: int):
    item_entry: StockValue = await get_item_from_db(item_id)
    item_entry.stock -= int(amount)
    app.logger.debug(f"Item: {item_id} stock updated to: {item_entry.stock}")
    if item_entry.stock < 0:
        abort(400, f"Item: {item_id} stock cannot get reduced below zero!")
    try:
        await db.set(item_id, msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)