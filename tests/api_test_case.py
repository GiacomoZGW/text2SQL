"""Isolated API fixtures that never call a model provider."""

import unittest
from unittest.mock import patch

from core_engine.request_runtime import RequestRuntime
from core_engine.runtime_config import ModelRuntimeConfig


class ConfiguredApiTestCase(unittest.TestCase):
    def setUp(self):
        config = ModelRuntimeConfig(
            openai_api_key="unit-test-placeholder",
            openai_base_url="http://127.0.0.1:9/v1",
        )
        for patcher in (
            patch("api.main.model_runtime_config", config),
            patch("api.main.request_runtime", RequestRuntime()),
            patch(
                "langchain_openai.ChatOpenAI.invoke",
                side_effect=AssertionError("API tests must mock model calls"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
