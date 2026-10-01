from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from gtm_agent import config, models

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("PAGES_DIR", str(tmp_path / "pages"))
    config.get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(models, "_engine", engine)
    with Session(engine) as s:
        yield s
    config.get_settings.cache_clear()


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
