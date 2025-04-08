import os, sys
import atexit
import uuid
import asyncio

from quart import Quart, jsonify, abort, Response
import redis
from msgspec import msgpack, Struct

from tenacity import retry, stop_after_attempt, wait_exponential
from circuitbreaker import circuit

DB_ERROR_STR = "DB error"
REQ_ERROR_STR = "Requests error"

app = Quart("stock-service")

db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=False,
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


async def acquire_redis_lock(item_id: str):

    timeout = 10
    retry_interval = 0.1

    lock_key = str("lock_" + item_id)
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


async def release_redis_lock(item_id: str):
    lock_key = str("lock_" + item_id)
    try:
        await db.delete(lock_key)
        return True
    except redis.exceptions.RedisError as e:
        return False


@app.post("/item/create/<price>")
async def create_item(price: int):
    key = str(uuid.uuid4())
    app.logger.debug(f"Item: {key} created")
    value = msgpack.encode(StockValue(stock=0, price=int(price)))
    try:
        await db.set(key, value)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return jsonify({"item_id": key})


@app.post("/batch_init/<n>/<starting_stock>/<item_price>")
async def batch_init_users(n: int, starting_stock: int, item_price: int):
    n = int(n)
    starting_stock = int(starting_stock)
    item_price = int(item_price)

    tasks = [
        db.set(
            f"{i}", msgpack.encode(StockValue(stock=starting_stock, price=item_price))
        )
        for i in range(n)
    ]

    try:
        await asyncio.gather(*tasks)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)

    return jsonify({"msg": "Batch init for stock successful"})


@app.get("/find/<item_id>")
async def find_item(item_id: str):
    item_entry = await get_item_from_db(item_id)
    return jsonify({"stock": item_entry.stock, "price": item_entry.price})


@app.post("/add/<item_id>/<amount>")
async def add_stock(item_id: str, amount: int):
    item_entry: StockValue = await get_item_from_db(item_id)
    item_entry.stock += int(amount)
    try:
        await db.set(item_id, msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)


@app.post("/subtract/<item_id>/<amount>")
async def remove_stock(item_id: str, amount: int):
    await acquire_redis_lock(item_id)

    item_entry: StockValue = await get_item_from_db(item_id)

    item_entry.stock -= int(amount)
    app.logger.debug(f"Item: {item_id} stock updated to: {item_entry.stock}")

    if item_entry.stock < 0:
        await release_redis_lock(item_id)
        abort(400, f"Item: {item_id} stock cannot get reduced below zero!")
    try:
        await db.set(item_id, msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        await release_redis_lock(item_id)
        return abort(400, DB_ERROR_STR)

    await release_redis_lock(item_id)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)

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
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def update_stock_with_retry(item_id: str, amount: int, operation: str) -> None:
    try:
        if operation == "subtract":
            await remove_stock(item_id, amount)
        else:
            await add_stock(item_id, amount)
    except Exception as e:
        app.logger.error(f"Stock update error: {str(e)}")
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

class StockState:
    def __init__(self, item_id: str, state_dir: str = "/app/state"):
        self.item_id = item_id
        self.state_file = f"{state_dir}/{item_id}.json"
        os.makedirs(state_dir, exist_ok=True)

    async def save_state(self, operation: str, stock: int, quantity: int):
        try:
            state = {
                "operation": operation,
                "timestamp": datetime.utcnow().isoformat(),
                "previous_stock": stock,
                "quantity": quantity
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            app.logger.error(f"Failed to save state: {str(e)}")

@app.post("/subtract/<item_id>/<amount>")
async def remove_stock(item_id: str, amount: int):
    state_manager = StockState(item_id)
    await acquire_redis_lock(item_id)

    try:
        item_entry = await get_item_from_db(item_id)
        await state_manager.save_state("SUBTRACT_STARTED", item_entry.stock, int(amount))

        item_entry.stock -= int(amount)
        
        if item_entry.stock < 0:
            await state_manager.save_state("SUBTRACT_FAILED", item_entry.stock, int(amount))
            abort(400, f"Item: {item_id} stock cannot get reduced below zero!")

        await db.set(item_id, msgpack.encode(item_entry))
        await state_manager.save_state("SUBTRACT_COMPLETED", item_entry.stock, int(amount))
        
        return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)

    except Exception as e:
        await state_manager.save_state("ERROR", item_entry.stock, int(amount))
        raise e
    finally:
        await release_redis_lock(item_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
