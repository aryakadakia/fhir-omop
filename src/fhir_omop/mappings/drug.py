"""FHIR R4 `MedicationRequest` -> OMOP CDM 5.4 `drug_exposure`.

A caveat that matters clinically: MedicationRequest is an ORDER, not an
administration. It records that a prescriber asked for a drug, not that the
patient received or took it. OMOP records this distinction in
drug_type_concept_id, and any adherence analysis built on prescriptions alone
is measuring prescribing, not exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir_omop.vocab import (
    NO_MATCHING_CONCEPT,
    concept_domain,
    omop_vocabulary_for,
    resolve_to_standard,
)

# 32838 = "EHR prescription". Verified standard in the vocabulary before use.
#
# This previously used 38000177 "Prescription written", which reads better but
# is NON-STANDARD -- caught by the Data Quality Dashboard, not by our own
# checks, which only verified event concepts and not type concepts. A
# non-standard type concept is invisible to any analysis that filters on it,
# in exactly the way a non-standard condition concept is.
DRUG_TYPE_PRESCRIPTION_WRITTEN = 32838

# Statuses that did not result in a prescription being issued.
EXCLUDED_STATUSES = {"entered-in-error", "draft", "cancelled"}


@dataclass
class MappedDrug:
    drug_exposure_id: int
    person_id: int
    drug_concept_id: int
    start_date: str
    end_date: str
    drug_source_value: str | None
    drug_source_concept_id: int
    stop_reason: str | None
    visit_occurrence_id: int | None
    source_id: str
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        return (
            self.drug_exposure_id, self.person_id, self.drug_concept_id,
            self.start_date,
            None,                 # drug_exposure_start_datetime
            self.end_date,
            None,                 # drug_exposure_end_datetime
            None,                 # verbatim_end_date
            DRUG_TYPE_PRESCRIPTION_WRITTEN,
            self.stop_reason,
            None,                 # refills
            None,                 # quantity
            None,                 # days_supply
            None,                 # sig
            None,                 # route_concept_id
            None,                 # lot_number
            None,                 # provider_id
            self.visit_occurrence_id,
            None,                 # visit_detail_id
            self.drug_source_value,
            self.drug_source_concept_id,
            None,                 # route_source_value
            None,                 # dose_unit_source_value
        )


def _first_coding(concept: dict | None):
    if not concept:
        return None, None
    codings = concept.get("coding") or []
    if not codings:
        return None, None
    return codings[0].get("system"), codings[0].get("code")


def map_medication_request(con, request: dict, drug_exposure_id: int,
                           person_id: int, visit_occurrence_id: int | None):
    issues: list[str] = []

    status = request.get("status")
    if status in EXCLUDED_STATUSES:
        return None

    start = (request.get("authoredOn") or "")[:10]
    if not start:
        return None

    system, code = _first_coding(request.get("medicationCodeableConcept"))
    vocabulary_id = omop_vocabulary_for(system)

    concept_id = NO_MATCHING_CONCEPT
    source_concept_id = NO_MATCHING_CONCEPT
    if vocabulary_id is None:
        issues.append(f"unknown medication code system {system!r}")
    else:
        resolution = resolve_to_standard(con, code, vocabulary_id)
        concept_id = resolution.concept_id
        source_concept_id = resolution.source_concept_id
        if resolution.note:
            issues.append(resolution.note)

        # Domain check, for the same reason Condition and Observation have one:
        # RxNorm does not resolve exclusively into the Drug domain. A concept
        # from another domain in drug_concept_id would corrupt every drug
        # analysis, so it is refused rather than written. The source concept is
        # preserved so nothing is lost and the case can be reviewed.
        if resolution.mapped:
            domain = concept_domain(con, concept_id)
            if domain != "Drug":
                issues.append(
                    f"concept {concept_id} is domain {domain!r}, not Drug; "
                    "drug_concept_id set to 0"
                )
                concept_id = NO_MATCHING_CONCEPT

    # drug_exposure_end_date is NOT NULL. Synthea supplies no dispense duration
    # here, so the order date is used for both ends. This UNDERSTATES exposure
    # and must not be used to compute days-supplied without a real duration.
    issues.append("no dispense duration in source; end_date set to start_date")

    return MappedDrug(
        drug_exposure_id=drug_exposure_id, person_id=person_id,
        drug_concept_id=concept_id, start_date=start, end_date=start,
        drug_source_value=code, drug_source_concept_id=source_concept_id,
        stop_reason="stopped" if status == "stopped" else None,
        visit_occurrence_id=visit_occurrence_id,
        source_id=request.get("id", "<no id>"), issues=issues,
    )
