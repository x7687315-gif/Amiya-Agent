import pytest

from config import ConfigError, load_settings


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_settings()


def test_loads_with_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    s = load_settings()
    assert s.api_key == "sk-test-123"
    assert s.model == "deepseek-chat"
    assert s.history_limit == 20
