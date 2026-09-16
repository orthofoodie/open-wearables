"""A database password may hold any character and still reach the server as written.

`Settings.db_uri` puts the password inside a URL, and unencoded, `@ / : % #` end or
bend it: SQLAlchemy finds the wrong host, or decodes a `%xx` that was never an
encoding. A production secret is exactly the kind of string that holds them.
"""

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings

PLACE = {"db_host": "db.example", "db_port": 6543, "db_name": "ow_db", "db_user": "ow_user"}


@pytest.mark.parametrize(
    "password",
    ["p@ss/w%rd#x", "a:b@c/d?e#f", "with space", "%41%", "a%%b", "plain-token_123"],
)
def test_the_uri_parses_back_to_the_password_and_the_right_place(password: str) -> None:
    url = make_url(Settings(**PLACE, db_password=password).db_uri)

    assert (url.username, url.password, url.host, url.port, url.database) == (
        "ow_user",
        password,
        "db.example",
        6543,
        "ow_db",
    )


def test_a_password_with_nothing_to_encode_is_unchanged() -> None:
    """The control: an existing deployment's URL is byte-identical."""
    settings = Settings(**PLACE, db_password="open-wearables")

    assert settings.db_uri == "postgresql+psycopg://ow_user:open-wearables@db.example:6543/ow_db"
