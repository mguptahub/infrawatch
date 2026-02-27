from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError
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
    from sqlalchemy import text
    from ..db import models  # noqa: F401 — registers all models

    # One-time migration: convert otp_codes.purpose from PG ENUM to VARCHAR
    # Safe to run every startup — silently skipped if already VARCHAR or table absent
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE otp_codes "
                "ALTER COLUMN purpose TYPE VARCHAR(20) "
                "USING purpose::text"
            ))
            conn.commit()
    except ProgrammingError:
        pass  # table doesn't exist yet — create_all will create it correctly
    except Exception as e:
        print(f"otp_codes purpose migration skipped: {e}")

    for attempt in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception as e:
            if attempt == 9:
                raise
            print(f"DB not ready (attempt {attempt + 1}/10): {e}. Retrying in 3s…")
            time.sleep(3)
