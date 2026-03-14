"""
db/base.py — SQLAlchemy 2.0 declarative base.
Import Base from here for all models.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass
