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
    omop_vocabulary_for,
    resolve_to_standard,
)

# 38000177 = "Prescription written" -- the honest type concept for an order.
DRUG_TYPE_PRESCRIPTION_WRITTEN = 38000177

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
