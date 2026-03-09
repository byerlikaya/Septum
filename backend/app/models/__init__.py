from __future__ import annotations

"""SQLAlchemy ORM base for Septum models."""

from sqlalchemy.orm import DeclarativeBase, declared_attr


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore[misc]
        """Generate __tablename__ automatically from the class name."""
        return cls.__name__.lower()

    def __repr__(self) -> str:
        """Return a concise string representation for debugging."""
        return f"<{self.__class__.__name__}>"


