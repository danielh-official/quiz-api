from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import config

engine = create_engine(config.DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1), pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db
