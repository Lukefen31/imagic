"""SQLAlchemy database engine, session factory, and declarative base.

All database access flows through the ``DatabaseManager`` singleton so that
connection pooling, WAL-mode, and foreign-key enforcement are configured in
exactly one place.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class DatabaseManager:
    """Manages the SQLite database lifecycle.

    Attributes:
        engine: The SQLAlchemy ``Engine`` instance.
        SessionLocal: A configured ``sessionmaker`` bound to ``engine``.
    """

    _instance: Optional["DatabaseManager"] = None

    def __init__(self, db_path: Path) -> None:
        """Initialise the database engine.

        Args:
            db_path: Absolute ``pathlib.Path`` to the SQLite file.  The parent
                directory is created automatically if it does not exist.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path.as_posix()}"
        logger.info("Opening database at %s", url)

        self.engine: Engine = create_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal: sessionmaker[Session] = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

        # Enable WAL mode + foreign keys on every connection.
        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self._migrate(self.engine)
        logger.info("Database tables created / verified.")

    # ------------------------------------------------------------------
    # Lightweight migration
    # ------------------------------------------------------------------
    @staticmethod
    def _migrate(engine: Engine) -> None:
        """Add columns that may be missing in older databases."""
        import sqlite3
        raw_url = str(engine.url).replace("sqlite:///", "")
        try:
            conn = sqlite3.connect(raw_url)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(photos);")
            existing = {row[1] for row in cursor.fetchall()}

            migrations = [
                ("scene_preset", "VARCHAR(64)"),
                ("cull_reasons", "TEXT"),
                ("color_grade", "VARCHAR(64)"),
                ("auto_crop_data", "TEXT"),
            ]
            for col_name, col_type in migrations:
                if col_name not in existing:
                    cursor.execute(f"ALTER TABLE photos ADD COLUMN {col_name} {col_type};")
                    logger.info("Migration: added column photos.%s", col_name)

            conn.commit()
            conn.close()
        except Exception:
            logger.debug("Migration check skipped (table may not exist yet).")

    # ------------------------------------------------------------------
    # Singleton convenience
    # ------------------------------------------------------------------
    @classmethod
    def init(cls, db_path: Path) -> "DatabaseManager":
        """Create (or return existing) global ``DatabaseManager``.

        Args:
            db_path: Path to the SQLite database file.

        Returns:
            The singleton ``DatabaseManager`` instance.
        """
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def get(cls) -> "DatabaseManager":
        """Return the existing singleton.

        Raises:
            RuntimeError: If ``init()`` has not been called yet.
        """
        if cls._instance is None:
            raise RuntimeError(
                "DatabaseManager.init() must be called before DatabaseManager.get()"
            )
        return cls._instance

    def get_session(self) -> Session:
        """Create a new scoped session.

        Returns:
            A fresh ``Session`` instance.  The caller is responsible for
            calling ``session.close()`` (or using it as a context manager).
        """
        return self.SessionLocal()

    def close(self) -> None:
        """Dispose of the engine connection pool."""
        self.engine.dispose()
        logger.info("Database connection closed.")
        DatabaseManager._instance = None
