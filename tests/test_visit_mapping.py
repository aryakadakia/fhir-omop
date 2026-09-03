"""Tests for Encounter -> visit_occurrence."""

import pytest

from fhir_omop import ddl
from fhir_omop.mappings.visit import (
    VISIT_EMERGENCY, VISIT_INPATIENT, VISIT_OUTPATIENT, map_encounter,
)


def encounter(cls="AMB", **kw):
    base = {
        "resourceType": "Encounter", "id": "enc-1", "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                  "code": cls},
        "period": {"start": "1965-11-15T06:22:41-05:00",
                   "end": "1965-11-15T08:07:41-05:00"},
    }
    base.update(kw)
    return base


class TestVisitClass:
    @pytest.mark.parametrize("code,expected", [
        ("IMP", VISIT_INPATIENT), ("ACUTE", VISIT_INPATIENT),
        ("EMER", VISIT_EMERGENCY),
        ("AMB", VISIT_OUTPATIENT), ("HH", VISIT_OUTPATIENT),
    ])
    def test_actcode_maps_to_visit_concept(self, code, expected):
        assert map_encounter(encounter(code), 1, 1).visit_concept_id == expected

    def test_unrecognised_class_is_not_forced_into_a_bucket(self):
        # ActCode has far more values than OMOP has visit types. Guessing the
        # nearest would silently misclassify visits.
        m = map_encounter(encounter("SOMETHING_ELSE"), 1, 1)
        assert m.visit_concept_id == 0
        assert m.visit_source_value == "SOMETHING_ELSE"
        assert any("no OMOP visit concept" in i for i in m.issues)

    def test_absent_class(self):
        e = encounter()
        del e["class"]
        assert map_encounter(e, 1, 1).visit_concept_id == 0


class TestStatus:
    @pytest.mark.parametrize("status", ["cancelled", "entered-in-error", "planned"])
    def test_encounters_that_did_not_happen_are_not_loaded(self, status):
        assert map_encounter(encounter(status=status), 1, 1) is None

    def test_finished_is_loaded(self):
        assert map_encounter(encounter(), 1, 1) is not None

    def test_in_progress_is_loaded(self):
        assert map_encounter(encounter(status="in-progress"), 1, 1) is not None


class TestPeriod:
    def test_dates_are_truncated_from_datetimes(self):
        m = map_encounter(encounter(), 1, 1)
        assert m.visit_start_date == "1965-11-15"
        assert m.visit_end_date == "1965-11-15"

    def test_missing_end_closes_on_the_start_date(self):
        # visit_end_date is NOT NULL. An ongoing encounter is closed on its
        # start date rather than left open or given an invented duration.
        e = encounter(period={"start": "2020-01-01T00:00:00Z"})
        m = map_encounter(e, 1, 1)
        assert m.visit_end_date == "2020-01-01"
        assert any("closed on start date" in i for i in m.issues)

    def test_missing_start_is_rejected(self):
        assert map_encounter(encounter(period={}), 1, 1) is None

    def test_absent_period_is_rejected(self):
        e = encounter()
        del e["period"]
        assert map_encounter(e, 1, 1) is None


class TestRowShape:
    def test_row_width_matches_ddl(self):
        m = map_encounter(encounter(), 1, 1)
        assert len(m.as_row()) == ddl.VISIT_OCCURRENCE.count(",\n") + 1
