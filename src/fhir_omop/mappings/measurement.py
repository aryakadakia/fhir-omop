"""FHIR R4 `Observation` -> OMOP `measurement` or `observation`.

FHIR puts everything with a result in one resource type: lab values, vital
signs, smoking status, survey answers. OMOP splits them:

  measurement  -- quantitative results with a number and a unit
  observation  -- everything else (qualitative facts, survey items, statuses)

The split is decided by the DOMAIN of the resolved LOINC concept, exactly as
with Condition. This is the same rule twice, which is the point: in OMOP the
source resource type never determines the destination table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir_omop.vocab import (
    NO_MATCHING_CONCEPT,
    concept_domain,
    omop_vocabulary_for,
    resolve_to_standard,
)

MEASUREMENT_TYPE_EHR = 32817
OBSERVATION_TYPE_EHR = 38000280

# Observation.status values that must not be loaded.
EXCLUDED_STATUSES = {"entered-in-error", "cancelled"}


@dataclass
class MappedObservation:
    row_id: int
    person_id: int
    concept_id: int
    date: str
    value_as_number: float | None
    value_as_string: str | None
    unit_concept_id: int
    unit_source_value: str | None
    source_value: str | None
    source_concept_id: int
    visit_occurrence_id: int | None
    target_table: str
    source_id: str
    issues: list[str] = field(default_factory=list)

    def as_measurement_row(self) -> tuple:
        return (
            self.row_id, self.person_id, self.concept_id, self.date,
            None,                       # measurement_datetime
            None,                       # measurement_time
            MEASUREMENT_TYPE_EHR,
            None,                       # operator_concept_id
            self.value_as_number,
            None,                       # value_as_concept_id
            self.unit_concept_id,
            None, None,                 # range_low, range_high
            None,                       # provider_id
            self.visit_occurrence_id,
            None,                       # visit_detail_id
            self.source_value,
            self.source_concept_id,
            self.unit_source_value,
            None,                       # unit_source_concept_id
            self.value_as_string,
            None, None,                 # measurement_event_id, meas_event_field_concept_id
        )

    def as_observation_row(self) -> tuple:
        return (
            self.row_id, self.person_id, self.concept_id, self.date,
            None,                       # observation_datetime
            OBSERVATION_TYPE_EHR,
            self.value_as_number,
            self.value_as_string,
            None,                       # value_as_concept_id
            None,                       # qualifier_concept_id
            self.unit_concept_id,
            None,                       # provider_id
            self.visit_occurrence_id,
            None,                       # visit_detail_id
            self.source_value,
            self.source_concept_id,
            self.unit_source_value,
            None,                       # qualifier_source_value
            self.value_as_string,
            None, None,                 # observation_event_id, obs_event_field_concept_id
        )


def _first_coding(concept: dict | None):
    if not concept:
        return None, None
    codings = concept.get("coding") or []
    if not codings:
        return None, None
    return codings[0].get("system"), codings[0].get("code")


def reference_id(node: dict | None) -> str | None:
    ref = (node or {}).get("reference")
    if not ref:
        return None
    for prefix in ("urn:uuid:", "Patient/", "Encounter/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def map_observation(con, obs: dict, row_id: int, person_id: int,
                    visit_occurrence_id: int | None):
    issues: list[str] = []

    if obs.get("status") in EXCLUDED_STATUSES:
        return None

    date = (obs.get("effectiveDateTime") or obs.get("issued") or "")[:10]
    if not date:
        return None

    system, code = _first_coding(obs.get("code"))
    vocabulary_id = omop_vocabulary_for(system)

    concept_id = NO_MATCHING_CONCEPT
    source_concept_id = NO_MATCHING_CONCEPT
    target_table = "observation"

    if vocabulary_id is None:
        issues.append(f"unknown code system {system!r}")
    else:
        resolution = resolve_to_standard(con, code, vocabulary_id)
        concept_id = resolution.concept_id
        source_concept_id = resolution.source_concept_id
        if resolution.note:
            issues.append(resolution.note)
        if resolution.mapped:
            domain = concept_domain(con, concept_id)
            target_table = "measurement" if domain == "Measurement" else "observation"
            if domain not in ("Measurement", "Observation"):
                issues.append(f"LOINC concept in domain {domain!r}; sent to observation")

    # A quantitative result carries valueQuantity; a qualitative one carries
    # valueCodeableConcept or valueString. Both are legal Observations.
    quantity = obs.get("valueQuantity") or {}
    value_number = quantity.get("value")
    unit_source_value = quantity.get("unit")

    unit_concept_id = NO_MATCHING_CONCEPT
    if quantity.get("code"):
        unit_resolution = resolve_to_standard(con, quantity["code"], "UCUM")
        unit_concept_id = unit_resolution.concept_id

    value_string = None
    if value_number is None:
        _, value_code = _first_coding(obs.get("valueCodeableConcept"))
        value_string = value_code or obs.get("valueString")

    return MappedObservation(
        row_id=row_id, person_id=person_id, concept_id=concept_id, date=date,
        value_as_number=value_number, value_as_string=value_string,
        unit_concept_id=unit_concept_id, unit_source_value=unit_source_value,
        source_value=code, source_concept_id=source_concept_id,
        visit_occurrence_id=visit_occurrence_id,
        target_table=target_table,
        source_id=obs.get("id", "<no id>"), issues=issues,
    )
