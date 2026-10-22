"""Every series the Withings provider stores reaches webhook consumers, and a
series that has no webhook event is counted and logged instead of dropped quietly.

Before this, `bone_mass`, `body_water_mass`, `withings_pulse_wave_velocity` and
`withings_metabolic_age` were stored by the Withings provider and never sent:
`on_timeseries_batch_saved` returned without a word for any series missing from
`SERIES_TYPE_TO_GROUP_EVENT`.
"""

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.constants.webhooks.events import SERIES_TYPE_TO_GRANULAR_EVENT, SERIES_TYPE_TO_GROUP_EVENT
from app.schemas.enums import SeriesType
from app.schemas.enums.series_types import get_series_type_unit
from app.schemas.webhooks.event_types import EVENT_TYPE_GROUPS, WebhookEventType
from app.services.outgoing_webhooks import events
from app.services.providers.withings.coverage import MEASURE_TYPE_MAP, MEASURE_UNIT_FACTOR, TIMESERIES


class TestWithingsSeriesAreEmitted:
    def test_every_series_the_provider_stores_has_a_group_event(self) -> None:
        missing = sorted(s.value for s in TIMESERIES if s.value not in SERIES_TYPE_TO_GROUP_EVENT)

        assert missing == []

    @pytest.mark.parametrize(
        ("series", "group", "granular"),
        [
            ("bone_mass", WebhookEventType.BODY_COMPOSITION_CREATED, WebhookEventType.SERIES_BONE_MASS),
            ("body_water_mass", WebhookEventType.BODY_COMPOSITION_CREATED, WebhookEventType.SERIES_BODY_WATER_MASS),
            (
                "withings_pulse_wave_velocity",
                WebhookEventType.FITNESS_METRICS_CREATED,
                WebhookEventType.SERIES_WITHINGS_PULSE_WAVE_VELOCITY,
            ),
            (
                "withings_metabolic_age",
                WebhookEventType.FITNESS_METRICS_CREATED,
                WebhookEventType.SERIES_WITHINGS_METABOLIC_AGE,
            ),
        ],
    )
    def test_the_four_body_series_have_their_events(
        self, series: str, group: WebhookEventType, granular: WebhookEventType
    ) -> None:
        assert SERIES_TYPE_TO_GROUP_EVENT[series] == group
        assert SERIES_TYPE_TO_GRANULAR_EVENT[series] == granular
        assert granular in EVENT_TYPE_GROUPS[group]


class TestTheMapsAgree:
    @pytest.mark.parametrize("series", sorted(SERIES_TYPE_TO_GROUP_EVENT))
    def test_a_grouped_series_is_listed_under_its_group(self, series: str) -> None:
        """What `EVENT_TYPE_GROUPS` shows subscribers must match what is sent."""
        granular = SERIES_TYPE_TO_GRANULAR_EVENT.get(series)
        group = SERIES_TYPE_TO_GROUP_EVENT[series]

        if granular is None or granular == group:
            pytest.skip(f"{series} has no granular event of its own")
        assert granular in EVENT_TYPE_GROUPS[group]


class TestWithingsGlucoseUnit:
    def test_glucose_is_taken_as_sent(self) -> None:
        """Withings documents meastype 119 as glucose in mg/dL (notification
        content, appli 58), which is already the unit of `blood_glucose` - so no
        factor belongs in MEASURE_UNIT_FACTOR. Converting it as if it were mmol/L
        would inflate every reading eighteenfold."""
        assert MEASURE_TYPE_MAP[119] == SeriesType.blood_glucose
        assert get_series_type_unit(SeriesType.blood_glucose) == "mg_dl"
        assert 119 not in MEASURE_UNIT_FACTOR


def _samples(series: str, unit: str) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": "2026-09-16T07:00:00+00:00",
            "zone_offset": "+00:00",
            "type": series,
            "value": 3.2,
            "unit": unit,
            "source": {"provider": "withings", "device": None},
            "is_daily_total": None,
        }
    ]


def _save(series: str, unit: str) -> None:
    events.on_timeseries_batch_saved(
        user_id=uuid4(),
        provider="withings",
        series_type=series,
        sample_count=1,
        start_time="2026-09-16T07:00:00+00:00",
        end_time="2026-09-16T07:00:00+00:00",
        samples=_samples(series, unit),
    )


class TestAnUnmappedSeriesIsCountedAndLogged:
    def test_it_is_not_dispatched_and_is_counted_and_logged(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dispatch = MagicMock()
        monkeypatch.setattr(events, "_dispatch", dispatch)
        before = events.UNEMITTED_SERIES_BATCHES["elevation"]

        _save("elevation", "meters")

        dispatch.assert_not_called()
        assert events.UNEMITTED_SERIES_BATCHES["elevation"] == before + 1
        out = capsys.readouterr().out
        assert "webhook_series_not_emitted" in out
        assert "elevation" in out

    def test_a_mapped_series_is_dispatched_and_not_counted(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The control: the counting branch is the unmapped one only."""
        dispatch = MagicMock()
        monkeypatch.setattr(events, "_dispatch", dispatch)
        before = sum(events.UNEMITTED_SERIES_BATCHES.values())

        _save("bone_mass", "kg")

        assert [c.args[0] for c in dispatch.call_args_list] == [
            WebhookEventType.BODY_COMPOSITION_CREATED,
            WebhookEventType.SERIES_BONE_MASS,
        ]
        assert sum(events.UNEMITTED_SERIES_BATCHES.values()) == before
        assert "webhook_series_not_emitted" not in capsys.readouterr().out
