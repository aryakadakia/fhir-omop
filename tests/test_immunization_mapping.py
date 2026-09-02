"""Tests for the Immunization mapping."""

import duckdb
import pytest

from fhir_omop import ddl
from fhir_omop.mappings.immunization import map_immunization, patient_id
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
        (40213154, "Influenza vaccine", "140", "CVX", "S", "Drug"),
        (45959923, "A misfiled CVX code", "999", "CVX", "S", "Observation"),
    ])
    c.execute(
        "CREATE TABLE concept_relationship (concept_id_1 INTEGER, "
        "concept_id_2 INTEGER, relationship_id VARCHAR)"
    )
    return c


def immunization(code="140", **kw):
    base = {
        "resourceType": "Immunization", "id": "imm-1", "status": "completed",
        "vaccineCode": {"coding": [
            {"system": "http://hl7.org/fhir/sid/cvx", "code": code}]},
        "patient": {"reference": "urn:uuid:pat-1"},
        "occurrenceDateTime": "2010-03-01T06:22:41-05:00",
    }
    base.update(kw)
    return base


class TestPatientReference:
    def test_uses_patient_not_subject(self):
        # Immunization is the odd one out: .patient, not .subject. Reading
        # .subject here would orphan every immunization silently.
        assert patient_id(immunization()) == "pat-1"

    def test_subject_is_not_consulted(self):
        imm = immunization()
        del imm["patient"]
        imm["subject"] = {"reference": "urn:uuid:wrong"}
        assert patient_id(imm) is None


class TestStatus:
    @pytest.mark.parametrize("status", ["entered-in-error", "not-done"])
    def test_ungiven_vaccines_are_not_loaded(self, con, status):
        assert map_immunization(con, immunization(status=status), 1, 1, None) is None

    def test_completed_is_loaded(self, con):
        assert map_immunization(con, immunization(), 1, 1, None) is not None


class TestConceptResolution:
    def test_cvx_resolves_to_drug_concept(self, con):
        m = map_immunization(con, immunization("140"), 1, 1, None)
        assert m.drug_concept_id == 40213154
        assert m.drug_source_value == "140"

    def test_unknown_cvx_code_maps_to_zero(self, con):
        m = map_immunization(con, immunization("00000"), 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT

    def test_non_drug_domain_concept_is_refused(self, con):
        # A CVX code resolving outside the Drug domain must not land in
        # drug_concept_id; the source concept is still preserved.
        m = map_immunization(con, immunization("999"), 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT
        assert m.drug_source_concept_id == 45959923
        assert any("not Drug" in i for i in m.issues)

    def test_unknown_code_system(self, con):
        imm = immunization()
        imm["vaccineCode"]["coding"][0]["system"] = "http://example.org/vaccines"
        m = map_immunization(con, imm, 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT
        assert any("unknown vaccine code system" in i for i in m.issues)


class TestDates:
    def test_occurrence_becomes_both_ends(self, con):
        # A vaccination is a point event -- start == end is genuinely true
        # here, not a missing-duration fallback.
        m = map_immunization(con, immunization(), 1, 1, None)
        assert m.start_date == "2010-03-01"
        assert m.end_date == "2010-03-01"

    def test_missing_occurrence_is_refused(self, con):
        imm = immunization()
        del imm["occurrenceDateTime"]
        assert map_immunization(con, imm, 1, 1, None) is None

    def test_occurrence_string_is_refused_with_a_reason(self, con):
        imm = immunization()
        del imm["occurrenceDateTime"]
        imm["occurrenceString"] = "approximately 2010"
        assert map_immunization(con, imm, 1, 1, None) is None


class TestRowShape:
    def test_row_width_matches_drug_exposure_ddl(self, con):
        m = map_immunization(con, immunization(), 1, 1, None)
        assert len(m.as_row()) == ddl.DRUG_EXPOSURE.count(",\n") + 1

    def test_lot_number_is_carried(self, con):
        m = map_immunization(con, immunization(lotNumber="AB123"), 1, 1, None)
        assert m.lot_number == "AB123"
        assert "AB123" in m.as_row()
