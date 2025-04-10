import os
import asyncio

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from msgspec import msgpack

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")

LOG_FOLDER = "/data/order"
os.makedirs(LOG_FOLDER, exist_ok=True)
LOG_FILE = os.path.join(LOG_FOLDER, "order_log.csv")
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w") as f:
        f.write("order_id,status\n")

PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"
ROLLBACK_PAYMENT = "RollbackPayment"


async def replay_payments(processed_orders):
    print("[ORDER]: Replaying payments on startup")
    consumer = AIOKafkaConsumer(
        "PAYMENT_PROCESSING",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        group_id="payment-replay",
        value_deserializer=lambda m: msgpack.decode(m),
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda m: msgpack.encode(m),
    )

    try:
        msg_pack = await consumer.getmany()
        for tp, messages in msg_pack.items():
            for message in messages:
                event_type = message.value["event_type"]
                order_id, items_quantities, user_id, total_cost = (
                    message.value["order_id"],
                    message.value["items_quantities"],
                    message.value["user_id"],
                    message.value["total_cost"],
                )

                if order_id not in processed_orders:
                    if event_type == PAYMENT_COMPLETED:
                        print(f"[ORDER] Rolling back payment of order {order_id}")
                        producer.send(
                            "PAYMENT",
                            {
                                "order_id": order_id,
                                "items_quantities": items_quantities,
                                "user_id": user_id,
                                "total_cost": total_cost,
                                "event_type": ROLLBACK_PAYMENT,
                            },
                        )
                else:
                    pass  # no rollback
    except:
        print("[ORDER] No replayable payments found")
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    os.makedirs(LOG_FOLDER, exist_ok=True)
    LOG_FILE = os.path.join(LOG_FOLDER, "order_log.csv")
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("order_id,status\n")
    processed_orders = set()
    with open(LOG_FILE, "r") as f:
        for line in f:
            if line.startswith("order_id,status"):
                continue
            order_id, _ = line.strip().split(",")
            processed_orders.add(order_id)
    asyncio.run(replay_payments(processed_orders))
