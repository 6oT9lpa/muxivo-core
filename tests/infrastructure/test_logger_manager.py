import logging

from src.infrastructure.logging.logger import LoggerManager


def test_logger_manager_falls_back_to_console_when_log_files_are_unavailable(monkeypatch) -> None:
    """A locked Windows log file must never stop API/test startup."""
    previous_instance = LoggerManager._instance
    monkeypatch.setattr(LoggerManager, "_can_open_for_append", staticmethod(lambda _path: False))
    LoggerManager._instance = None

    try:
        logger = LoggerManager().get_logger("tests")
        assert any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers)
        assert not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers)
    finally:
        LoggerManager._instance = previous_instance
