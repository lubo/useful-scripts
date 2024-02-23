import logging

from . import DEBUG


def get_logger(name: str = "bookmarkmgr") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(
        logging.DEBUG if DEBUG else logging.INFO,
    )

    return logger
