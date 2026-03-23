import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/rag"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def ensure_database_schema() -> None:
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE docs ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''")
        )


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
