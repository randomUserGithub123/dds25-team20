import os, sys
from time import sleep

from threading import Thread

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

stock_consumer = KafkaConsumer(
    "STOCK_PROCESSING",
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    auto_offset_reset="earliest",
    enable_auto_commit=True,
    value_deserializer=lambda m: msgpack.decode(m),
    key_deserializer=lambda m: m.decode("utf-8") if m else None
)

stock_producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda m: msgpack.encode(m),
    key_serializer=lambda m: str(m).encode('utf-8')
)

def consume_infinitely_stock():

    stock_consumer.subscribe([
        "STOCK_PROCESSING"
    ])

    while True:
        try: 

            raw_messages = stock_consumer.poll(
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
                    topic_partition.topic == "STOCK_PROCESSING"
                ):
                    for record in messages:
                        event_type = record.value["event_type"]
                        if(
                            event_type == STOCK_UPDATED
                        ):
                            print(
                                f"STOCK_UPDATED event of order: {record.value["order_id"]}"
                            )
                            sys.stdout.flush()

                            stock_producer.send(
                                "PAYMENT",
                                {
                                    "order_id": record.value["order_id"],
                                    "items_quantities": record.value["items_quantities"],
                                    "user_id": record.value["user_id"],
                                    "total_cost": record.value["total_cost"],
                                    "event_type": PAYMENT_REQUESTED
                                }
                            )
                            stock_producer.flush()
                            
                        elif(
                            event_type == STOCK_UPDATE_FAILED
                        ):
                            print(
                                f"STOCK_UPDATE_FAILED event of order: {record.value["order_id"]}"
                            )
                            sys.stdout.flush()

                            # TODO: Set order as FAILED


        except Exception as e:
            print(e)
            sys.stdout.flush()

if __name__ == "__main__":
    consume_infinitely_stock()