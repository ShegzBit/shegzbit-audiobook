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
    columns = [c["name"] for c in insp.get_columns("jobs")]
    if "progress_pct" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN progress_pct INTEGER"))
            conn.execute(text("ALTER TABLE jobs ADD COLUMN progress_msg VARCHAR(200)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
