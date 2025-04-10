import os
import sys
import asyncio
import redis

from collections import defaultdict

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from msgspec import msgpack
import aiohttp

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
STOCK_UPDATE_REQUESTED = "StockUpdateRequested"
STOCK_UPDATED = "StockUpdateSucceeded"
STOCK_UPDATE_FAILED = "StockUpdateFailed"

PAYMENT_FAILED = "PaymentFailed"

ROLLBACK_STOCK_UPDATE = "RollbackStockUpdate"

# adding redis connection
db = redis.asyncio.cluster.RedisCluster(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=True,
)


async def consume_infinitely():
    consumer = AIOKafkaConsumer(
        "STOCK",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        group_id="stock-consumer",
        value_deserializer=lambda m: msgpack.decode(m),
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m),
    )

    await consumer.start()
    await producer.start()

    async def try_update_stock(
        order_id: str, items_quantities: dict, user_id: str, total_cost: int
    ):
        lock_key = str("payment_lock_" + order_id)

        try:
            result = await db.set(lock_key, "true", nx=True, ex=10)
            if not result:
                print(f"[STOCK] Payment for order {order_id} already being processed")
                return
            if await db.get(f"payment_processed_{order_id}") == "true":
                print(
                    f"[STOCK] Payment for order {order_id} already processed, skipping"
                )
                return
        except redis.exceptions.RedisError as e:
            print(f"[STOCK] Redis error: {e}")
            return

        failed_stock_processing = False
        removed_items = defaultdict(int)

        async with aiohttp.ClientSession() as session:
            for item_id, quantity in items_quantities.items():
                try:
                    async with session.post(
                        f"http://127.0.0.1:5000/subtract/{item_id}/{quantity}"
                    ) as resp:
                        print(f"Response: {resp.status}")
                        sys.stdout.flush()
                        if resp.status != 200:
                            raise Exception("Stock update failed")
                except Exception as e:
                    print(e)
                    sys.stdout.flush()

                    asyncio.create_task(
                        readd_stock(removed_items, order_id, user_id, total_cost)
                    )

                    failed_stock_processing = True
                    break
                removed_items[item_id] = quantity

        if not failed_stock_processing:
            await producer.send(
                "STOCK_PROCESSING",
                {
                    "order_id": order_id,
                    "items_quantities": items_quantities,
                    "user_id": user_id,
                    "total_cost": total_cost,
                    "event_type": STOCK_UPDATED,
                },
            )

    async def readd_stock(
        items_quantities: dict, order_id: str, user_id: str, total_cost: int
    ):
        async with aiohttp.ClientSession() as session:
            for item_id, quantity in items_quantities.items():
                added_item = False
                while not added_item:
                    print(f"""ROLLING BACK: {item_id}""")
                    sys.stdout.flush()
                    async with session.post(
                        f"http://127.0.0.1:5000/add/{item_id}/{quantity}"
                    ) as resp:
                        if resp.status == 200:
                            added_item = True

        await producer.send(
            "STOCK_PROCESSING",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "user_id": user_id,
                "total_cost": total_cost,
                "event_type": STOCK_UPDATE_FAILED,
            },
        )

    try:
        async for message in consumer:
            event_type = message.value["event_type"]

            if event_type == STOCK_UPDATE_REQUESTED:
                print(
                    f"STOCK_UPDATE_REQUESTED event of order: {message.value['order_id']}"
                )
                sys.stdout.flush()

                await try_update_stock(
                    message.value["order_id"],
                    message.value["items_quantities"],
                    message.value["user_id"],
                    message.value["total_cost"],
                )
            elif event_type == PAYMENT_FAILED:
                print(
                    f"STOCK_UPDATE_FAILED event of order: {message.value['order_id']}"
                )
                sys.stdout.flush()

                await readd_stock(
                    message.value["items_quantities"],
                    message.value["order_id"],
                    message.value["user_id"],
                    message.value["total_cost"],
                )

            elif event_type == ROLLBACK_STOCK_UPDATE:
                print(
                    f"ROLLBACK_STOCK_UPDATE event of order: {message.value['order_id']}"
                )
                sys.stdout.flush()

                await readd_stock(
                    message.value["items_quantities"],
                    message.value["order_id"],
                    message.value["user_id"],
                    message.value["total_cost"],
                )
            await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(consume_infinitely())
