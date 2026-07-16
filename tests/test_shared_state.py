import unittest

from core_engine.request_control import RequestControl
from core_engine.shared_state import SharedState


class SharedStateTests(unittest.TestCase):
    def test_memory_fallback_stores_json_and_pause_markers(self):
        state = SharedState(redis_url=None, namespace="test")
        control = RequestControl(state=state, pause_ttl_seconds=30)

        state.set_json("schema", {"vectors": [[1.0, 0.0]]}, 30)
        self.assertEqual(state.get_json("schema")["vectors"], [[1.0, 0.0]])

        control.pause("request-1")
        self.assertTrue(control.is_paused("request-1"))
        control.clear("request-1")
        self.assertFalse(control.is_paused("request-1"))
