"""DB_SCHEMA and the pool size: upstream's defaults unchanged, and a schema that
reaches every connection when one is set."""

import pytest
from pydantic import ValidationError

from app.config import Settings


class TestDbSchema:
    def test_the_default_is_public(self) -> None:
        assert Settings().db_schema == "public"

    def test_public_adds_no_connection_option(self) -> None:
        """Upstream's behaviour exactly: no `options` at all, so a PGOPTIONS the
        operator set still applies."""
        assert Settings(db_schema="public").db_connect_args == {}

    def test_a_schema_sets_the_search_path_on_every_connection(self) -> None:
        settings = Settings(db_schema="openwearables")

        assert settings.db_connect_args == {"options": "-c search_path=openwearables"}

    @pytest.mark.parametrize(
        "value",
        ["", "OpenWearables", "open-wearables", "ow; DROP SCHEMA public", "1ow", "a" * 64, "ow,public", "ow public"],
    )
    def test_anything_but_a_plain_identifier_is_refused(self, value: str) -> None:
        """It is interpolated into a libpq option and a CREATE SCHEMA; a comma
        would widen the search_path, a space would add a libpq option."""
        with pytest.raises(ValidationError, match="DB_SCHEMA"):
            Settings(db_schema=value)


class TestPool:
    def test_the_defaults_are_upstreams(self) -> None:
        settings = Settings()

        assert (settings.db_pool_size, settings.db_max_overflow) == (20, 30)

    def test_it_is_configurable(self) -> None:
        settings = Settings(db_pool_size=2, db_max_overflow=0)

        assert (settings.db_pool_size, settings.db_max_overflow) == (2, 0)

    def test_a_pool_of_nothing_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            Settings(db_pool_size=0)
