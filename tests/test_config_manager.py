import unittest

from browser_agent.config_manager import ConfigError, ConfigManager


class ConfigManagerTests(unittest.TestCase):
    def test_gemini_requires_api_key(self) -> None:
        config = ConfigManager.defaults()
        config["api_key"] = ""

        with self.assertRaises(ConfigError):
            ConfigManager().validate(config)

    def test_codex_provider_allows_empty_api_key(self) -> None:
        config = ConfigManager.defaults()
        config["llm_provider"] = "codex"
        config["api_key"] = ""

        ConfigManager().validate(config)

    def test_provider_override_merges(self) -> None:
        config = ConfigManager.defaults()
        config["api_key"] = "test-key"

        merged = ConfigManager().merge_overrides(
            config,
            llm_provider="codex",
            model=None,
            mode=None,
            max_steps=None,
        )

        self.assertEqual(merged["llm_provider"], "codex")


if __name__ == "__main__":
    unittest.main()
