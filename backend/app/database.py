from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
import os

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:root@localhost:5432/nexsidi')

# Production-grade connection pooling
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,              # 10 permanent connections
    max_overflow=20,           # 20 additional when needed
    pool_pre_ping=True,        # Validate connections
    pool_recycle=3600,         # Recycle hourly
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
