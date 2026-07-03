import unittest

from app.retry import RetryExhaustedError, async_retry


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_retry_succeeds_after_transient_failure(self):
        calls = {"count": 0}

        async def flaky():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient")
            return "ok"

        result = await async_retry(flaky, initial_delay=0, max_attempts=2)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 2)

    async def test_async_retry_raises_after_exhaustion(self):
        async def failing():
            raise RuntimeError("down")

        with self.assertRaises(RetryExhaustedError):
            await async_retry(failing, initial_delay=0, max_attempts=2)


if __name__ == "__main__":
    unittest.main()
