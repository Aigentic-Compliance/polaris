"""config.py and logging_config.py had 0% and 44% coverage respectively --
both are simple, but "simple" isn't the same as "safe to leave untested,"
per the audit standard applied to everything else in this pass.
"""
import importlib
import logging


def test_config_database_url_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("POLARIS_DATABASE_URL", raising=False)
    import polaris.common.config as config
    importlib.reload(config)
    assert config.DATABASE_URL == "sqlite:///polaris_registry.db"


def test_config_database_url_reads_env_override(monkeypatch):
    monkeypatch.setenv("POLARIS_DATABASE_URL", "postgresql://example/db")
    import polaris.common.config as config
    importlib.reload(config)
    assert config.DATABASE_URL == "postgresql://example/db"
    monkeypatch.delenv("POLARIS_DATABASE_URL", raising=False)
    importlib.reload(config)  # restore for other tests


def test_config_azure_configured_flag_reflects_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "something")
    import polaris.common.config as config
    importlib.reload(config)
    assert config.AZURE_OPENAI_CONFIGURED is True
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    importlib.reload(config)
    assert config.AZURE_OPENAI_CONFIGURED is False


def test_configure_is_idempotent_adds_handler_once():
    from polaris.common.logging_config import _CONFIGURED, configure

    root = logging.getLogger("polaris")
    handlers_before = len(root.handlers)
    configure()
    configure()  # second call must be a no-op, not a second handler
    handlers_after = len(root.handlers)
    # Either it was already configured by an earlier test/import (handlers_before > 0
    # and unchanged), or this is the first call (exactly one handler added).
    assert handlers_after in (handlers_before, handlers_before + 1)
    configure()  # third call, must still not add another
    assert len(root.handlers) == handlers_after
