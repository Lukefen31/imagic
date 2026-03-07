"""Catalog / Collection ORM models.

A *Catalog* is a named grouping that can contain many photos (many-to-many).
This allows the user to organise images into virtual albums without moving
files on disk.
"""

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from imagic.models.database import Base

# Association table for the many-to-many relationship.
catalog_photos = Table(
    "catalog_photos",
    Base.metadata,
    Column("catalog_id", Integer, ForeignKey("catalogs.id", ondelete="CASCADE"), primary_key=True),
    Column("photo_id", Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True),
)


class Catalog(Base):
    """A named virtual album / collection.

    Attributes:
        id: Auto-increment primary key.
        name: Human-readable collection name.
        description: Optional longer description.
        created_at: Timestamp of creation.
        photos: Lazy-loaded list of ``Photo`` objects in this catalog.
    """

    __tablename__ = "catalogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    photos = relationship("Photo", secondary=catalog_photos, lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Catalog(id={self.id}, name={self.name!r})>"
