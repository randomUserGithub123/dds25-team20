import os
import sys
import asyncio

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from msgspec import msgpack
import aiohttp

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

ROLLBACK_PAYMENT = "RollbackPayment"


async def consume_infinitely():
    consumer = AIOKafkaConsumer(
        "PAYMENT",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda m: msgpack.decode(m),
    )

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m),
    )

    await consumer.start()
    await producer.start()

    async def try_make_payment(
        user_id: str, total_cost: int, order_id: str, items_quantities: dict
    ):
        failed_payment_processing = False

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"http://127.0.0.1:5000/pay/{user_id}/{total_cost}"
                ) as resp:
                    print(f"Response: {resp.status}")
                    sys.stdout.flush()
                    if resp.status >= 400:
                        raise Exception("Payment failed")
            except Exception as e:
                print(e)
                sys.stdout.flush()
                failed_payment_processing = True

        await producer.send(
            "PAYMENT_PROCESSING",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "user_id": user_id,
                "total_cost": total_cost,
                "event_type": (
                    PAYMENT_COMPLETED
                    if not failed_payment_processing
                    else PAYMENT_FAILED
                ),
            },
        )

    try:
        async for message in consumer:
            if not message:
                continue

            event_type = message.value["event_type"]
            if event_type == PAYMENT_REQUESTED:
                print(f"PAYMENT_REQUESTED event of order: {message.value['order_id']}")
                sys.stdout.flush()

                asyncio.create_task(
                    try_make_payment(
                        message.value["user_id"],
                        message.value["total_cost"],
                        message.value["order_id"],
                        message.value["items_quantities"],
                    )
                )

            elif event_type == ROLLBACK_PAYMENT:
                print(f"ROLLBACK_PAYMENT event of order: {message.value['order_id']}")

                user_id, total_cost = (
                    message.value["user_id"],
                    message.value["total_cost"],
                )

                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.post(
                            f"http://127.0.0.1:5000/add_funds/{user_id}/{total_cost}"
                        ) as resp:
                            print(f"Response: {resp.status}")
                            sys.stdout.flush()
                            if resp.status >= 400:
                                raise Exception("Payment failed")
                    except Exception as e:
                        print(e)

            await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(consume_infinitely())
