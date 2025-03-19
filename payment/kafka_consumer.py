import os, sys
from time import sleep

from concurrent.futures import ThreadPoolExecutor
import atexit
import requests

from kafka import KafkaProducer, KafkaConsumer
from msgspec import msgpack

KAFKA_BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS"
)

STOCK_UPDATE_REQUESTED = "StockUpdateRequested"
STOCK_UPDATED = "StockUpdateSucceeded"
STOCK_UPDATE_FAILED = "StockUpdateFailed"

PAYMENT_REQUESTED = "PaymentRequested"
PAYMENT_COMPLETED = "PaymentCompleted"
PAYMENT_FAILED = "PaymentFailed"

ORDER_COMPLETED = "OrderCompleted"

consumer = KafkaConsumer(
    "PAYMENT",
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    auto_offset_reset="earliest",
    enable_auto_commit=True,
    value_deserializer=lambda m: msgpack.decode(m),
    key_deserializer=lambda m: m.decode("utf-8") if m else None
)

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda m: msgpack.encode(m),
    key_serializer=lambda m: str(m).encode('utf-8')
)

thread_pool = ThreadPoolExecutor(
    max_workers=20
)

atexit.register(
    thread_pool.shutdown
)

def try_make_payment(
    user_id: int,
    total_cost: int,
    order_id: int,
    items_quantities: dict
):
    
    failed_payment_processing = False
    
    try:
        response = requests.post(
            f"""http://127.0.0.1:5000/pay/{user_id}/{total_cost}""",
        )
        print(
            f"""Response: {response}"""
        )
        sys.stdout.flush()
        if(
            response.status_code != 200
        ):
            raise Exception()
    except Exception as e:
        print(e)
        sys.stdout.flush()
        failed_payment_processing = True
        
    if(
        not failed_payment_processing
    ):
        producer.send(
            "PAYMENT_PROCESSING",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "event_type": PAYMENT_COMPLETED
            }
        )
        producer.flush()
    else:
        producer.send(
            "PAYMENT_PROCESSING",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "event_type": PAYMENT_FAILED
            }
        )
        producer.flush()

def consume_infinitely():

    consumer.subscribe([
        "PAYMENT"
    ])

    while True:
        try: 

            raw_messages = consumer.poll(
                timeout_ms=10
            )

            if not raw_messages:
                continue

            print(
                raw_messages
            )
            sys.stdout.flush()

            for topic_partition, messages in raw_messages.items():
                if(
                    topic_partition.topic == "PAYMENT"
                ):
                    for record in messages:
                        event_type = record.value["event_type"]
                        if(
                            event_type == PAYMENT_REQUESTED
                        ):
                            print(
                                f"PAYMENT_REQUESTED event of order: {record.value["order_id"]}"
                            )
                            sys.stdout.flush()

                            future = thread_pool.submit(
                                try_make_payment,
                                record.value["user_id"],
                                record.value["total_cost"],
                                record.value["order_id"],
                                record.value["items_quantities"]
                            )

        except Exception as e:
            print(e)
            sys.stdout.flush()

if __name__ == "__main__":
    consume_infinitely()