import asyncio
import json
import logging
import os

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(asctime)s - %(name)s - %(message)s",
    datefmt="%I:%M:%S",
)
logger = logging.getLogger(__name__)

n = 10
NUMBER_OF_ITEMS = 100_000
ITEM_STARTING_STOCK = 1_000_000
ITEM_PRICE = 1
NUMBER_OF_USERS = 100_000
USER_STARTING_CREDIT = 1_000_000
NUMBER_OF_ORDERS = NUMBER_OF_USERS

# use absolute path
abs_dir = os.path.dirname(os.path.abspath(__file__))
urls_path = os.path.join(abs_dir, "..", "urls.json")


with open(urls_path) as f:
    urls = json.load(f)
    ORDER_URL = urls["ORDER_URL"]
    PAYMENT_URL = urls["PAYMENT_URL"]
    STOCK_URL = urls["STOCK_URL"]


async def populate_databases():
    async with aiohttp.ClientSession() as session:
        logger.info(f"Batch creating {NUMBER_OF_USERS} users...")
        url: str = (
            f"{PAYMENT_URL}/payment/batch_init/"
            f"{NUMBER_OF_USERS}/{USER_STARTING_CREDIT}"
        )
        async with session.post(url) as resp:
            await resp.json()
        logger.info("Users created")
        logger.info(f"Batch creating {NUMBER_OF_ITEMS} items...")
        url: str = (
            f"{STOCK_URL}/stock/batch_init/"
            f"{NUMBER_OF_ITEMS}/{ITEM_STARTING_STOCK}/{ITEM_PRICE}"
        )
        async with session.post(url) as resp:
            await resp.json()
        logger.info("Items created")
        logger.info(f"Batch creating {NUMBER_OF_ORDERS} orders...")
        url: str = (
            f"{ORDER_URL}/orders/batch_init/"
            f"{NUMBER_OF_ORDERS}/{NUMBER_OF_ITEMS}/{NUMBER_OF_USERS}/{ITEM_PRICE}"
        )
        async with session.post(url) as resp:
            await resp.json()
        logger.info("Orders created")


if __name__ == "__main__":
    asyncio.run(populate_databases())
