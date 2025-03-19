import os
import sys
import asyncio

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
import aiohttp
from msgspec import msgpack

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")

PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"
STOCK_UPDATE_FAILED = "StockUpdateFailed"

async def consume_infinitely_payment():
    consumer = AIOKafkaConsumer(
        "PAYMENT_PROCESSING",
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
            if event_type == PAYMENT_COMPLETED:
                print(f"PAYMENT_COMPLETED event of order: {message.value['order_id']}")
                sys.stdout.flush()

                await producer.send(
                    "ORDER_STATUS_UPDATE",
                    {
                        "order_id": message.value['order_id'], 
                        "status": 'COMPLETED'
                    }
                )

            elif event_type == PAYMENT_FAILED:
                print(f"PAYMENT_FAILED event of order: {message.value['order_id']}")
                sys.stdout.flush()

                await producer.send(
                    "STOCK",
                    {
                        "order_id": message.value["order_id"],
                        "items_quantities": message.value["items_quantities"],
                        "event_type": STOCK_UPDATE_FAILED
                    }
                )

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
    asyncio.run(consume_infinitely_payment())