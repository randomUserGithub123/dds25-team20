import asyncio
import os
import shutil
import logging

from verify import verify_systems_consistency
from populate import populate_databases, abs_dir
from stress import stress

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(asctime)s - %(name)s - %(message)s",
    datefmt="%I:%M:%S",
)
logger = logging.getLogger("Consistency test")

# create the tmp folder to store the logs, the users and the stock
logger.info("Creating tmp folder...")
temp_folder: str = os.path.join(abs_dir, "wdm_consistency_test")
shutil.rmtree(temp_folder, ignore_errors=True)
os.mkdir(temp_folder)

# if os.path.isdir(temp_folder):
#     shutil.rmtree(temp_folder)

# os.mkdir(temp_folder)
# logger.info("Temp folder created")

# populate the payment and stock databases
logger.info("Populating the databases...")
item_ids, user_ids = asyncio.run(populate_databases())
logger.info("Databases populated")

# run the load test
logger.info("Starting the load test...")
asyncio.run(stress(item_ids, user_ids))
logger.info("Load test completed")

# verify consistency
logger.info("Starting the consistency evaluation...")
asyncio.run(verify_systems_consistency(temp_folder, item_ids, user_ids))
logger.info("Consistency evaluation completed")

# if os.path.isdir(temp_folder):
#     shutil.rmtree(temp_folder)
