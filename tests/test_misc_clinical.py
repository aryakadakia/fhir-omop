"""Tests for MedicationAdministration, AllergyIntolerance and Device.

The reference field each uses to name its patient is DIFFERENT, and getting it
wrong orphans every row silently. That is the main thing pinned here.
"""

import duckdb
import pytest

from fhir_omop import cdm_remainder, ddl
from fhir_omop.mappings.misc_clinical import (
    DRUG_TYPE_ADMINISTRATION, administration_encounter, administration_subject,
    allergy_patient, device_patient, map_allergy, map_device,
    map_medication_administration,
)
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
        (1736854, "Cisplatin 50 MG", "1736854", "RxNorm", "S", "Drug"),
        (4188027, "Allergy to mould", "419474003", "SNOMED", "S", "Observation"),
        (4155514, "Implantable defibrillator", "72506001", "SNOMED", "S", "Device"),
        (9999999, "Wrong domain", "000", "SNOMED", "S", "Condition"),
    ])
    c.execute("CREATE TABLE concept_relationship (concept_id_1 INTEGER, "
              "concept_id_2 INTEGER, relationship_id VARCHAR)")
    return c


class TestReferenceFields:
    """Three resources, three different field names for "the patient"."""

    def test_medication_administration_uses_subject(self):
        r = {"subject": {"reference": "urn:uuid:pat-1"}}
        assert administration_subject(r) == "pat-1"

    def test_medication_administration_encounter_is_context_not_encounter(self):
        # .context, not .encounter. Reading .encounter loses every visit link.
        r = {"context": {"reference": "urn:uuid:enc-1"}}
        assert administration_encounter(r) == "enc-1"

    def test_medication_administration_falls_back_to_encounter(self):
        r = {"encounter": {"reference": "urn:uuid:enc-2"}}
        assert administration_encounter(r) == "enc-2"

    def test_allergy_uses_patient_not_subject(self):
        assert allergy_patient({"patient": {"reference": "urn:uuid:p"}}) == "p"
        assert allergy_patient({"subject": {"reference": "urn:uuid:wrong"}}) is None

    def test_device_uses_patient(self):
        assert device_patient({"patient": {"reference": "urn:uuid:p"}}) == "p"


class TestMedicationAdministration:
    def admin(self, **kw):
        base = {"resourceType": "MedicationAdministration", "id": "ma-1",
                "status": "completed",
                "medicationCodeableConcept": {"coding": [
                    {"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                     "code": "1736854"}]},
                "effectiveDateTime": "1964-10-24T10:50:10-04:00"}
        base.update(kw); return base

    def test_resolves(self, con):
        m = map_medication_administration(con, self.admin(), 1, 1, None)
        assert m.drug_concept_id == 1736854
        assert m.start_date == "1964-10-24"

    def test_type_concept_distinguishes_administration_from_prescription(self, con):
        # An administration is direct evidence the drug entered the patient;
        # a prescription is not. Same table, different evidence strength.
        m = map_medication_administration(con, self.admin(), 1, 1, None)
        assert DRUG_TYPE_ADMINISTRATION in m.as_row()

    @pytest.mark.parametrize("status", ["entered-in-error", "not-done", "cancelled"])
    def test_excluded_statuses(self, con, status):
        assert map_medication_administration(con, self.admin(status=status), 1, 1, None) is None

    def test_effective_period_fallback(self, con):
        a = self.admin(); del a["effectiveDateTime"]
        a["effectivePeriod"] = {"start": "1970-01-02T00:00:00Z"}
        assert map_medication_administration(con, a, 1, 1, None).start_date == "1970-01-02"

    def test_no_date_rejected(self, con):
        a = self.admin(); del a["effectiveDateTime"]
        assert map_medication_administration(con, a, 1, 1, None) is None

    def test_non_drug_domain_refused(self, con):
        a = self.admin()
        a["medicationCodeableConcept"]["coding"][0] = {
            "system": "http://snomed.info/sct", "code": "000"}
        m = map_medication_administration(con, a, 1, 1, None)
        assert m.drug_concept_id == NO_MATCHING_CONCEPT

    def test_row_width(self, con):
        m = map_medication_administration(con, self.admin(), 1, 1, None)
        assert len(m.as_row()) == ddl.DRUG_EXPOSURE.count(",\n") + 1


class TestAllergy:
    def allergy(self, **kw):
        base = {"resourceType": "AllergyIntolerance", "id": "al-1",
                "verificationStatus": {"coding": [{"code": "confirmed"}]},
                "criticality": "low",
                "code": {"coding": [{"system": "http://snomed.info/sct",
                                     "code": "419474003"}]},
                "recordedDate": "2003-04-05T10:13:00-05:00"}
        base.update(kw); return base

    def test_resolves_into_observation_domain(self, con):
        # There is no allergy table in the CDM; allergies are observations.
        m = map_allergy(con, self.allergy(), 1, 1)
        assert m.observation_concept_id == 4188027

    @pytest.mark.parametrize("status", ["refuted", "entered-in-error"])
    def test_unasserted_allergies_not_loaded(self, con, status):
        a = self.allergy(verificationStatus={"coding": [{"code": status}]})
        assert map_allergy(con, a, 1, 1) is None

    def test_criticality_is_retained(self, con):
        # Clinically meaningful and has no dedicated CDM column.
        assert map_allergy(con, self.allergy(), 1, 1).value_as_string == "low"

    def test_onset_fallback(self, con):
        a = self.allergy(); del a["recordedDate"]
        a["onsetDateTime"] = "1999-01-01T00:00:00Z"
        assert map_allergy(con, a, 1, 1).date == "1999-01-01"

    def test_row_width(self, con):
        m = map_allergy(con, self.allergy(), 1, 1)
        assert len(m.as_row()) == ddl.OBSERVATION.count(",\n") + 1


class TestDevice:
    def device(self, **kw):
        base = {"resourceType": "Device", "id": "dev-1", "status": "active",
                "udiCarrier": [{"deviceIdentifier": "38205793067477"}],
                "manufactureDate": "1965-10-25T07:22:41-04:00",
                "type": {"coding": [{"system": "http://snomed.info/sct",
                                     "code": "72506001"}]}}
        base.update(kw); return base

    def test_resolves(self, con):
        m = map_device(con, self.device(), 1, 1)
        assert m.device_concept_id == 4155514
        assert m.unique_device_id == "38205793067477"

    def test_manufacture_date_is_flagged_as_not_an_implant_date(self, con):
        # device_exposure_start_date will be read as an implant date. It is
        # not one, and the source has nothing better.
        m = map_device(con, self.device(), 1, 1)
        assert m.start_date == "1965-10-25"
        assert any("manufactureDate" in i for i in m.issues)

    def test_no_date_rejected(self, con):
        d = self.device(); del d["manufactureDate"]
        assert map_device(con, d, 1, 1) is None

    def test_entered_in_error_excluded(self, con):
        assert map_device(con, self.device(status="entered-in-error"), 1, 1) is None

    def test_row_width(self, con):
        m = map_device(con, self.device(), 1, 1)
        assert len(m.as_row()) == cdm_remainder.DEVICE_EXPOSURE.count(",\n") + 1
