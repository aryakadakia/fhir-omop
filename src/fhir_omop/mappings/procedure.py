"""FHIR R4 `Procedure` -> OMOP CDM 5.4 `procedure_occurrence`.

Domain-routed like every other coded mapping, and for a reason that matters
here more than elsewhere: SNOMED procedure codes routinely resolve into
Observation or Measurement rather than Procedure. "Colonoscopy" is a Procedure;
"Assessment using AUDIT-C" arrives as a FHIR Procedure but is an Observation in
OMOP. Trusting the resource type would put both in procedure_occurrence.
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

# 32817 = "EHR". Verified standard, like every other type concept here after
# three non-standard ones shipped and were caught by the Data Quality Dashboard.
PROCEDURE_TYPE_EHR = 32817
OBSERVATION_TYPE_EHR = 32817
MEASUREMENT_TYPE_EHR = 32817

# Statuses meaning the procedure did not happen.
EXCLUDED_STATUSES = {"entered-in-error", "not-done", "stopped"}

# Where a resolved concept goes, by domain. Anything else keeps the fact but
# refuses the concept, since this ETL has no table for it.
DOMAIN_TO_TABLE = {
    "Procedure": "procedure_occurrence",
    "Observation": "observation",
    "Measurement": "measurement",
}


@dataclass
class MappedProcedure:
    row_id: int
    person_id: int
    concept_id: int
    start_date: str
    end_date: str | None
    source_value: str | None
    source_concept_id: int
    visit_occurrence_id: int | None
    target_table: str
    source_id: str
    issues: list[str] = field(default_factory=list)

    def as_procedure_row(self) -> tuple:
        """Column order must match ddl / cdm_remainder PROCEDURE_OCCURRENCE."""
        return (
            self.row_id, self.person_id, self.concept_id, self.start_date,
            None,                  # procedure_datetime
            self.end_date,
            None,                  # procedure_end_datetime
            PROCEDURE_TYPE_EHR,
            None,                  # modifier_concept_id
            None,                  # quantity
            None,                  # provider_id
            self.visit_occurrence_id,
            None,                  # visit_detail_id
            self.source_value,
            self.source_concept_id,
            None,                  # modifier_source_value
        )

    def as_observation_row(self) -> tuple:
        return (
            self.row_id, self.person_id, self.concept_id, self.start_date,
            None, OBSERVATION_TYPE_EHR,
            None, None, None, None, None,   # values, qualifier, unit
            None,                            # provider_id
            self.visit_occurrence_id,
            None,                            # visit_detail_id
            self.source_value, self.source_concept_id,
            None, None, None, None, None,
        )

    def as_measurement_row(self) -> tuple:
        return (
            self.row_id, self.person_id, self.concept_id, self.start_date,
            None, None, MEASUREMENT_TYPE_EHR,
            None, None, None, None, None, None,   # operator..range_high
            None,                                  # provider_id
            self.visit_occurrence_id,
            None,                                  # visit_detail_id
            self.source_value, self.source_concept_id,
            None, None, None, None, None,
        )


def subject_id(procedure: dict) -> str | None:
    return resolve_reference(procedure.get("subject"))


def _first_coding(concept: dict | None):
    if not concept:
        return None, None
    codings = concept.get("coding") or []
    if not codings:
        return None, None
    return codings[0].get("system"), codings[0].get("code")


def map_procedure(con, procedure: dict, row_id: int, person_id: int,
                  visit_occurrence_id: int | None):
    issues: list[str] = []

    if procedure.get("status") in EXCLUDED_STATUSES:
        return None

    # performedPeriod is Synthea's form; performedDateTime is equally legal and
    # appears from live servers. Both are handled rather than one assumed.
    period = procedure.get("performedPeriod") or {}
    start = (period.get("start") or procedure.get("performedDateTime") or "")[:10]
    if not start:
        return None
    end = (period.get("end") or "")[:10] or None

    system, code = _first_coding(procedure.get("code"))
    vocabulary_id = omop_vocabulary_for(system)

    concept_id = NO_MATCHING_CONCEPT
    source_concept_id = NO_MATCHING_CONCEPT
    target_table = "procedure_occurrence"

    if vocabulary_id is None:
        issues.append(f"unknown procedure code system {system!r}")
    else:
        resolution = resolve_to_standard(con, code, vocabulary_id)
        concept_id = resolution.concept_id
        source_concept_id = resolution.source_concept_id
        if resolution.note:
            issues.append(resolution.note)
        if resolution.mapped:
            domain = concept_domain(con, concept_id)
            routed = DOMAIN_TO_TABLE.get(domain)
            if routed is None:
                issues.append(
                    f"concept {concept_id} is domain {domain!r}; no target table "
                    "in this ETL, concept set to 0"
                )
                concept_id = NO_MATCHING_CONCEPT
            elif routed != "procedure_occurrence":
                issues.append(f"routed to {routed} (concept domain {domain})")
                target_table = routed

    return MappedProcedure(
        row_id=row_id, person_id=person_id, concept_id=concept_id,
        start_date=start,
        # procedure_end_date is nullable, unlike visit_end_date. A procedure
        # with no recorded end is left open rather than closed on its start.
        end_date=end if target_table == "procedure_occurrence" else None,
        source_value=code, source_concept_id=source_concept_id,
        visit_occurrence_id=visit_occurrence_id,
        target_table=target_table,
        source_id=procedure.get("id", "<no id>"), issues=issues,
    )
