import unittest

from pydantic import ValidationError

from core_engine.runtime_config import load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_match_the_runtime_model_contract(self):
        config = load_runtime_config({})
        self.assertEqual(config.llm_model, "deepseek-v4-flash")
        self.assertEqual(config.embedding_model, "text-embedding-v4")
        self.assertEqual(config.llm_max_tokens, 4096)

    def test_dashscope_key_is_used_when_openai_key_is_absent(self):
        config = load_runtime_config({"DASHSCOPE_API_KEY": "dashscope-key"})
        self.assertEqual(config.llm_api_key, "dashscope-key")

    def test_invalid_temperature_is_rejected(self):
        with self.assertRaises(ValidationError):
            load_runtime_config({"LLM_TEMPERATURE": "2.5"})


if __name__ == "__main__":
    unittest.main()
