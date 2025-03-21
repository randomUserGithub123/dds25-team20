import logging
import pytest
import asyncio
import requests
from kafka import KafkaConsumer
from msgspec import msgpack
import os
import utils as tu

# Assuming your service is running on localhost:5000
BASE_URL = "http://127.0.0.1:8000"
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9093")
SAGA_COMPLETED = "SagaCompleted"

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


@pytest.mark.asyncio
async def test_checkout_success():
    logging.info("Starting test_checkout_success")
    # Setup a test consumer
    test_consumer = KafkaConsumer(
        SAGA_COMPLETED,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda m: msgpack.decode(m),
    )

    # Test /payment/pay/<user_id>/<order_id>
    user: dict = tu.create_user()
    #self.assertIn('user_id', user)

    user_id: str = user['user_id']
    tu.add_credit_to_user(user_id, 15)

    # create order in the order service and add item to the order
    order: dict = tu.create_order(user_id)
    #self.assertIn('order_id', order)

    order_id: str = order['order_id']

    # add item to the stock service
    item1: dict = tu.create_item(5)
    #self.assertIn('item_id', item1)
    item_id1: str = item1['item_id']
    add_stock_response = tu.add_stock(item_id1, 15)
    #self.assertTrue(tu.status_code_is_success(add_stock_response))

    # add item to the stock service
    item2: dict = tu.create_item(5)
    #self.assertIn('item_id', item2)
    item_id2: str = item2['item_id']
    add_stock_response = tu.add_stock(item_id2, 15)
    #self.assertTrue(tu.status_code_is_success(add_stock_response))

    add_item_response = tu.add_item_to_order(order_id, item_id1, 1)
    #self.assertTrue(tu.status_code_is_success(add_item_response))
    add_item_response = tu.add_item_to_order(order_id, item_id2, 1)
    #self.assertTrue(tu.status_code_is_success(add_item_response))



    # Checkout
    checkout_response = tu.checkout_order(order_id)
    print(checkout_response.text)
    print('##############')
    assert checkout_response.status_code == 200

    # Wait for the ORDER_COMPLETED event
    # async def consume_event():
    #     for message in test_consumer:
    #         if message.value.get("order_id") == order_id:
    #             return message.value

    #completed_event = await asyncio.wait_for(consume_event(), timeout=10)

    # Assert the saga's status
    test_consumer.close()
    logging.info("Finished test_checkout_success")

"""
@pytest.mark.asyncio
async def test_checkout_failure():
    # Setup a test consumer
    logging.info("Starting test_checkout_failure")
    test_consumer = KafkaConsumer(
        SAGA_COMPLETED,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda m: msgpack.decode(m),
    )

    # Create an order
    user_id = "test_user"
    create_response = requests.post(f"{BASE_URL}/create/{user_id}")
    create_response.raise_for_status()
    order_id = create_response.json()["order_id"]

    # Add an item
    item_id = "test_item"
    quantity = 1

    # Simulate a payment error by adding a item that will cause an error in payment service
    # for example an item id that will make the total cost to be a negative number.
    add_item_response = requests.post(f"{BASE_URL}/addItem/{order_id}/errorItem/{quantity}")
    add_item_response.raise_for_status()

    # Checkout
    checkout_response = requests.post(f"{BASE_URL}/checkout/{order_id}")
    checkout_response.raise_for_status()
    saga_id = checkout_response.json()["saga_id"]

    # Wait for the SAGA_COMPLETED event
    async def consume_event():
        for message in test_consumer:
            if message.value["saga_id"] == saga_id:
                return message.value

    completed_event = await asyncio.wait_for(consume_event(), timeout=10)

    # Assert the saga's status
    assert completed_event["status"] == "failed"
    test_consumer.close()
    logging.info("Finished test_checkout_failure")
"""
# Add more test cases as needed...