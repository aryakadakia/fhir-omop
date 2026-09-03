"""FHIR R4 `Encounter` -> OMOP CDM 5.4 `visit_occurrence`."""

from __future__ import annotations

from dataclasses import dataclass, field

# OMOP visit concepts. These are the standard concepts every OHDSI analysis
# expects, so they are resolved statically rather than looked up.
VISIT_INPATIENT = 9201
VISIT_OUTPATIENT = 9202
VISIT_EMERGENCY = 9203
VISIT_TYPE_EHR = 32817

# FHIR Encounter.class uses HL7 v3 ActCode.
#   http://terminology.hl7.org/CodeSystem/v3-ActCode
# The mapping is lossy in both directions -- OMOP has three broad visit types
# where ActCode has a dozen -- so anything unrecognised becomes 0 rather than
# being forced into the nearest bucket.
ACTCODE_TO_VISIT_CONCEPT = {
    "IMP": VISIT_INPATIENT,     # inpatient encounter
    "ACUTE": VISIT_INPATIENT,
    "NONAC": VISIT_INPATIENT,
    "EMER": VISIT_EMERGENCY,    # emergency
    "AMB": VISIT_OUTPATIENT,    # ambulatory
    "OBSENC": VISIT_OUTPATIENT,
    "VR": VISIT_OUTPATIENT,     # virtual
    "HH": VISIT_OUTPATIENT,     # home health
}

# Encounter.status values that must not become a visit. A cancelled or
# entered-in-error encounter did not happen.
EXCLUDED_STATUSES = {"cancelled", "entered-in-error", "planned"}


@dataclass
class MappedVisit:
    visit_occurrence_id: int
    person_id: int
    visit_concept_id: int
    visit_start_date: str
    visit_end_date: str
    visit_type_concept_id: int
    visit_source_value: str | None
    source_id: str
    # Assigned by the ETL once the provider and care_site indexes exist; a
    # visit cannot resolve them itself because both are corpus-wide reference
    # entities rather than anything inside the Encounter.
    provider_id: int | None = None
    care_site_id: int | None = None
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        return (
            self.visit_occurrence_id,
            self.person_id,
            self.visit_concept_id,
            self.visit_start_date,
            None,  # visit_start_datetime
            self.visit_end_date,
            None,  # visit_end_datetime
            self.visit_type_concept_id,
            self.provider_id,
            self.care_site_id,
            self.visit_source_value,
            None,  # visit_source_concept_id
            None,  # admitted_from_concept_id
            None,  # admitted_from_source_value
            None,  # discharged_to_concept_id
            None,  # discharged_to_source_value
            None,  # preceding_visit_occurrence_id
        )


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def map_encounter(encounter: dict, visit_occurrence_id: int, person_id: int):
    issues: list[str] = []

    if encounter.get("status") in EXCLUDED_STATUSES:
        return None

    period = encounter.get("period") or {}
    start = _date(period.get("start"))
    if start is None:
        return None

    # visit_end_date is NOT NULL in CDM 5.4. An encounter with no end is
    # ongoing; OMOP convention is to close it on the start date rather than
    # leave it open or invent a duration.
    end = _date(period.get("end"))
    if end is None:
        end = start
        issues.append("no period.end; visit closed on start date")

    class_code = (encounter.get("class") or {}).get("code")
    visit_concept_id = ACTCODE_TO_VISIT_CONCEPT.get(class_code, 0)
    if visit_concept_id == 0:
        issues.append(f"Encounter.class {class_code!r} has no OMOP visit concept")

    return MappedVisit(
        visit_occurrence_id=visit_occurrence_id,
        person_id=person_id,
        visit_concept_id=visit_concept_id,
        visit_start_date=start,
        visit_end_date=end,
        visit_type_concept_id=VISIT_TYPE_EHR,
        visit_source_value=class_code,
        source_id=encounter.get("id", "<no id>"),
        issues=issues,
    )
