"""Vocabulary lookup against the OMOP `concept` table.

The whole point of OMOP is that clinical facts are stored as `concept_id`
integers resolved through a shared vocabulary. Every mapping in this ETL goes
through this module rather than hard-coding integers inline, so that the
mapping decisions are inspectable in one place.
"""

from __future__ import annotations

# OMOP reserves concept_id 0 for "No matching concept". Using 0 is the correct,
# spec-compliant way to say "this source value did not map" -- it is NOT a
# failure to be hidden. Guessing a plausible concept_id instead would silently
# corrupt every downstream analysis.
NO_MATCHING_CONCEPT = 0

# Gender is one of the few OMOP fields with a tiny fixed value set, so it is
# safe to resolve statically. These two ids are stable across all vocabulary
# releases and are verified against the loaded vocabulary by the test suite.
GENDER_MALE = 8507
GENDER_FEMALE = 8532

# FHIR R4 `Patient.gender` is bound to the required AdministrativeGender value
# set: male | female | other | unknown.
#   http://hl7.org/fhir/R4/valueset-administrative-gender.html
# OMOP has no standard concept corresponding to "other" or "unknown", so both
# map to 0 and the original code is preserved in gender_source_value.
FHIR_GENDER_TO_OMOP = {
    "male": GENDER_MALE,
    "female": GENDER_FEMALE,
    "other": NO_MATCHING_CONCEPT,
    "unknown": NO_MATCHING_CONCEPT,
}


class Vocabulary:
    """Read-only accessor over the OMOP `concept` table."""

    def __init__(self, con):
        self.con = con

    def name(self, concept_id: int) -> str | None:
        row = self.con.execute(
            "SELECT concept_name FROM concept WHERE concept_id = ?", [concept_id]
        ).fetchone()
        return row[0] if row else None

    def exists(self, concept_id: int) -> bool:
        return self.name(concept_id) is not None

    def lookup(self, code: str, vocabulary_id: str) -> int:
        """Resolve a source code to a concept_id, or 0 if it does not map."""
        row = self.con.execute(
            """
            SELECT concept_id FROM concept
            WHERE concept_code = ? AND vocabulary_id = ?
            ORDER BY CASE WHEN standard_concept = 'S' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            [code, vocabulary_id],
        ).fetchone()
        return row[0] if row else NO_MATCHING_CONCEPT


def map_gender(fhir_gender: str | None) -> int:
    """Map a FHIR Patient.gender code to an OMOP gender concept_id."""
    if fhir_gender is None:
        return NO_MATCHING_CONCEPT
    return FHIR_GENDER_TO_OMOP.get(fhir_gender.lower(), NO_MATCHING_CONCEPT)
