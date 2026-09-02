"""Tests for the Patient -> person mapping.

Each test pins a decision documented in mappings/person.py. If someone later
"simplifies" one of those decisions, a test should fail and say why.
"""

import pytest

from fhir_omop.mappings.person import map_patient, _split_birth_date
from fhir_omop.vocab import GENDER_FEMALE, GENDER_MALE, NO_MATCHING_CONCEPT, map_gender


class TestGenderMapping:
    def test_male_and_female_map_to_standard_concepts(self):
        assert map_gender("male") == GENDER_MALE
        assert map_gender("female") == GENDER_FEMALE

    def test_gender_is_case_insensitive(self):
        assert map_gender("Female") == GENDER_FEMALE

    @pytest.mark.parametrize("code", ["other", "unknown"])
    def test_valid_fhir_codes_without_omop_concepts_map_to_zero(self, code):
        # These are legal FHIR values. Mapping them to 0 is correct; mapping
        # them to male or female would fabricate data.
        assert map_gender(code) == NO_MATCHING_CONCEPT

    def test_absent_gender_maps_to_zero(self):
        assert map_gender(None) == NO_MATCHING_CONCEPT


class TestBirthDatePrecision:
    def test_full_date(self):
        assert _split_birth_date("1985-03-14") == (1985, 3, 14)

    def test_year_month_precision(self):
        assert _split_birth_date("1985-03") == (1985, 3, None)

    def test_year_only_precision_does_not_fabricate_month_or_day(self):
        # The bug this guards against is defaulting to January 1st, which
        # produces a false birthday for every year-precision record.
        assert _split_birth_date("1985") == (1985, None, None)

    def test_absent(self):
        assert _split_birth_date(None) == (None, None, None)


class TestPatientMapping:
    def test_maps_a_complete_patient(self):
        mapped = map_patient(
            {"resourceType": "Patient", "id": "pat-001",
             "gender": "female", "birthDate": "1985-03-14"},
            person_id=1,
        )
        assert mapped is not None
        assert mapped.gender_concept_id == GENDER_FEMALE
        assert (mapped.year_of_birth, mapped.month_of_birth, mapped.day_of_birth) == (1985, 3, 14)
        assert mapped.person_source_value == "pat-001"

    def test_rejects_patient_without_birthdate(self):
        # year_of_birth is NOT NULL in CDM 5.4, so there is no valid row here.
        mapped = map_patient(
            {"resourceType": "Patient", "id": "pat-005", "gender": "female"},
            person_id=1,
        )
        assert mapped is None

    def test_rejects_patient_without_id(self):
        mapped = map_patient(
            {"resourceType": "Patient", "gender": "male", "birthDate": "1970-01-01"},
            person_id=1,
        )
        assert mapped is None

    def test_preserves_source_value_when_gender_does_not_map(self):
        mapped = map_patient(
            {"resourceType": "Patient", "id": "pat-003",
             "gender": "other", "birthDate": "1990-07-21"},
            person_id=1,
        )
        assert mapped.gender_concept_id == NO_MATCHING_CONCEPT
        # The information is not lost -- it moves to the source column.
        assert mapped.gender_source_value == "other"
        assert any("no standard OMOP concept" in i for i in mapped.issues)

    def test_missing_race_extension_yields_zero_not_a_guess(self):
        mapped = map_patient(
            {"resourceType": "Patient", "id": "pat-002",
             "gender": "male", "birthDate": "1972-11-02"},
            person_id=1,
        )
        assert mapped.race_concept_id == NO_MATCHING_CONCEPT
        assert mapped.ethnicity_concept_id == NO_MATCHING_CONCEPT
        assert mapped.race_source_value is None

    def test_extracts_us_core_race_text(self):
        mapped = map_patient(
            {"resourceType": "Patient", "id": "pat-001",
             "gender": "female", "birthDate": "1985-03-14",
             "extension": [{
                 "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                 "extension": [{"url": "text", "valueString": "White"}],
             }]},
            person_id=1,
        )
        assert mapped.race_source_value == "White"

    def test_row_width_matches_person_ddl(self):
        from fhir_omop import ddl
        mapped = map_patient(
            {"resourceType": "Patient", "id": "x",
             "gender": "male", "birthDate": "1980"},
            person_id=1,
        )
        ddl_columns = ddl.PERSON.count(",\n") + 1
        assert len(mapped.as_row()) == ddl_columns


class TestGenderVocabularyPresence:
    """The Gender vocabulary is a separate selectable item in an Athena bundle
    and is easy to omit. These pin the degradation behaviour when it is."""

    def _con(self, with_gender: bool):
        import duckdb
        c = duckdb.connect(":memory:")
        c.execute("CREATE TABLE concept (concept_id INTEGER, concept_name VARCHAR)")
        if with_gender:
            c.executemany("INSERT INTO concept VALUES (?,?)",
                          [(8507, "MALE"), (8532, "FEMALE")])
        return c

    def test_resolves_when_gender_vocabulary_present(self):
        m = map_patient({"resourceType": "Patient", "id": "p", "gender": "male",
                         "birthDate": "1980"}, 1, self._con(True))
        assert m.gender_concept_id == GENDER_MALE

    def test_degrades_to_zero_when_gender_vocabulary_absent(self):
        # Writing 8507 into a database whose vocabulary lacks it produces a
        # dangling reference in every person row. 0 is visible in the unmapped
        # rate; a dangling id is only visible if someone runs the check.
        m = map_patient({"resourceType": "Patient", "id": "p", "gender": "male",
                         "birthDate": "1980"}, 1, self._con(False))
        assert m.gender_concept_id == NO_MATCHING_CONCEPT
        assert any("Gender vocabulary loaded" in i for i in m.issues)

    def test_source_value_survives_the_degradation(self):
        m = map_patient({"resourceType": "Patient", "id": "p", "gender": "female",
                         "birthDate": "1980"}, 1, self._con(False))
        assert m.gender_source_value == "female"

    def test_no_connection_keeps_previous_behaviour(self):
        m = map_patient({"resourceType": "Patient", "id": "p", "gender": "male",
                         "birthDate": "1980"}, 1)
        assert m.gender_concept_id == GENDER_MALE
