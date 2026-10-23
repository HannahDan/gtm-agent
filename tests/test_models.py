import threading

from sqlalchemy import text
from sqlmodel import SQLModel

from gtm_agent import config, models


class _TrackingLock:
    def __init__(self):
        self._lock = threading.Lock()
        self.waiting = threading.Event()
        self._count_lock = threading.Lock()
        self.entered = 0

    def __enter__(self):
        with self._count_lock:
            self.entered += 1
            if self.entered >= 2:
                self.waiting.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()


def test_get_engine_does_not_share_until_tables_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    config.get_settings.cache_clear()
    monkeypatch.setattr(models, "_engine", None)
    gate = _TrackingLock()
    monkeypatch.setattr(models, "_engine_lock", gate)

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors = []
    original = SQLModel.metadata.create_all

    def slow_create_all(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(SQLModel.metadata, "create_all", slow_create_all)

    def first():
        try:
            models.get_engine()
        except Exception as exc:
            errors.append(exc)

    def second():
        try:
            assert started.wait(timeout=5)
            engine = models.get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM institution"))
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    try:
        threads[0].start()
        assert started.wait(timeout=5)
        threads[1].start()
        assert gate.waiting.wait(timeout=5)
        assert not finished.is_set()
        release.set()
        assert finished.wait(timeout=5)
        assert errors == []
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        config.get_settings.cache_clear()
