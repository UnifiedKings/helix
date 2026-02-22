from __future__ import annotations

import os
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

DB_PATH = os.getenv("MR_DB_PATH", "data/app.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Lightweight forward migrations for SQLite. (create_all does not alter existing tables.)
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(stations)")).fetchall()}
        if "popular_track_pool_size" not in cols:
            conn.execute(text("ALTER TABLE stations ADD COLUMN popular_track_pool_size INTEGER NOT NULL DEFAULT 10"))


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
