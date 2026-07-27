from pymongo import MongoClient
from pymongo.database import Database

from .config import get_settings

_client: MongoClient | None = None


def get_database() -> Database:
    global _client

    settings = get_settings()
    if _client is None:
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)

    return _client[settings.mongodb_database]


def close_database() -> None:
    global _client

    if _client is not None:
        _client.close()
        _client = None
