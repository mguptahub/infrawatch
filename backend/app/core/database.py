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

    # One-time: dedupe collected_metrics then add unique constraint (for incremental pull)
    try:
        with engine.connect() as conn:
            # Remove duplicate (series, timestamp) rows, keeping one with smallest id
            conn.execute(text("""
                DELETE FROM collected_metrics a
                USING collected_metrics b
                WHERE a.id > b.id
                  AND a.service_type = b.service_type AND a.resource_id = b.resource_id
                  AND a.region = b.region AND a.metric_name = b.metric_name AND a.timestamp = b.timestamp
            """))
            conn.commit()
            conn.execute(text(
                "ALTER TABLE collected_metrics "
                "ADD CONSTRAINT uq_collected_metrics_series_ts "
                "UNIQUE (service_type, resource_id, region, metric_name, timestamp)"
            ))
            conn.commit()
    except ProgrammingError as e:
        err = str(e).lower()
        if "already exists" in err or "duplicate key" in err:
            pass  # constraint already there or dedupe left duplicates
        else:
            print(f"collected_metrics unique constraint migration: {e}")
    except Exception as e:
        print(f"collected_metrics unique constraint skipped: {e}")

    # Add panel_id column to dashboard_widgets if missing
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE dashboard_widgets "
                "ADD COLUMN panel_id UUID REFERENCES dashboard_panels(id) ON DELETE CASCADE"
            ))
            conn.commit()
    except ProgrammingError:
        pass  # column already exists or table doesn't exist yet
    except Exception as e:
        print(f"dashboard_widgets panel_id migration skipped: {e}")

    for attempt in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except Exception as e:
            if attempt == 9:
                raise
            print(f"DB not ready (attempt {attempt + 1}/10): {e}. Retrying in 3s…")
            time.sleep(3)

    # Migrate orphan widgets (no panel) into a default panel per user
    try:
        from ..db.models import DashboardWidget, DashboardPanel
        with SessionLocal() as db:
            orphan_emails = (
                db.query(DashboardWidget.user_email)
                .filter(DashboardWidget.panel_id.is_(None))
                .distinct()
                .all()
            )
            for (email,) in orphan_emails:
                panel = DashboardPanel(
                    user_email=email,
                    title="My Widgets",
                    sort_order=0,
                )
                db.add(panel)
                db.flush()
                db.query(DashboardWidget).filter(
                    DashboardWidget.user_email == email,
                    DashboardWidget.panel_id.is_(None),
                ).update({"panel_id": panel.id})
            db.commit()
    except Exception as e:
        print(f"Orphan widget migration skipped: {e}")
