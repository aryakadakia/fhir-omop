"""FHIR R4 `Immunization` -> OMOP CDM 5.4 `drug_exposure`.

Vaccines are drugs in OMOP. CVX concepts sit in the Drug domain, so an
administered vaccine belongs in drug_exposure alongside prescriptions -- there
is no separate immunization table in the CDM.

Two things differ from the MedicationRequest mapping and both are easy to miss:

  1. Immunization uses `.patient`, not `.subject`. Condition, Observation and
     MedicationRequest all use `.subject`. Assuming `.subject` here silently
     orphans every row.

  2. An Immunization is an ADMINISTRATION, not an order. Unlike a prescription
     it is direct evidence the substance entered the patient, which makes it
     stronger exposure evidence than anything in MedicationRequest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir_omop.references import resolve as resolve_reference
from fhir_omop.vocab import (
    NO_MATCHING_CONCEPT,
    concept_domain,
    omop_vocabulary_for,
    resolve_to_standard,
)

# 32817 = "EHR". Verified present in the vocabulary rather than assumed -- the
# more specific administration type concepts are not in every vocabulary
# subset, and a dangling type concept fails the conformance checks.
DRUG_TYPE_EHR = 32817

# Statuses that mean the vaccine was not given.
EXCLUDED_STATUSES = {"entered-in-error", "not-done"}


@dataclass
class MappedImmunization:
    drug_exposure_id: int
    person_id: int
    drug_concept_id: int
    start_date: str
    end_date: str
    drug_source_value: str | None
    drug_source_concept_id: int
    visit_occurrence_id: int | None
    source_id: str
    lot_number: str | None = None
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        """Column order must match ddl.DRUG_EXPOSURE."""
        return (
            self.drug_exposure_id, self.person_id, self.drug_concept_id,
            self.start_date,
            None,                 # drug_exposure_start_datetime
            self.end_date,
            None,                 # drug_exposure_end_datetime
            None,                 # verbatim_end_date
            DRUG_TYPE_EHR,
            None,                 # stop_reason
            None,                 # refills
            None,                 # quantity
            None,                 # days_supply
            None,                 # sig
            None,                 # route_concept_id
            self.lot_number,
            None,                 # provider_id
            self.visit_occurrence_id,
            None,                 # visit_detail_id
            self.drug_source_value,
            self.drug_source_concept_id,
            None,                 # route_source_value
            None,                 # dose_unit_source_value
        )


def patient_id(immunization: dict) -> str | None:
    """Immunization references its patient as `.patient`, NOT `.subject`."""
    return resolve_reference(immunization.get("patient"))


def _first_coding(concept: dict | None):
    if not concept:
        return None, None
    codings = concept.get("coding") or []
    if not codings:
        return None, None
    return codings[0].get("system"), codings[0].get("code")


def map_immunization(con, immunization: dict, drug_exposure_id: int,
                     person_id: int, visit_occurrence_id: int | None):
    issues: list[str] = []

    if immunization.get("status") in EXCLUDED_STATUSES:
        return None

    # occurrenceDateTime is the usual form; occurrenceString is legal but
    # unparseable as a date, so it is refused rather than guessed at.
    occurrence = immunization.get("occurrenceDateTime")
    if not occurrence:
        if immunization.get("occurrenceString"):
            issues.append("occurrenceString is not a parseable date; row refused")
        return None
    start = occurrence[:10]

    system, code = _first_coding(immunization.get("vaccineCode"))
    vocabulary_id = omop_vocabulary_for(system)

    concept_id = NO_MATCHING_CONCEPT
    source_concept_id = NO_MATCHING_CONCEPT
    if vocabulary_id is None:
        issues.append(f"unknown vaccine code system {system!r}")
    else:
        resolution = resolve_to_standard(con, code, vocabulary_id)
        concept_id = resolution.concept_id
        source_concept_id = resolution.source_concept_id
        if resolution.note:
            issues.append(resolution.note)

        # Same domain guard as every other coded mapping. CVX should resolve
        # into Drug, but that is checked rather than assumed.
        if resolution.mapped:
            domain = concept_domain(con, concept_id)
            if domain != "Drug":
                issues.append(
                    f"concept {concept_id} is domain {domain!r}, not Drug; "
                    "drug_concept_id set to 0"
                )
                concept_id = NO_MATCHING_CONCEPT

    return MappedImmunization(
        drug_exposure_id=drug_exposure_id, person_id=person_id,
        drug_concept_id=concept_id,
        # A vaccination is a point event: it starts and ends the same day.
        # Unlike the MedicationRequest case this is genuinely true, not a
        # missing-duration fallback.
        start_date=start, end_date=start,
        drug_source_value=code, drug_source_concept_id=source_concept_id,
        visit_occurrence_id=visit_occurrence_id,
        source_id=immunization.get("id", "<no id>"),
        lot_number=immunization.get("lotNumber"),
        issues=issues,
    )
