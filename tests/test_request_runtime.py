import asyncio
import threading
import time
import unittest

from core_engine.request_control import RequestPaused, request_control
from core_engine.request_runtime import RequestRuntime, RequestTimedOut


class RequestRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = RequestRuntime(worker_count=1, queue_size=2, timeout_seconds=2)
        await self.runtime.start()

    async def asyncTearDown(self):
        await self.runtime.stop()

    async def test_worker_publishes_queue_lifecycle(self):
        result = await self.runtime.submit("runtime-complete", lambda: {"answer": "ok"})

        self.assertEqual(result, {"answer": "ok"})
        events = [event async for event in self.runtime.events("runtime-complete")]
        self.assertEqual([event["status"] for event in events], ["queued", "running", "completed"])

    async def test_pause_cancels_queued_request_without_running_it(self):
        running = threading.Event()
        release = threading.Event()
        executed_second = threading.Event()

        def first_job():
            running.set()
            release.wait(timeout=2)
            return "first"

        first_task = asyncio.create_task(self.runtime.submit("runtime-first", first_job))
        while not running.is_set():
            await asyncio.sleep(0.01)

        second_task = asyncio.create_task(
            self.runtime.submit("runtime-second", lambda: executed_second.set())
        )
        await asyncio.sleep(0.02)
        self.assertTrue(await self.runtime.pause("runtime-second"))
        with self.assertRaises(RequestPaused):
            await second_task

        release.set()
        self.assertEqual(await first_task, "first")
        self.assertFalse(executed_second.is_set())
        request_control.clear("runtime-second")

    async def test_timeout_sets_cooperative_pause_marker(self):
        runtime = RequestRuntime(worker_count=1, queue_size=1, timeout_seconds=1)
        await runtime.start()
        request_id = "runtime-timeout"
        request_control.clear(request_id)

        def slow_job():
            while not request_control.is_paused(request_id):
                time.sleep(0.01)
            raise RequestPaused("paused by timeout")

        try:
            with self.assertRaises(RequestTimedOut):
                await runtime.submit(request_id, slow_job)
            self.assertEqual(runtime.status(request_id)["status"], "timed_out")
        finally:
            await runtime.stop()
            request_control.clear(request_id)
