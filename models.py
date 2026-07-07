from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True)
    url = Column(Text, nullable=False)
    voice = Column(String(100), nullable=False)
    rate = Column(String(20), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    error_msg = Column(Text, nullable=True)
    progress_pct = Column(Integer, nullable=True)
    progress_msg = Column(String(200), nullable=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapter = relationship("Chapter", foreign_keys=[chapter_id])


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url_hash = Column(String(64), unique=True, nullable=False)
    url = Column(Text, nullable=False)
    voice = Column(String(100), nullable=False)
    rate = Column(String(20), nullable=False)
    title = Column(String(500), nullable=False)
    word_count = Column(Integer, nullable=False)
    audio_path = Column(Text, nullable=False)
    next_chapter_url = Column(Text, nullable=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    novel = relationship("Novel", back_populates="chapters")
    episodes = relationship("Episode", back_populates="chapter")


class Novel(Base):
    __tablename__ = "novels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    source_index_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    chapters = relationship("Chapter", back_populates="novel")
    episodes = relationship("Episode", back_populates="novel")


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id"), nullable=False)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)
    chapter_url = Column(Text, nullable=False)
    chapter_number = Column(Integer, nullable=True)
    listened_position_seconds = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    novel = relationship("Novel", back_populates="episodes")
    chapter = relationship("Chapter", back_populates="episodes")
