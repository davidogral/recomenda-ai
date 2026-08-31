import os
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class UserRating(Base):
    __tablename__ = 'user_ratings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    movie_id = Column(Integer, nullable=False)
    rating = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    movie_title = Column(String, nullable=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./recomendai.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)