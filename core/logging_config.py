from __future__ import annotations

import logging
import os
import platform
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

_HANDLER_MARKER = "back_up_helper_runtime_handler"
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_EXCEPTION_HOOK_INSTALLED = False


def runtime_log_directory(temporary_root: Path | None = None) -> Path:
    """Store logs below the configured temporary-work root."""
    root = temporary_root or Path(tempfile.gettempdir())
    return root / "backUpHelper" / "logs"


def log_runtime_environment() -> None:
    """Write a concise, non-sensitive environment snapshot for diagnostics."""
    logger = logging.getLogger("backUpHelper")
    logger.info(
        "Runtime environment | os=%s | release=%s | machine=%s | python=%s | "
        "executable=%s | cwd=%s | conda_env=%s | temp=%s | frozen=%s",
        platform.system(),
        platform.release(),
        platform.machine(),
        sys.version.replace("\n", " "),
        sys.executable,
        Path.cwd(),
        os.environ.get("CONDA_DEFAULT_ENV", "-"),
        tempfile.gettempdir(),
        bool(getattr(sys, "frozen", False)),
    )


def _log_unhandled_exception(exc_type, exc_value, traceback) -> None:
    logging.getLogger("backUpHelper").critical(
        "Unhandled application exception", exc_info=(exc_type, exc_value, traceback)
    )


def install_exception_logging() -> None:
    """Capture uncaught Python and threading exceptions in the runtime log."""
    global _EXCEPTION_HOOK_INSTALLED
    if _EXCEPTION_HOOK_INSTALLED:
        return
    sys.excepthook = _log_unhandled_exception

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        _log_unhandled_exception(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = log_thread_exception
    _EXCEPTION_HOOK_INSTALLED = True


def configure_application_logging(
    save_to_file: bool, temporary_root: Path | None = None
) -> Path | None:
    """Configure console logging and, optionally, one log file for this run."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in tuple(root_logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_LOG_FORMAT)
    stream = sys.stdout if getattr(sys.stdout, "write", None) else sys.stderr
    if getattr(stream, "write", None):
        console_handler = logging.StreamHandler(stream)
        setattr(console_handler, _HANDLER_MARKER, True)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if not save_to_file:
        install_exception_logging()
        log_runtime_environment()
        return None

    directory = runtime_log_directory(temporary_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"back-up-helper-{datetime.now():%Y%m%d-%H%M%S}.log"
    file_handler = logging.FileHandler(path, encoding="utf-8")
    setattr(file_handler, _HANDLER_MARKER, True)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    logging.getLogger("backUpHelper").info("Runtime log file: %s", path)
    install_exception_logging()
    log_runtime_environment()
    return path
