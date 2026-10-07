"""Concurrent uploads must be serialised by SQLite, not fail with
"database is locked" (found by a parallel-upload load test during review)."""

import io
import threading
from pathlib import Path

from sqlalchemy import text

from app.core.config import Settings
from app.db.database import (
    SQLITE_BUSY_TIMEOUT_SECONDS,
    create_db_engine,
    create_session_factory,
    init_db,
)
from app.main import build_processing_service
from app.services.file_service import FileService
from tests.factories import BLR_POLYGON_COORDS, kml_document, kml_polygon, placemark


def test_sqlite_connections_use_wal_and_busy_timeout(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar()
        assert busy_timeout == SQLITE_BUSY_TIMEOUT_SECONDS * 1000
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_parallel_uploads_all_succeed(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", log_level="WARNING")
    settings.upload_dir.mkdir(parents=True)
    engine = create_db_engine(settings.resolved_database_url)
    init_db(engine)
    factory = create_session_factory(engine)
    processing = build_processing_service(settings)

    polygons = [placemark(kml_polygon(BLR_POLYGON_COORDS)) for _ in range(3_000)]
    content = kml_document(*polygons).encode()
    errors: list[BaseException] = []

    def upload() -> None:
        session = factory()
        try:
            FileService(session, settings, processing).upload("a.kml", io.BytesIO(content))
        except BaseException as exc:  # noqa: BLE001  collected and asserted below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=upload) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    engine.dispose()
    assert errors == []
