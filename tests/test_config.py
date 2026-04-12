from pathlib import Path

import app.config as config_module


def test_resolve_env_file_path_uses_override(monkeypatch) -> None:
    monkeypatch.setenv("BEACON_ENV_FILE", ".env.localtest")
    expected = config_module.PROJECT_ROOT / ".env.localtest"
    assert Path(config_module.resolve_env_file_path()) == expected


def test_get_settings_loads_from_override_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("APP_NAME=Beacon Local Test\nDRY_RUN=true\n", encoding="utf-8")
    monkeypatch.setenv("BEACON_ENV_FILE", str(env_file))
    config_module.get_settings.cache_clear()
    try:
        settings = config_module.get_settings()
        assert settings.app_name == "Beacon Local Test"
        assert settings.dry_run is True
    finally:
        config_module.get_settings.cache_clear()
