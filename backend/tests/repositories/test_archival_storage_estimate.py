"""The storage estimate counts Open Wearables' own schema and nothing else.

In a database Open Wearables shares, every other schema belongs to someone else:
counting it would put their table sizes - and, through the estimate, their
existence - into Open Wearables' own figures.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.archival_repository import DataPointSeriesArchiveRepository

_FILL = "SELECT g AS n, repeat('x', 500) AS pad FROM generate_series(1, 2000) AS g"


def test_a_table_in_another_schema_is_not_counted(db: Session) -> None:
    repo = DataPointSeriesArchiveRepository()
    before = repo.get_storage_estimate(db)["other_tables_bytes"]

    db.execute(text("CREATE SCHEMA someone_else"))
    db.execute(text(f"CREATE TABLE someone_else.their_data AS {_FILL}"))

    assert repo.get_storage_estimate(db)["other_tables_bytes"] == before


def test_a_table_in_its_own_schema_is_counted(db: Session) -> None:
    """The control: without it, the arm above passes for an estimate that counts
    nothing at all."""
    repo = DataPointSeriesArchiveRepository()
    before = repo.get_storage_estimate(db)["other_tables_bytes"]

    db.execute(text(f"CREATE TABLE own_extra AS {_FILL}"))

    assert repo.get_storage_estimate(db)["other_tables_bytes"] > before
