import os
import sys
import asyncio

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
import aiohttp
from msgspec import msgpack

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")

STOCK_UPDATED = "StockUpdateSucceeded"
STOCK_UPDATE_FAILED = "StockUpdateFailed"
PAYMENT_REQUESTED = "PaymentRequested"

async def consume_infinitely_stock():
    consumer = AIOKafkaConsumer(
        "STOCK_PROCESSING",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda m: msgpack.decode(m)
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m)
    )

    await consumer.start()
    await producer.start()

    try:
        async for message in consumer:
            event_type = message.value["event_type"]
            if(
                event_type == STOCK_UPDATED
            ):
                print(f"STOCK_UPDATED event of order: {message.value['order_id']}")
                sys.stdout.flush()

                await producer.send(
                    "PAYMENT",
                    {
                        "order_id": message.value["order_id"],
                        "items_quantities": message.value["items_quantities"],
                        "user_id": message.value["user_id"],
                        "total_cost": message.value["total_cost"],
                        "event_type": PAYMENT_REQUESTED
                    }
                )

            elif(
                event_type == STOCK_UPDATE_FAILED
            ):
                print(f"STOCK_UPDATE_FAILED event of order: {message.value['order_id']}")
                sys.stdout.flush()

                await producer.send(
                    "ORDER_STATUS_UPDATE",
                    {
                        "order_id": message.value['order_id'], 
                        "status": 'FAILED'
                    }
                )

    finally:
        await consumer.stop()
        await producer.stop()

if __name__ == "__main__":
    asyncio.run(consume_infinitely_stock())