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
    "STOCK",
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

def try_update_stock(
    order_id: int,
    items_quantities: dict,
    user_id: int,
    total_cost: int
):
    
    failed_stock_processing = False
    
    removed_items: list[tuple[str, int]] = []
    for item_id, quantity in items_quantities.items():
        try:
            response = requests.post(
                f"""http://127.0.0.1:5000/subtract/{item_id}/{quantity}""",
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
            for item_id, quantity in removed_items:
                added_item = False
                while not added_item:
                    print(
                        f"""ROLL-BACK: Adding item: {item_id}"""
                    )
                    sys.stdout.flush()
                    response = requests.post(
                        f"""http://127.0.0.1:5000/add/{item_id}/{quantity}""",
                    )
                    if(
                        response.status_code == 200
                    ):
                        added_item = True
            producer.send(
                "STOCK_PROCESSING",
                {
                    "order_id": order_id,
                    "items_quantities": items_quantities,
                    "user_id": user_id,
                    "total_cost": total_cost,
                    "event_type": STOCK_UPDATE_FAILED
                }
            )
            producer.flush()
            failed_stock_processing = True
            break
        removed_items.append((item_id, quantity))
    
    if(
        not failed_stock_processing
    ):
        producer.send(
            "STOCK_PROCESSING",
            {
                "order_id": order_id,
                "items_quantities": items_quantities,
                "user_id": user_id,
                "total_cost": total_cost,
                "event_type": STOCK_UPDATED
            }
        )
        producer.flush()

def remove_stock(
    items_quantities: dict
):
    for item_id, quantity in items_quantities.items():
        added_item = False
        while not added_item:
            print(
                f"""ROLL-BACK: Adding item: {item_id}"""
            )
            sys.stdout.flush()
            response = requests.post(
                f"""http://127.0.0.1:5000/add/{item_id}/{quantity}""",
            )
            if(
                response.status_code == 200
            ):
                added_item = True

def consume_infinitely():

    consumer.subscribe([
        "STOCK"
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
                    topic_partition.topic == "STOCK"
                ):
                    for record in messages:
                        event_type = record.value["event_type"]
                        if(
                            event_type == STOCK_UPDATE_REQUESTED
                        ):
                            print(
                                f"STOCK_UPDATE_REQUESTED event of order: {record.value["order_id"]}"
                            )
                            sys.stdout.flush()

                            future = thread_pool.submit(
                                try_update_stock,
                                record.value["order_id"],
                                record.value["items_quantities"],
                                record.value["user_id"],
                                record.value["total_cost"]
                            )
                        elif(
                            event_type == STOCK_UPDATE_FAILED
                        ):
                            print(
                                f"STOCK_UPDATE_FAILED event of order: {record.value["order_id"]}"
                            )
                            sys.stdout.flush()

                            future = thread_pool.submit(
                                remove_stock,
                                record.value["items_quantities"]
                            )

        except Exception as e:
            print(e)
            sys.stdout.flush()

if __name__ == "__main__":
    consume_infinitely()