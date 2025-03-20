import time
import uuid
import redis
from typing import Optional, Callable, Any


class RedisLock:
    """
    Distributed lock implementation using Redis.
    Can be used as a context manager or decorator.
    """

    def __init__(self, redis_client: redis.Redis, lock_name: str,
                 expire_seconds: int = 10, retry_seconds: float = 0.1,
                 max_retries: int = 50):
        """
        Initialize a Redis lock.

        Args:
            redis_client: Redis client instance
            lock_name: Unique name for the lock resource
            expire_seconds: Lock expiration time in seconds
            retry_seconds: Time to wait between retries
            max_retries: Maximum number of retries
        """
        self.redis = redis_client
        self.lock_name = f"lock:{lock_name}"
        self.expire_seconds = expire_seconds
        self.retry_seconds = retry_seconds
        self.max_retries = max_retries
        self.lock_id = str(uuid.uuid4())
        self._locked = False

    def acquire(self) -> bool:
        """
        Acquire the lock, retrying if necessary.

        Returns:
            bool: True if lock was acquired, False otherwise
        """
        retry_count = 0

        while retry_count < self.max_retries:
            # Use Redis SET NX (not exists) to acquire lock atomically
            acquired = self.redis.set(
                self.lock_name,
                self.lock_id,
                nx=True,
                ex=self.expire_seconds
            )

            if acquired:
                self._locked = True
                return True

            retry_count += 1
            time.sleep(self.retry_seconds)

        return False

    def release(self) -> bool:
        """
        Release the lock using Lua script to ensure we only delete our own lock.

        Returns:
            bool: True if the lock was released, False otherwise
        """
        if not self._locked:
            return False

        # Lua script to ensure we only delete our own lock
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """

        # Execute the script
        result = self.redis.eval(script, 1, self.lock_name, self.lock_id)
        success = bool(result)

        if success:
            self._locked = False

        return success

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock {self.lock_name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    @classmethod
    def locked(cls, redis_client: redis.Redis, lock_name: str,
               expire_seconds: int = 10, retry_seconds: float = 0.1,
               max_retries: int = 50):
        """
        Decorator for functions that need lock protection.

        Usage:
            @RedisLock.locked(redis_client, "resource_name")
            def critical_function():
                # Do something that needs locking
        """

        def decorator(func: Callable) -> Callable:
            def wrapper(*args, **kwargs) -> Any:
                with cls(redis_client, lock_name, expire_seconds,
                         retry_seconds, max_retries):
                    return func(*args, **kwargs)

            return wrapper

        return decorator