import datetime as dt
import json
import copy
from typing import override
import logging
import logging.config
import atexit
from pathlib import Path

LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JSONLogFormatter(logging.Formatter):
    # we pass in keys we want to see in logs (Key,Val) -> Val is what we lookup in log record
    def __init__(
        self,
        *,
        fmt_keys: dict[str, str] | None = None,
    ):
        super().__init__()
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    # formats keys and returns json string
    @override
    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str)

    # prepare json string
    def _prepare_log_dict(self, record: logging.LogRecord):
        always_fields = {
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.timezone.utc
            ).isoformat(),
        }

        # excpetion handling
        if record.exc_info is not None:
            always_fields["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info is not None:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: msg_val
            if (msg_val := always_fields.pop(val, None)) is not None
            else getattr(record, val)
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        # any extra info passed in -> use extra = {K,V}
        for key, val in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS:
                message[key] = val

        return message


class SimpleLogFormatter(logging.Formatter):
    GREY = "\x1b[90m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    RED = "\x1b[31m"
    BOLD_RED = "\x1b[31;1m"
    CYAN = "\x1b[36m"
    RESET = "\x1b[0m"

    def __init__(self):
        super().__init__()
        self.extra_keys_to_display = [
            "run_session_id",
            "claude_session_id",
            "status",
            "attempt",
        ]

    @override
    def format(self, record):
        level_color = {
            logging.DEBUG: self.GREY,
            logging.INFO: self.GREEN,
            logging.WARNING: self.YELLOW,
            logging.ERROR: self.RED,
            logging.CRITICAL: self.BOLD_RED,
        }.get(record.levelno, self.GREY)
        timestamp = dt.datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        log_str = (
            f"[{level_color}{record.levelname:^7}{self.RESET}|{record.name}] "
            f"{self.GREY}{timestamp}{self.RESET}: {record.getMessage()}"
        )
        extra_parts = []
        for key in self.extra_keys_to_display:
            if hasattr(record, key):
                # Format -> key=value
                extra_parts.append(
                    f"{self.CYAN}{key}{self.RESET}={getattr(record, key)}"
                )

        if extra_parts:
            log_str += f" {self.GREY}[{' '.join(extra_parts)}]{self.RESET}"

        return log_str


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "()": SimpleLogFormatter,
        },
        "json": {
            "()": JSONLogFormatter,  # annoying python
            "fmt_keys": {  # this mapping is so that we can change what keys are called in the logs
                "level": "levelname",
                "message": "message",
                "timestamp": "timestamp",
                "logger": "name",
                "module": "module",
                "function": "funcName",
                "line": "lineno",
                "thread_name": "threadName",
            },
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "simple",
            "stream": "ext://sys.stdout",
        },
        "file_json": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "json",
            "filename": Path("logs") / "my_app.log.jsonl",
            "maxBytes": 5000000,
            "backupCount": 5,
        },
        "queue_handler": {
            "class": "logging.handlers.QueueHandler",
            "handlers": ["stderr", "file_json"],
            "respect_handler_level": True,
        },
    },
    "loggers": {
        "root": {"level": "DEBUG", "handlers": ["queue_handler"]}
    },  # change level to INFO to avoid lots of logs
}


def config_logging(file_name: str):
    log_path = Path("logs") / file_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    d_config = copy.deepcopy(LOGGING_CONFIG)
    d_config["handlers"]["file_json"]["filename"] = str(log_path)
    # Configure logging and start listner thread
    logging.config.dictConfig(d_config)  # root logger
    queue_handler = logging.getHandlerByName("queue_handler")
    if queue_handler is not None:
        queue_handler.listener.start()
        atexit.register(queue_handler.listener.stop)
