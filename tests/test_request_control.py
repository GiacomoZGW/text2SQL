import unittest

from core_engine.request_control import RequestPaused, request_control


class RequestControlTests(unittest.TestCase):
    def test_pause_marker_can_be_set_and_cleared(self):
        request_id = "request-control-test"
        request_control.clear(request_id)
        self.assertFalse(request_control.is_paused(request_id))

        request_control.pause(request_id)
        self.assertTrue(request_control.is_paused(request_id))

        request_control.clear(request_id)
        self.assertFalse(request_control.is_paused(request_id))
        self.assertTrue(issubclass(RequestPaused, RuntimeError))


if __name__ == "__main__":
    unittest.main()
