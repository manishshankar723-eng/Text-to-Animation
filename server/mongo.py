"""
mongo.py — one shared MongoDB client for every store that needs it.

Accounts (`users.py`), script drafts (`drafts.py`) and jobs (`jobs.py`) all live
in the same database. Each opening its own MongoClient would mean three
connection pools to one cluster, three sets of heartbeats, and three different
places to change a timeout. They share this one instead.

The client is created lazily on first use so a deployment that runs entirely on
local/in-memory stores never connects to (or even imports) Mongo.
"""

import logging
import threading

from . import config

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def get_client():
    """Return the shared MongoClient, connecting on first use."""
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:  # re-check inside the lock
            return _client
        from pymongo import MongoClient

        _client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
        logger.info("MongoDB client created (db=%s)", config.MONGODB_DB)
        return _client


def get_db():
    """Return the configured database handle."""
    return get_client()[config.MONGODB_DB]


def reset() -> None:
    """Drop the cached client. For tests that need to reconnect."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
        _client = None
