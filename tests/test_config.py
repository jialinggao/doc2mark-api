from app.config import settings


def test_settings_default_values():
    assert settings.SERVICE_PORT == 5926
    assert settings.LOG_LEVEL == "INFO"
    assert settings.REDIS_PORT == 6379
    assert settings.LLM_MODEL == "gpt-4o"
    assert settings.ENABLE_LLM is False
    assert settings.MAX_FILE_SIZE == 52428800


def test_settings_types():
    assert isinstance(settings.SERVICE_PORT, int)
    assert isinstance(settings.LOG_LEVEL, str)
    assert isinstance(settings.ENABLE_LLM, bool)
