"""
Simple logging module for MicroPython.
"""
import sys

DEBUG = 10
INFO = 20
WARNING = 30
ERROR = 40
CRITICAL = 50

_level_names = {
    DEBUG: "DEBUG",
    INFO: "INFO",
    WARNING: "WARNING",
    ERROR: "ERROR",
    CRITICAL: "CRITICAL",
}

class Logger:
    """Logger instance for handling log output."""
    def __init__(self, name: str) -> None:
        self.name = name
        self.level = DEBUG

    def log(self, level: int, msg: str) -> None:
        """Log a message at the specified level."""
        if level >= self.level:
            level_name = _level_names.get(level, str(level))
            print(f"{level_name} | {self.name} | {msg}")

    def debug(self, msg: str) -> None:
        """Log a debug message."""
        self.log(DEBUG, msg)

    def info(self, msg: str) -> None:
        """Log an info message."""
        self.log(INFO, msg)

    def warning(self, msg: str) -> None:
        """Log a warning message."""
        self.log(WARNING, msg)

    def error(self, msg: str) -> None:
        """Log an error message."""
        self.log(ERROR, msg)

    def critical(self, msg: str) -> None:
        """Log a critical message."""
        self.log(CRITICAL, msg)

def get_logger(name: str) -> Logger:
    """Get a named logger instance."""
    return Logger(name)
