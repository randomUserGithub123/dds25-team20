import unittest
import redis
import time
import threading
from stock.lock import RedisLock


class TestRedisLock(unittest.TestCase):
    def setUp(self):
        # Connect to Redis
        self.redis = redis.Redis(
            host="localhost",
            port=6379,
            password="redis",
            db=1  # Use a different DB for testing
        )
        # Clear test database
        self.redis.flushdb()

    def tearDown(self):
        # Clean up
        self.redis.flushdb()
        self.redis.close()

    def test_lock_acquire_and_release(self):
        # Test basic lock acquisition and release
        lock = RedisLock(self.redis, "test_lock")

        # Should be able to acquire lock
        self.assertTrue(lock.acquire())
        # Lock should be held
        self.assertTrue(lock._locked)
        # Key should exist in Redis
        self.assertTrue(self.redis.exists("lock:test_lock"))

        # Release the lock
        lock.release()
        # Lock should not be held
        self.assertFalse(lock._locked)
        # Key should not exist in Redis
        self.assertFalse(self.redis.exists("lock:test_lock"))

    def test_lock_context_manager(self):
        # Test the context manager functionality
        key_name = "lock:test_context_lock"

        with RedisLock(self.redis, "test_context_lock") as lock:
            self.assertTrue(lock._locked)
            self.assertTrue(self.redis.exists(key_name))

        # After exiting context, lock should be released
        self.assertFalse(self.redis.exists(key_name))

    def test_lock_timeout(self):
        # Test that lock acquisition times out correctly
        lock1 = RedisLock(self.redis, "timeout_lock", timeout=2)
        lock2 = RedisLock(self.redis, "timeout_lock", timeout=2)

        # First lock should succeed
        self.assertTrue(lock1.acquire())

        # Second lock should fail (timeout after 2 seconds)
        start = time.time()
        self.assertFalse(lock2.acquire())
        duration = time.time() - start

        # Should have taken ~2 seconds
        self.assertGreaterEqual(duration, 1.9)
        self.assertLess(duration, 2.5)

        # Clean up
        lock1.release()