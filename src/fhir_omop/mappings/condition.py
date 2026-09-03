"""FHIR R4 `Condition` -> OMOP CDM 5.4 `condition_occurrence`."""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir_omop.references import resolve as resolve_reference
from fhir_omop.vocab import (
    NO_MATCHING_CONCEPT,
    concept_domain,
    omop_vocabulary_for,
    resolve_to_standard,
)

# OMOP routes a coded fact to a table by the DOMAIN of its standard concept,
# not by the resource type that carried it. A FHIR Condition whose concept is
# Observation-domain -- "Normal pregnancy", "BMI 30+ obesity" -- belongs in
# `observation`, not `condition_occurrence`.
#
# This is the rule that most hand-rolled ETLs get wrong, and the failure is
# silent: the rows load, every foreign key resolves, and a prevalence query
# against condition_occurrence quietly returns the wrong denominator.
DOMAIN_TO_TABLE = {
    "Condition": "condition_occurrence",
    "Observation": "observation",
    "Measurement": "measurement",
    "Procedure": "procedure_occurrence",
    "Drug": "drug_exposure",
}

# 32817 = "EHR". Verified standard; 38000280 is not.
OBSERVATION_TYPE_EHR = 32817

# CDM 5.4 requires condition_type_concept_id to record HOW the row was
# obtained -- claim, EHR problem list, registry. It is provenance, not
# clinical meaning, and it is NOT NULL.
#   32817 = "EHR"
CONDITION_TYPE_EHR = 32817

# verificationStatus codes that mean "this condition is not asserted to be
# true". Loading these as diagnoses would be a data-integrity failure: a
# refuted condition is a statement that the patient does NOT have it.
#   http://hl7.org/fhir/R4/valueset-condition-ver-status.html
EXCLUDED_VERIFICATION_STATUSES = {"refuted", "entered-in-error"}


@dataclass
class MappedCondition:
    condition_occurrence_id: int
    person_id: int
    condition_concept_id: int
    condition_start_date: str
    condition_end_date: str | None
    condition_type_concept_id: int
    condition_source_value: str | None
    condition_source_concept_id: int
    condition_status_source_value: str | None
    source_id: str
    target_table: str = "condition_occurrence"
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        """Column order must match ddl.CONDITION_OCCURRENCE."""
        return (
            self.condition_occurrence_id,
            self.person_id,
            self.condition_concept_id,
            self.condition_start_date,
            None,  # condition_start_datetime
            self.condition_end_date,
            None,  # condition_end_datetime
            self.condition_type_concept_id,
            None,  # condition_status_concept_id
            None,  # stop_reason
            None,  # provider_id
            None,  # visit_occurrence_id -- set by the Encounter mapping
            None,  # visit_detail_id
            self.condition_source_value,
            self.condition_source_concept_id,
            self.condition_status_source_value,
        )


    def as_observation_row(self) -> tuple:
        """Same fact, projected onto ddl.OBSERVATION column order."""
        return (
            self.condition_occurrence_id,   # observation_id
            self.person_id,
            self.condition_concept_id,      # observation_concept_id
            self.condition_start_date,      # observation_date
            None,                           # observation_datetime
            OBSERVATION_TYPE_EHR,
            None,                           # value_as_number
            None,                           # value_as_string
            None,                           # value_as_concept_id
            None,                           # qualifier_concept_id
            None,                           # unit_concept_id
            None,                           # provider_id
            None,                           # visit_occurrence_id
            None,                           # visit_detail_id
            self.condition_source_value,
            self.condition_source_concept_id,
            None,                           # unit_source_value
            None,                           # qualifier_source_value
            None,                           # value_source_value
            None,                           # observation_event_id
            None,                           # obs_event_field_concept_id
        )


def _first_coding(concept: dict | None) -> tuple[str | None, str | None]:
    """Return (system, code) from the first coding in a CodeableConcept.

    A CodeableConcept may carry the same idea in several code systems. Taking
    the first is adequate for Synthea, which emits SNOMED only; production
    code against a real EHR should prefer a configured system order rather
    than trusting array position.
    """
    if not concept:
        return None, None
    codings = concept.get("coding") or []
    if not codings:
        return None, None
    return codings[0].get("system"), codings[0].get("code")


def _status_code(concept: dict | None) -> str | None:
    _, code = _first_coding(concept)
    return code


def _date_part(value: str | None) -> str | None:
    """FHIR dateTime -> ISO date. OMOP *_date columns are dates, not instants."""
    if not value:
        return None
    return value[:10]


def subject_id(condition: dict) -> str | None:
    """Extract the referenced Patient id from Condition.subject."""
    return resolve_reference(condition.get("subject"))


def map_condition(
    con, condition: dict, condition_occurrence_id: int, person_id: int
) -> MappedCondition | None:
    """Map one FHIR Condition to a condition_occurrence row.

    Returns None when the resource must not become a row at all -- either it
    is not asserted to be true, or it lacks a NOT NULL field.
    """
    issues: list[str] = []
    target_table = "condition_occurrence"

    verification = _status_code(condition.get("verificationStatus"))
    if verification in EXCLUDED_VERIFICATION_STATUSES:
        return None

    # condition_start_date is NOT NULL. Prefer clinical onset over recordedDate,
    # which is when the fact was typed in rather than when it began.
    start = _date_part(condition.get("onsetDateTime"))
    if start is None:
        start = _date_part(condition.get("recordedDate"))
        if start is not None:
            issues.append("no onsetDateTime; fell back to recordedDate")
    if start is None:
        return None

    system, code = _first_coding(condition.get("code"))
    vocabulary_id = omop_vocabulary_for(system)

    if vocabulary_id is None:
        resolution_concept_id = NO_MATCHING_CONCEPT
        source_concept_id = NO_MATCHING_CONCEPT
        issues.append(f"unknown code system {system!r}")
    else:
        resolution = resolve_to_standard(con, code, vocabulary_id)
        resolution_concept_id = resolution.concept_id
        source_concept_id = resolution.source_concept_id
        if resolution.note:
            issues.append(resolution.note)

        # Route by domain rather than by source resource type.
        if resolution.mapped:
            domain = concept_domain(con, resolution_concept_id)
            routed = DOMAIN_TO_TABLE.get(domain)
            if routed is None:
                issues.append(f"domain {domain!r} has no target table; row dropped")
                return None
            if routed != "condition_occurrence":
                issues.append(f"routed to {routed} (concept domain {domain})")
                target_table = routed

    return MappedCondition(
        target_table=target_table,
        condition_occurrence_id=condition_occurrence_id,
        person_id=person_id,
        condition_concept_id=resolution_concept_id,
        condition_start_date=start,
        # abatementDateTime is present exactly when the condition has resolved.
        condition_end_date=_date_part(condition.get("abatementDateTime")),
        condition_type_concept_id=CONDITION_TYPE_EHR,
        condition_source_value=code,
        condition_source_concept_id=source_concept_id,
        condition_status_source_value=_status_code(condition.get("clinicalStatus")),
        source_id=condition.get("id", "<no id>"),
        issues=issues,
    )
