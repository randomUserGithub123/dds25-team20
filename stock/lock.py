import sys
import os

# Add the parent directory to sys.path to import from common
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.redis_lock import RedisLock

class StockLock(RedisLock):
    """Stock service-specific lock implementation."""
    
    @classmethod
    def for_item(cls, redis_client, item_id, **kwargs):
        """Create a lock specific to a stock item."""
        return cls(redis_client, f"stock:item:{item_id}", **kwargs)
    
    @classmethod
    def for_batch_operation(cls, redis_client, operation_id, **kwargs):
        """Create a lock for batch stock operations."""
        return cls(redis_client, f"stock:batch:{operation_id}", **kwargs)