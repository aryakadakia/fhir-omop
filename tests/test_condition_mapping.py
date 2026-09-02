"""Tests for the Condition mapping, including domain routing."""

import duckdb
import pytest

from fhir_omop.vocab import clear_caches


@pytest.fixture(autouse=True)
def _isolate_concept_cache():
    """Concept resolution is memoised module-level; each test needs a clean
    cache or results leak between fixtures."""
    clear_caches()
    yield
    clear_caches()

from fhir_omop import ddl
from fhir_omop.mappings.condition import map_condition, subject_id
from fhir_omop.vocab import NO_MATCHING_CONCEPT


@pytest.fixture
def con():
    """A minimal in-memory vocabulary exercising every resolution path."""
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE concept (concept_id INTEGER, concept_name VARCHAR, "
        "concept_code VARCHAR, vocabulary_id VARCHAR, standard_concept VARCHAR, "
        "domain_id VARCHAR)"
    )
    c.executemany("INSERT INTO concept VALUES (?,?,?,?,?,?)", [
        (321042, "Cardiac arrest", "410429000", "SNOMED", "S", "Condition"),
        (4217975, "Normal pregnancy", "72892002", "SNOMED", "S", "Observation"),
        (40316773, "Prediabetes", "714628002", "SNOMED", None, "Condition"),
        (4311629, "Prediabetes std", "PD-STD", "SNOMED", "S", "Condition"),
    ])
    c.execute(
        "CREATE TABLE concept_relationship (concept_id_1 INTEGER, "
        "concept_id_2 INTEGER, relationship_id VARCHAR)"
    )
    c.execute("INSERT INTO concept_relationship VALUES (40316773, 4311629, 'Maps to')")
    return c


def condition(code, **kw):
    base = {
        "resourceType": "Condition", "id": "cond-1",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": code}]},
        "subject": {"reference": "urn:uuid:pat-1"},
        "onsetDateTime": "1965-11-15T06:22:41-05:00",
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
    }
    base.update(kw)
    return base


class TestSubjectReference:
    def test_resolves_urn_uuid_form(self):
        assert subject_id({"subject": {"reference": "urn:uuid:abc"}}) == "abc"

    def test_resolves_relative_form(self):
        assert subject_id({"subject": {"reference": "Patient/abc"}}) == "abc"

    def test_absent_subject(self):
        assert subject_id({}) is None


class TestVerificationStatus:
    @pytest.mark.parametrize("status", ["refuted", "entered-in-error"])
    def test_unasserted_conditions_are_not_loaded(self, con, status):
        # A refuted condition asserts the patient does NOT have it.
        result = map_condition(
            con, condition("410429000",
                           verificationStatus={"coding": [{"code": status}]}), 1, 1)
        assert result is None

    def test_confirmed_condition_is_loaded(self, con):
        assert map_condition(con, condition("410429000"), 1, 1) is not None


class TestConceptResolution:
    def test_standard_concept_resolves_directly(self, con):
        m = map_condition(con, condition("410429000"), 1, 1)
        assert m.condition_concept_id == 321042
        assert m.condition_source_concept_id == 321042
        assert m.condition_source_value == "410429000"

    def test_non_standard_concept_follows_maps_to(self, con):
        # Skipping this step makes prediabetes patients silently invisible.
        m = map_condition(con, condition("714628002"), 1, 1)
        assert m.condition_concept_id == 4311629      # standard target
        assert m.condition_source_concept_id == 40316773  # code as received

    def test_unknown_code_maps_to_zero(self, con):
        m = map_condition(con, condition("999999999"), 1, 1)
        assert m.condition_concept_id == NO_MATCHING_CONCEPT

    def test_unknown_code_system_maps_to_zero(self, con):
        c = condition("410429000")
        c["code"]["coding"][0]["system"] = "http://example.org/local-codes"
        m = map_condition(con, c, 1, 1)
        assert m.condition_concept_id == NO_MATCHING_CONCEPT
        assert any("unknown code system" in i for i in m.issues)


class TestDomainRouting:
    def test_condition_domain_stays_in_condition_occurrence(self, con):
        m = map_condition(con, condition("410429000"), 1, 1)
        assert m.target_table == "condition_occurrence"

    def test_observation_domain_concept_is_routed_out(self, con):
        # "Normal pregnancy" arrives as a FHIR Condition but is an
        # Observation-domain concept in OMOP, so it must NOT land in
        # condition_occurrence.
        m = map_condition(con, condition("72892002"), 1, 1)
        assert m.target_table == "observation"
        assert any("routed to observation" in i for i in m.issues)

    def test_observation_row_width_matches_ddl(self, con):
        m = map_condition(con, condition("72892002"), 1, 1)
        assert len(m.as_observation_row()) == ddl.OBSERVATION.count(",\n") + 1


class TestDates:
    def test_onset_preferred_over_recorded(self, con):
        m = map_condition(con, condition("410429000", recordedDate="1999-01-01"), 1, 1)
        assert m.condition_start_date == "1965-11-15"

    def test_falls_back_to_recorded_date(self, con):
        c = condition("410429000")
        del c["onsetDateTime"]
        c["recordedDate"] = "1999-01-01T00:00:00Z"
        m = map_condition(con, c, 1, 1)
        assert m.condition_start_date == "1999-01-01"
        assert any("fell back to recordedDate" in i for i in m.issues)

    def test_no_usable_start_date_is_rejected(self, con):
        c = condition("410429000")
        del c["onsetDateTime"]
        assert map_condition(con, c, 1, 1) is None

    def test_abatement_becomes_end_date(self, con):
        m = map_condition(
            con, condition("410429000", abatementDateTime="1970-02-03T00:00:00Z"), 1, 1)
        assert m.condition_end_date == "1970-02-03"

    def test_row_width_matches_ddl(self, con):
        m = map_condition(con, condition("410429000"), 1, 1)
        assert len(m.as_row()) == ddl.CONDITION_OCCURRENCE.count(",\n") + 1
