import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.environ.get("BOTMAKER_API_BASE_URL", "https://api.botmaker.com/v2.0")


@dataclass(frozen=True)
class Settings:
    access_token: str
    database_url: str
    api_base_url: str = API_BASE_URL


def load_settings() -> Settings:
    access_token = os.environ.get("BOTMAKER_ACCESS_TOKEN")
    database_url = os.environ.get("DATABASE_URL")
    missing = [
        name
        for name, val in [("BOTMAKER_ACCESS_TOKEN", access_token), ("DATABASE_URL", database_url)]
        if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")
    return Settings(access_token=access_token, database_url=database_url, api_base_url=API_BASE_URL)
