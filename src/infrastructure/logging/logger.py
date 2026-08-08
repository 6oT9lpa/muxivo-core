from __future__ import annotations

import logging
from logging.config import dictConfig
from pathlib import Path
from typing import Any, Optional


class LoggerManager:
    _instance: Optional["LoggerManager"] = None

    LOGGER_NAMES = ("application", "infrastructure", "modules", "shared")
    TEST_LOGGER_NAMES = ("tests", "tests.preprocessing")

    def __new__(cls) -> "LoggerManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._configure()
        return cls._instance

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(name)

    def _configure(self) -> None:
        log_level = self._get_configured_log_level()

        log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        app_log_file = log_dir / "ai-moder.log"
        tests_log_file = log_dir / "tests.log"

        append_locked_logs = False

        # Remove old log files and their rotations
        for log_file in [app_log_file, tests_log_file]:
            if log_file.exists():
                append_locked_logs = not self._try_unlink(log_file) or append_locked_logs
            for rotation in log_dir.glob(f"{log_file.name}.*"):
                append_locked_logs = not self._try_unlink(rotation) or append_locked_logs

        handlers: dict[str, dict[str, Any]] = {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        }
        app_handlers = ["console"]
        test_handlers = ["console"]

        # A running process, an antivirus scanner, or a read-only working
        # directory must not prevent the application (or its test suite) from
        # starting.  File logs are useful, but console logging remains a safe
        # production fallback when Windows refuses to open the log files.
        if self._can_open_for_append(app_log_file):
            handlers["app_file"] = {
                "class": "logging.FileHandler",
                "level": log_level,
                "formatter": "default",
                "filename": str(app_log_file),
                "mode": "a" if append_locked_logs else "w",
                "encoding": "utf-8",
            }
            app_handlers.append("app_file")

        if self._can_open_for_append(tests_log_file):
            handlers["test_file"] = {
                "class": "logging.FileHandler",
                "level": log_level,
                "formatter": "default",
                "filename": str(tests_log_file),
                "mode": "a" if append_locked_logs else "w",
                "encoding": "utf-8",
            }
            test_handlers.append("test_file")

        dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "format": log_format,
                        "datefmt": date_format,
                    }
                },
                "handlers": handlers,
                "loggers": {
                    **{
                        logger_name: {
                            "level": log_level,
                            "handlers": app_handlers,
                            "propagate": False,
                        }
                        for logger_name in self.LOGGER_NAMES
                    },
                    **{
                        logger_name: {
                            "level": log_level,
                            "handlers": test_handlers,
                            "propagate": False,
                        }
                        for logger_name in self.TEST_LOGGER_NAMES
                    },
                },
                "root": {
                    "level": log_level,
                    "handlers": app_handlers,
                },
            }
        )

    @classmethod
    def _get_configured_log_level(cls) -> str:
        try:
            from src.infrastructure.config import get_config

            config = get_config()
            return cls._normalize_log_level(getattr(config, "log_level", "INFO"))
        except Exception:
            return "INFO"

    @staticmethod
    def _normalize_log_level(value: Any) -> str:
        if isinstance(value, str):
            normalized = value.upper().strip()
            if normalized in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
                return normalized

        return "INFO"

    @staticmethod
    def _try_unlink(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except PermissionError:
            return False

    @staticmethod
    def _can_open_for_append(path: Path) -> bool:
        try:
            with path.open("a", encoding="utf-8"):
                pass
            return True
        except OSError:
            return False


def get_logger(name: str) -> logging.Logger:
    return LoggerManager().get_logger(name)
