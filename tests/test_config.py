from pmeval.config import SERIES_REGISTRY, Settings


def test_defaults_and_secret_hidden(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abc123")
    settings = Settings(_env_file=None)
    assert settings.pmeval_forecast_hours_before == 24
    assert "abc123" not in repr(settings)
    assert settings.fred_api_key.get_secret_value() == "abc123"


def test_registry_keys_unique():
    keys = [spec.key for spec in SERIES_REGISTRY]
    assert len(keys) == len(set(keys))
