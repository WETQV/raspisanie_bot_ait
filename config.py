"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    token: str
    group_name: str
    admin_ids: list[int]
    telegram_proxy_url: str | None = None
    telegram_api_base_url: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise ValueError("Переменная окружения BOT_TOKEN не задана!")

        group_name = os.getenv("GROUP_NAME", "ИСП-3-22")

        admin_ids_raw = os.getenv("ADMIN_IDS", "")
        admin_ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]

        telegram_proxy_url = os.getenv("TELEGRAM_PROXY_URL")
        if telegram_proxy_url:
            telegram_proxy_url = telegram_proxy_url.strip() or None

        telegram_api_base_url = os.getenv("TELEGRAM_API_BASE_URL")
        if telegram_api_base_url:
            telegram_api_base_url = telegram_api_base_url.strip().rstrip("/") or None

        return cls(
            token=token,
            group_name=group_name,
            admin_ids=admin_ids,
            telegram_proxy_url=telegram_proxy_url,
            telegram_api_base_url=telegram_api_base_url,
        )
