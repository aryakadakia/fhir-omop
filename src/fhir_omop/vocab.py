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


# FHIR identifies code systems by URI; OMOP identifies them by vocabulary_id.
# Translating between the two is a required step in every FHIR->OMOP mapping.
#   http://hl7.org/fhir/R4/terminologies-systems.html
FHIR_SYSTEM_TO_OMOP_VOCABULARY = {
    "http://snomed.info/sct": "SNOMED",
    "http://loinc.org": "LOINC",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
    "http://hl7.org/fhir/sid/icd-10": "ICD10",
    "http://hl7.org/fhir/sid/icd-10-cm": "ICD10CM",
    "http://unitsofmeasure.org": "UCUM",
    # Australian systems, for when this runs against AU Core rather than
    # Synthea. AMT is the Australian Medicines Terminology.
    "http://snomed.info/sct/32506021000036107": "SNOMED",
}


def omop_vocabulary_for(system: str | None) -> str | None:
    """Translate a FHIR CodeSystem URI to an OMOP vocabulary_id."""
    if system is None:
        return None
    return FHIR_SYSTEM_TO_OMOP_VOCABULARY.get(system.rstrip("/"))


class ConceptResolution:
    """The result of resolving a source code, keeping both concept ids.

    OMOP deliberately stores two ids for every coded fact:
      source_concept_id   -- the concept for the code as it was received
      concept_id          -- the STANDARD concept used for analysis
    They differ whenever the source code is non-standard. Keeping both is what
    makes an OMOP dataset auditable back to the source system.
    """

    __slots__ = ("concept_id", "source_concept_id", "source_value", "note")

    def __init__(self, concept_id, source_concept_id, source_value, note=None):
        self.concept_id = concept_id
        self.source_concept_id = source_concept_id
        self.source_value = source_value
        self.note = note

    @property
    def mapped(self) -> bool:
        return self.concept_id != NO_MATCHING_CONCEPT


# Concept resolution is by far the hottest path in the ETL: a quarter of a
# million resources resolve against roughly a thousand distinct codes. Caching
# turns two SQL round-trips per row into a dict hit. The cache is keyed on
# (code, vocabulary_id) and is safe because `concept` is read-only during a
# load.
_RESOLUTION_CACHE: dict[tuple[str, str], "ConceptResolution"] = {}
_DOMAIN_CACHE: dict[int, str | None] = {}


def clear_caches() -> None:
    """Drop memoised lookups. Call when switching to a different vocabulary."""
    _RESOLUTION_CACHE.clear()
    _DOMAIN_CACHE.clear()


def resolve_to_standard(con, code: str, vocabulary_id: str, domain: str | None = None):
    """Resolve a source code to its STANDARD OMOP concept.

    Two steps, and the second is the one people forget:
      1. find the concept for (code, vocabulary_id)
      2. if it is not standard, follow the 'Maps to' relationship

    A non-standard concept used directly in condition_concept_id will simply
    never match a cohort definition, because cohort definitions are written
    against standard concepts. The rows are not rejected -- they are silently
    invisible, which is worse.
    """
    cache_key = (code, vocabulary_id)
    cached = _RESOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    row = con.execute(
        """
        SELECT concept_id, standard_concept, domain_id
        FROM concept WHERE concept_code = ? AND vocabulary_id = ?
        LIMIT 1
        """,
        [code, vocabulary_id],
    ).fetchone()

    if row is None:
        result = ConceptResolution(
            NO_MATCHING_CONCEPT, NO_MATCHING_CONCEPT, code,
            f"code {code} not present in vocabulary {vocabulary_id}",
        )
        _RESOLUTION_CACHE[cache_key] = result
        return result

    source_concept_id, standard_flag, domain_id = row

    if standard_flag == "S":
        note = None
        if domain and domain_id != domain:
            # A valid concept in the wrong domain is a mapping error: putting a
            # Procedure concept in condition_concept_id corrupts the CDM.
            note = f"concept {source_concept_id} is domain {domain_id}, expected {domain}"
        result = ConceptResolution(source_concept_id, source_concept_id, code, note)
        _RESOLUTION_CACHE[cache_key] = result
        return result

    mapped = con.execute(
        """
        SELECT cr.concept_id_2
        FROM concept_relationship cr
        JOIN concept c ON c.concept_id = cr.concept_id_2
        WHERE cr.concept_id_1 = ?
          AND cr.relationship_id = 'Maps to'
          AND c.standard_concept = 'S'
        LIMIT 1
        """,
        [source_concept_id],
    ).fetchone()

    if mapped is None:
        result = ConceptResolution(
            NO_MATCHING_CONCEPT, source_concept_id, code,
            f"non-standard concept {source_concept_id} has no 'Maps to' target",
        )
    else:
        result = ConceptResolution(
            mapped[0], source_concept_id, code,
            f"non-standard {source_concept_id} mapped to standard {mapped[0]}",
        )
    _RESOLUTION_CACHE[cache_key] = result
    return result


def concept_domain(con, concept_id: int) -> str | None:
    """Return the domain_id of a concept -- the field that decides which CDM
    table a coded fact belongs in."""
    if concept_id in _DOMAIN_CACHE:
        return _DOMAIN_CACHE[concept_id]
    row = con.execute(
        "SELECT domain_id FROM concept WHERE concept_id = ?", [concept_id]
    ).fetchone()
    domain = row[0] if row else None
    _DOMAIN_CACHE[concept_id] = domain
    return domain
