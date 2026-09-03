"""Tests for MedicationRequest -> drug_exposure."""

import duckdb
import pytest

from fhir_omop import ddl
from fhir_omop.mappings.drug import DRUG_TYPE_PRESCRIPTION_WRITTEN, map_medication_request
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
        (19078461, "Ibuprofen 200 MG Oral Tablet", "310965", "RxNorm", "S", "Drug"),
        (4000000, "A device, miscoded", "999999", "RxNorm", "S", "Device"),
    ])
    c.execute(
        "CREATE TABLE concept_relationship (concept_id_1 INTEGER, "
        "concept_id_2 INTEGER, relationship_id VARCHAR)"
    )
    return c


def request(code="310965", **kw):
    base = {
        "resourceType": "MedicationRequest", "id": "mr-1",
        "status": "active", "intent": "order",
        "medicationCodeableConcept": {"coding": [
            {"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": code}]},
        "subject": {"reference": "urn:uuid:pat-1"},
        "authoredOn": "2016-05-21T07:22:41-04:00",
    }
    base.update(kw)
    return base


class TestStatus:
    @pytest.mark.parametrize("status", ["entered-in-error", "draft", "cancelled"])
    def test_orders_never_issued_are_not_loaded(self, con, status):
        assert map_medication_request(con, request(status=status), 1, 1, None) is None

    def test_active_is_loaded(self, con):
        assert map_medication_request(con, request(), 1, 1, None) is not None

    def test_stopped_records_a_stop_reason(self, con):
        m = map_medication_request(con, request(status="stopped"), 1, 1, None)
        assert m.stop_reason == "stopped"

    def test_active_has_no_stop_reason(self, con):
        assert map_medication_request(con, request(), 1, 1, None).stop_reason is None


class TestConceptResolution:
    def test_rxnorm_resolves(self, con):
        m = map_medication_request(con, request(), 1, 1, None)
        assert m.drug_concept_id == 19078461
        assert m.drug_source_value == "310965"

    def test_non_drug_domain_is_refused(self, con):
        # RxNorm does not resolve exclusively into the Drug domain. A foreign
        # concept in drug_concept_id would corrupt every drug analysis.
        m = map_medication_request(con, request("999999"), 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT
        assert m.drug_source_concept_id == 4000000
        assert any("not Drug" in i for i in m.issues)

    def test_unknown_code(self, con):
        m = map_medication_request(con, request("00000"), 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT

    def test_unknown_code_system(self, con):
        r = request()
        r["medicationCodeableConcept"]["coding"][0]["system"] = "http://example.org/drugs"
        m = map_medication_request(con, r, 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT
        assert any("unknown medication code system" in i for i in m.issues)


class TestDatesAndProvenance:
    def test_authored_on_becomes_the_start(self, con):
        assert map_medication_request(con, request(), 1, 1, None).start_date == "2016-05-21"

    def test_missing_authored_on_is_rejected(self, con):
        r = request()
        del r["authoredOn"]
        assert map_medication_request(con, r, 1, 1, None) is None

    def test_missing_duration_is_reported_not_invented(self, con):
        # Synthea supplies no dispense duration, so end == start. This
        # UNDERSTATES exposure and must be visible, because it is the reason
        # chronic medications do not form long drug eras.
        m = map_medication_request(con, request(), 1, 1, None)
        assert m.end_date == m.start_date
        assert any("no dispense duration" in i for i in m.issues)

    def test_type_concept_records_this_is_an_order(self, con):
        # A prescription is not evidence the drug was taken. drug_type_concept_id
        # is where OMOP records that distinction.
        m = map_medication_request(con, request(), 1, 1, None)
        assert DRUG_TYPE_PRESCRIPTION_WRITTEN in m.as_row()


class TestRowShape:
    def test_row_width_matches_ddl(self, con):
        m = map_medication_request(con, request(), 1, 1, None)
        assert len(m.as_row()) == ddl.DRUG_EXPOSURE.count(",\n") + 1

    def test_visit_is_carried(self, con):
        m = map_medication_request(con, request(), 1, 1, 77)
        assert 77 in m.as_row()
