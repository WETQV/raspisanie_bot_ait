"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    token: str
    group_name: str
    admin_ids: list[int]

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise ValueError("Переменная окружения BOT_TOKEN не задана!")

        group_name = os.getenv("GROUP_NAME", "ИСП-3-22")

        admin_ids_raw = os.getenv("ADMIN_IDS", "")
        admin_ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]

        return cls(token=token, group_name=group_name, admin_ids=admin_ids)
