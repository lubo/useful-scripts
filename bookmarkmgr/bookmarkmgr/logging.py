import logging

from . import DEBUG


def get_logger(name="bookmarkmgr"):
    logger = logging.getLogger(name)
    logger.setLevel(
        logging.DEBUG if DEBUG else logging.INFO,
    )

    return logger
