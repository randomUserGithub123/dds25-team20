import redis
from redis_lock import RedisLock


def create_lock(redis_client: redis.Redis, resource_id: str,
                lock_prefix: str = "", expire_seconds: int = 10) -> RedisLock:
    """
    Create a Redis lock for a specific resource.

    Args:
        redis_client: Redis client instance
        resource_id: ID of the resource to lock (e.g., order_id, item_id)
        lock_prefix: Prefix to add to the lock name (e.g., "order", "stock")
        expire_seconds: Lock expiration time in seconds

    Returns:
        RedisLock: Initialized lock object
    """
    lock_name = f"{lock_prefix}:{resource_id}" if lock_prefix else resource_id
    return RedisLock(
        redis_client=redis_client,
        lock_name=lock_name,
        expire_seconds=expire_seconds
    )