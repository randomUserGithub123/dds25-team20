import os
import sys
import asyncio
import redis.asyncio as redis

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from msgspec import msgpack
import aiohttp

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

# adding redis connection
db = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]), 
    password=os.environ["REDIS_PASSWORD"],
    decode_responses=True
)

async def consume_infinitely():

    consumer = AIOKafkaConsumer(
        "PAYMENT",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda m: msgpack.decode(m)
    )

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m)
    )

    await consumer.start()
    await producer.start()

    async def try_make_payment(
        user_id: str,
        total_cost: int,
        order_id: str,
        items_quantities: dict
    ):
        async with db.pipeline() as pipe:
            try:
                # adding atomicity
                pipe.multi()
                pipe.get(f"payment:{order_id}:processed")
                pipe.set(f"payment:{order_id}:processing", "true", nx=True, ex=60)
                results = await pipe.execute()
                
                if results[0]:  # already processed
                    print(f"Payment for order {order_id} was already processed, skipping")
                    return
                if not results[1]:  # could not acquire lock
                    print(f"Payment for order {order_id} is being processed by another instance")
                    return
            except redis.exceptions.RedisError as e:
                print(f"Redis transaction error: {e}")
                return

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
                    
                    try:
                        await db.set(f"payment:{order_id}:processed", "true")
                    except redis.exceptions.RedisError as e:
                        print(f"Redis error marking payment as processed: {e}")
                        failed_payment_processing = True


            except Exception as e:
                print(e)
                sys.stdout.flush()
                failed_payment_processing = True
        
        await producer.send_and_wait(
            "PAYMENT_PROCESSING",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "user_id": user_id,
                "total_cost": total_cost,
                "event_type": (
                    PAYMENT_COMPLETED if not failed_payment_processing
                    else PAYMENT_FAILED
                )
            }
        )

    try:
        async for message in consumer:

            if(
                not message
            ):
                continue

            event_type = message.value["event_type"]
            if(
                event_type == PAYMENT_REQUESTED
            ):
                print(f"PAYMENT_REQUESTED event of order: {message.value['order_id']}")
                sys.stdout.flush()

                # preventing race conditions
                await try_make_payment(
                    message.value["user_id"],
                    message.value["total_cost"],
                    message.value["order_id"],
                    message.value["items_quantities"]
                )

                await consumer.commit()

    finally:
        await consumer.stop()
        await producer.stop()

if __name__ == "__main__":
    asyncio.run(consume_infinitely())