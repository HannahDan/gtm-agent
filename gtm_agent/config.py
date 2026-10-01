from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    database_url: str = f"sqlite:///{ROOT / 'data' / 'gtm.db'}"

    sender_name: str = ""
    sender_email: str = ""

    daily_send_cap: int = 30

    crawl_delay_seconds: float = 2.0
    max_pages_per_site: int = 15
    crawl_max_depth: int = 2
    user_agent: str = "GTMResearchBot/0.1"

    gmail_credentials_file: Path = ROOT / "credentials.json"
    gmail_token_file: Path = ROOT / "token.json"

    product_config: Path = ROOT / "config" / "product.yaml"
    pages_dir: Path = ROOT / "data" / "pages"


class StudyConfig(BaseModel):
    format: str
    duration_minutes: int
    incentive: str
    scheduling_link: str


class SenderConfig(BaseModel):
    name: str
    title: str
    company: str
    email: str
    physical_address: str


class ProductConfig(BaseModel):
    product_name: str
    one_liner: str
    pitch: str
    study: StudyConfig
    sender: SenderConfig
    opt_out_line: str = (
        "If you'd prefer not to hear from us, just reply \"unsubscribe\" and we won't email you again."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_product_config(path: Path | None = None) -> ProductConfig:
    path = path or get_settings().product_config
    with open(path) as f:
        return ProductConfig.model_validate(yaml.safe_load(f))
