"""Tests for Procedure -> procedure_occurrence | observation | measurement."""

import duckdb
import pytest

from fhir_omop import cdm_remainder, ddl
from fhir_omop.mappings.procedure import map_procedure, subject_id
from fhir_omop.vocab import NO_MATCHING_CONCEPT, clear_caches


@pytest.fixture(autouse=True)
def _cache():
    clear_caches(); yield; clear_caches()


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE concept (concept_id INTEGER, concept_name VARCHAR, "
              "concept_code VARCHAR, vocabulary_id VARCHAR, standard_concept VARCHAR, "
              "domain_id VARCHAR)")
    c.executemany("INSERT INTO concept VALUES (?,?,?,?,?,?)", [
        (4249893, "Colonoscopy", "73761001", "SNOMED", "S", "Procedure"),
        (4058954, "Assessment score", "22222", "SNOMED", "S", "Measurement"),
        (4133224, "A social observation", "33333", "SNOMED", "S", "Observation"),
        (40213291, "A vaccine product", "44444", "SNOMED", "S", "Drug"),
    ])
    c.execute("CREATE TABLE concept_relationship (concept_id_1 INTEGER, "
              "concept_id_2 INTEGER, relationship_id VARCHAR)")
    return c


def procedure(code="73761001", **kw):
    base = {"resourceType": "Procedure", "id": "proc-1", "status": "completed",
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": code}]},
            "subject": {"reference": "urn:uuid:pat-1"},
            "performedPeriod": {"start": "2010-12-07T06:22:41-05:00",
                                "end": "2010-12-07T06:50:41-05:00"}}
    base.update(kw); return base


class TestDomainRouting:
    def test_procedure_domain_stays(self, con):
        m = map_procedure(con, procedure("73761001"), 1, 1, None)
        assert m.target_table == "procedure_occurrence"
        assert m.concept_id == 4249893

    def test_measurement_domain_is_routed(self, con):
        # SNOMED "procedure" codes routinely resolve to Measurement -- an
        # assessment score is performed like a procedure but is a measurement.
        m = map_procedure(con, procedure("22222"), 1, 1, None)
        assert m.target_table == "measurement"

    def test_observation_domain_is_routed(self, con):
        m = map_procedure(con, procedure("33333"), 1, 1, None)
        assert m.target_table == "observation"

    def test_domain_without_a_target_table_refuses_the_concept(self, con):
        # Drug-domain concepts (vaccine products) have no route from Procedure
        # in this ETL. The fact and source concept survive; the concept does not.
        m = map_procedure(con, procedure("44444"), 1, 1, None)
        assert m.concept_id == NO_MATCHING_CONCEPT
        assert m.source_concept_id == 40213291
        assert any("no target table" in i for i in m.issues)

    def test_unknown_code(self, con):
        assert map_procedure(con, procedure("00000"), 1, 1, None).concept_id == NO_MATCHING_CONCEPT


class TestStatusAndDates:
    @pytest.mark.parametrize("status", ["entered-in-error", "not-done", "stopped"])
    def test_unperformed_procedures_not_loaded(self, con, status):
        assert map_procedure(con, procedure(status=status), 1, 1, None) is None

    def test_performed_period(self, con):
        m = map_procedure(con, procedure(), 1, 1, None)
        assert m.start_date == "2010-12-07"
        assert m.end_date == "2010-12-07"

    def test_performed_datetime_form(self, con):
        p = procedure(); del p["performedPeriod"]
        p["performedDateTime"] = "2015-06-01T00:00:00Z"
        m = map_procedure(con, p, 1, 1, None)
        assert m.start_date == "2015-06-01"
        assert m.end_date is None

    def test_no_date_rejected(self, con):
        p = procedure(); del p["performedPeriod"]
        assert map_procedure(con, p, 1, 1, None) is None

    def test_end_date_dropped_when_routed_away(self, con):
        # measurement and observation have no end-date column.
        m = map_procedure(con, procedure("22222"), 1, 1, None)
        assert m.end_date is None


class TestRowShape:
    def test_procedure_row_width(self, con):
        m = map_procedure(con, procedure(), 1, 1, None)
        assert len(m.as_procedure_row()) == cdm_remainder.PROCEDURE_OCCURRENCE.count(",\n") + 1

    def test_observation_row_width(self, con):
        m = map_procedure(con, procedure("33333"), 1, 1, None)
        assert len(m.as_observation_row()) == ddl.OBSERVATION.count(",\n") + 1

    def test_measurement_row_width(self, con):
        m = map_procedure(con, procedure("22222"), 1, 1, None)
        assert len(m.as_measurement_row()) == ddl.MEASUREMENT.count(",\n") + 1

    def test_subject_resolves(self):
        assert subject_id(procedure()) == "pat-1"
