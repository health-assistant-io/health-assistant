import os
import logging
import logging.handlers
import datetime
import glob
from pathlib import Path


def setup_logging(log_name: str = "latest", debug: bool = False):
    """
    Sets up logging for the application using rotating file handlers.
    Creates a 'logging' directory at the project root.
    Keeps the last 5 logs and rotates at 10MB.
    """
    # Project root is 3 levels up from this file: backend/app/core/logging_setup.py
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    log_dir = project_root / "logging"

    # Ensure directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    current_log = log_dir / f"{log_name}.log"

    # Clean up old-style timestamped logs to transition to the new rotating system
    old_timestamped_logs = glob.glob(str(log_dir / f"{log_name}_*.log"))
    old_timestamped_logs.extend(glob.glob(str(log_dir / "log_*.log")))
    for old_log in old_timestamped_logs:
        try:
            os.remove(old_log)
        except Exception:
            pass

    # Define format
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_level = logging.DEBUG if debug else logging.INFO

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # File handler with rotation: 10MB per file, keep 5 backups
    try:
        # Use .log.1, .log.2 etc instead of default .1, .2
        # To do this we need a custom namer
        file_handler = logging.handlers.RotatingFileHandler(
            current_log, maxBytes=10 * 1024 * 1024, backupCount=5
        )

        def namer(name):
            if name.endswith(".log"):
                return name
            # if name is 'backend.log.1', it becomes 'backend.1.log'
            # But the user wants 'backend.log.1' to end with '.log'
            # So 'backend.log.1' -> 'backend.1.log'
            parts = name.split(".")
            if len(parts) >= 3 and parts[-2] == "log":
                # backend.log.1 -> backend.1.log
                base = ".".join(parts[:-2])
                ext = parts[-2]
                index = parts[-1]
                return f"{base}.{index}.{ext}"
            return name

        file_handler.namer = namer

        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Error creating log file handler: {e}")

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console_handler)

    # Ensure uvicorn and other loggers propagate to root
    for logger_name in [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "celery",
    ]:
        l = logging.getLogger(logger_name)
        l.propagate = True
        # Remove their own handlers to avoid double logging
        for h in l.handlers[:]:
            l.removeHandler(h)

    logging.info(f"--- Application started (Level: {'DEBUG' if debug else 'INFO'}) ---")

    logging.info(f"Log file: {current_log}")
