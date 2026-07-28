from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./novel_reader.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    from models import Job, Chapter, Novel, Episode  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Migrate missing columns (for existing databases)
    insp = inspect(engine)

    jobs_columns = [c["name"] for c in insp.get_columns("jobs")]
    if "progress_pct" not in jobs_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN progress_pct INTEGER"))
            conn.execute(text("ALTER TABLE jobs ADD COLUMN progress_msg VARCHAR(200)"))

    chapters_columns = [c["name"] for c in insp.get_columns("chapters")]
    if "source_name" not in chapters_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE chapters ADD COLUMN source_name VARCHAR(50)"))

    if "chapter_number" not in chapters_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE chapters ADD COLUMN chapter_number INTEGER"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
