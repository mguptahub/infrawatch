from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import time
    from ..db import models  # noqa: F401 — registers all models
    for attempt in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception as e:
            if attempt == 9:
                raise
            print(f"DB not ready (attempt {attempt + 1}/10): {e}. Retrying in 3s…")
            time.sleep(3)
