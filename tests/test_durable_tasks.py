import unittest

from core_engine.durable_tasks import DurableTaskQueue, DurableTaskWorker


class FakePipeline:
    def __init__(self, client):
        self.client = client

    def hset(self, *args, **kwargs):
        self.client.hset(*args, **kwargs)
        return self

    def expire(self, *args, **kwargs):
        self.client.expire(*args, **kwargs)
        return self

    def xadd(self, *args, **kwargs):
        self.client.xadd(*args, **kwargs)
        return self

    def execute(self):
        return []


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.messages = []
        self.acknowledged = []

    def ping(self):
        return True

    def hsetnx(self, key, field, value):
        values = self.hashes.setdefault(key, {})
        if field in values:
            return False
        values[field] = value
        return True

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def exists(self, key):
        return key in self.hashes

    def expire(self, key, seconds):
        return True

    def delete(self, key):
        self.hashes.pop(key, None)

    def xadd(self, stream, fields, **kwargs):
        self.messages.append((stream, dict(fields)))
        return str(len(self.messages))

    def xack(self, stream, group, message_id):
        self.acknowledged.append(message_id)

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class DurableTaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.queue = DurableTaskQueue(client=self.redis, namespace="test-tasks")
        self.queue.max_attempts = 2

    def test_enqueue_is_idempotent_for_the_same_request_id(self):
        first = self.queue.enqueue("request-1", "user-1", {"request": {"query": "sales"}})
        second = self.queue.enqueue("request-1", "user-1", {"request": {"query": "changed"}})

        self.assertEqual(first["public"]["status"], "queued")
        self.assertEqual(second["payload"]["request"]["query"], "sales")
        self.assertEqual(len(self.redis.messages), 1)

    def test_worker_retries_server_failures_then_marks_task_failed(self):
        self.queue.enqueue("request-2", "user-1", {"request": {"query": "sales"}})
        worker = DurableTaskWorker(self.queue, "test-worker")

        worker._handle("1-0", {"request_id": "request-2"}, lambda _: {"code": 500})
        self.assertEqual(self.queue.get("request-2")["public"]["status"], "queued")
        self.assertEqual(self.queue.get("request-2")["public"]["attempt"], 1)

        worker._handle("2-0", {"request_id": "request-2"}, lambda _: {"code": 500})
        self.assertEqual(self.queue.get("request-2")["public"]["status"], "failed")
        self.assertEqual(self.queue.get("request-2")["public"]["attempt"], 2)
        self.assertEqual(self.redis.acknowledged, ["1-0", "2-0"])

    def test_pause_prevents_worker_execution(self):
        self.queue.enqueue("request-3", "user-1", {"request": {"query": "sales"}})
        self.queue.request_pause("request-3")
        worker = DurableTaskWorker(self.queue, "test-worker")

        worker._handle("1-0", {"request_id": "request-3"}, lambda _: self.fail("handler should not run"))

        self.assertEqual(self.queue.get("request-3")["public"]["status"], "paused")

    def test_redis_timeout_exceeds_stream_blocking_read(self):
        self.assertGreaterEqual(self.queue.redis_socket_timeout_seconds, 10)


if __name__ == "__main__":
    unittest.main()
