"""Unit tests for the database models."""

from pathlib import Path

from imagic.models.database import DatabaseManager
from imagic.models.enums import PhotoStatus
from imagic.models.photo import Photo


class TestDatabase:
    """Tests for database operations."""

    def test_create_and_query_photo(self, db: DatabaseManager) -> None:
        """Insert a Photo record and retrieve it."""
        session = db.get_session()
        try:
            photo = Photo(
                file_path="/tmp/test.cr2",
                file_name="test.cr2",
                file_extension=".cr2",
                file_size_bytes=1024,
                directory="/tmp",
                status=PhotoStatus.PENDING.value,
            )
            session.add(photo)
            session.commit()

            fetched = session.query(Photo).filter_by(file_name="test.cr2").first()
            assert fetched is not None
            assert fetched.file_path == "/tmp/test.cr2"
            assert fetched.status == PhotoStatus.PENDING.value
        finally:
            session.close()

    def test_unique_file_path(self, db: DatabaseManager) -> None:
        """Duplicate file_path should raise an integrity error."""
        import sqlalchemy

        session = db.get_session()
        try:
            for _ in range(2):
                session.add(Photo(
                    file_path="/tmp/dup.cr2",
                    file_name="dup.cr2",
                    file_extension=".cr2",
                    file_size_bytes=100,
                    directory="/tmp",
                ))
            try:
                session.commit()
                assert False, "Should have raised IntegrityError"
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
        finally:
            session.close()

    def test_status_update(self, db: DatabaseManager) -> None:
        """Updating photo status should persist."""
        session = db.get_session()
        try:
            photo = Photo(
                file_path="/tmp/status.cr2",
                file_name="status.cr2",
                file_extension=".cr2",
                file_size_bytes=512,
                directory="/tmp",
            )
            session.add(photo)
            session.commit()

            photo.status = PhotoStatus.KEEP.value
            photo.quality_score = 0.92
            session.commit()

            refreshed = session.get(Photo, photo.id)
            assert refreshed.status == PhotoStatus.KEEP.value
            assert refreshed.quality_score == 0.92
        finally:
            session.close()

    def test_photo_path_property(self, db: DatabaseManager) -> None:
        """``Photo.path`` should return a ``pathlib.Path``."""
        session = db.get_session()
        try:
            photo = Photo(
                file_path="/tmp/photos/img.nef",
                file_name="img.nef",
                file_extension=".nef",
                file_size_bytes=2048,
                directory="/tmp/photos",
            )
            session.add(photo)
            session.commit()
            assert isinstance(photo.path, Path)
            assert photo.path.name == "img.nef"
        finally:
            session.close()
