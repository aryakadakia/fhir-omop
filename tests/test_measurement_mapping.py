"""Tests for Observation -> measurement | observation.

This module produces 260,000 of the database's rows and had no unit tests
until a coverage audit found the gap. The domain-routing behaviour below was
previously guarded only by an integration-level data-quality check.
"""

import duckdb
import pytest

from fhir_omop import ddl
from fhir_omop.mappings.measurement import map_observation
from fhir_omop.vocab import NO_MATCHING_CONCEPT, clear_caches


@pytest.fixture(autouse=True)
def _isolate_concept_cache():
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE concept (concept_id INTEGER, concept_name VARCHAR, "
        "concept_code VARCHAR, vocabulary_id VARCHAR, standard_concept VARCHAR, "
        "domain_id VARCHAR)"
    )
    c.executemany("INSERT INTO concept VALUES (?,?,?,?,?,?)", [
        (3036277, "Body height", "8302-2", "LOINC", "S", "Measurement"),
        (4275495, "Smoking status", "72166-2", "LOINC", "S", "Observation"),
        (3000905, "A note type", "11506-3", "LOINC", "S", "Note"),
        (4163872, "A procedure", "99999-9", "LOINC", "S", "Procedure"),
        (8582, "centimeter", "cm", "UCUM", "S", "Unit"),
    ])
    c.execute(
        "CREATE TABLE concept_relationship (concept_id_1 INTEGER, "
        "concept_id_2 INTEGER, relationship_id VARCHAR)"
    )
    return c


def observation(code="8302-2", **kw):
    base = {
        "resourceType": "Observation", "id": "obs-1", "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
        "subject": {"reference": "urn:uuid:pat-1"},
        "effectiveDateTime": "2010-03-01T06:22:41-05:00",
        "valueQuantity": {"value": 173.9, "unit": "cm",
                          "system": "http://unitsofmeasure.org", "code": "cm"},
    }
    base.update(kw)
    return base


class TestDomainRouting:
    def test_measurement_domain_goes_to_measurement(self, con):
        m = map_observation(con, observation("8302-2"), 1, 1, None)
        assert m.target_table == "measurement"
        assert m.concept_id == 3036277

    def test_observation_domain_goes_to_observation(self, con):
        m = map_observation(con, observation("72166-2"), 1, 1, None)
        assert m.target_table == "observation"
        assert m.concept_id == 4275495

    @pytest.mark.parametrize("code,concept", [("11506-3", 3000905), ("99999-9", 4163872)])
    def test_domains_without_a_target_table_refuse_the_concept(self, con, code, concept):
        # LOINC carries Note- and Procedure-domain concepts, which belong in
        # `note` and `procedure_occurrence`. This ETL populates neither, so the
        # fact is kept but the concept is refused -- writing it to observation
        # anyway would violate the domain rule. 102 real rows hit this.
        m = map_observation(con, observation(code), 1, 1, None)
        assert m.concept_id == NO_MATCHING_CONCEPT
        assert m.source_concept_id == concept       # source preserved
        assert m.target_table == "observation"
        assert any("no target table" in i for i in m.issues)

    def test_unresolved_code_defaults_to_observation(self, con):
        m = map_observation(con, observation("00000-0"), 1, 1, None)
        assert m.concept_id == NO_MATCHING_CONCEPT
        assert m.target_table == "observation"


class TestValues:
    def test_quantitative_value_and_unit(self, con):
        m = map_observation(con, observation(), 1, 1, None)
        assert m.value_as_number == 173.9
        assert m.unit_source_value == "cm"
        assert m.unit_concept_id == 8582

    def test_qualitative_value_from_codeable_concept(self, con):
        obs = observation("72166-2")
        del obs["valueQuantity"]
        obs["valueCodeableConcept"] = {"coding": [{"system": "http://snomed.info/sct",
                                                   "code": "266919005"}]}
        m = map_observation(con, obs, 1, 1, None)
        assert m.value_as_number is None
        assert m.value_as_string == "266919005"

    def test_value_string(self, con):
        obs = observation("72166-2")
        del obs["valueQuantity"]
        obs["valueString"] = "never smoked"
        assert map_observation(con, obs, 1, 1, None).value_as_string == "never smoked"

    def test_observation_with_no_value_is_still_loaded(self, con):
        # A result can legitimately be absent; the fact that the test was done
        # is itself data.
        obs = observation()
        del obs["valueQuantity"]
        m = map_observation(con, obs, 1, 1, None)
        assert m is not None
        assert m.value_as_number is None

    def test_unknown_unit_maps_to_zero(self, con):
        obs = observation()
        obs["valueQuantity"]["code"] = "furlongs"
        assert map_observation(con, obs, 1, 1, None).unit_concept_id == NO_MATCHING_CONCEPT


class TestStatusAndDates:
    @pytest.mark.parametrize("status", ["entered-in-error", "cancelled"])
    def test_excluded_statuses_are_not_loaded(self, con, status):
        assert map_observation(con, observation(status=status), 1, 1, None) is None

    def test_effective_date_is_truncated(self, con):
        assert map_observation(con, observation(), 1, 1, None).date == "2010-03-01"

    def test_falls_back_to_issued(self, con):
        obs = observation()
        del obs["effectiveDateTime"]
        obs["issued"] = "2011-05-06T00:00:00Z"
        assert map_observation(con, obs, 1, 1, None).date == "2011-05-06"

    def test_no_date_is_rejected(self, con):
        obs = observation()
        del obs["effectiveDateTime"]
        assert map_observation(con, obs, 1, 1, None) is None


class TestRowShape:
    def test_measurement_row_width(self, con):
        m = map_observation(con, observation(), 1, 1, None)
        assert len(m.as_measurement_row()) == ddl.MEASUREMENT.count(",\n") + 1

    def test_observation_row_width(self, con):
        m = map_observation(con, observation("72166-2"), 1, 1, None)
        assert len(m.as_observation_row()) == ddl.OBSERVATION.count(",\n") + 1

    def test_visit_is_carried_through(self, con):
        m = map_observation(con, observation(), 1, 1, 4242)
        assert m.visit_occurrence_id == 4242
        assert 4242 in m.as_measurement_row()
