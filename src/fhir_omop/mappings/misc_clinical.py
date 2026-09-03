"""Three small mappings that share a shape: a coded fact with a date.

    MedicationAdministration -> drug_exposure
    AllergyIntolerance       -> observation
    Device                   -> device_exposure

Each carries one reference trap, and they are all different:

    MedicationAdministration   .subject   for patient, .context for encounter
    AllergyIntolerance         .patient
    Device                     .patient

FHIR does not use one field name for "the patient this is about". Assuming it
does is how rows silently orphan.
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

# 32818 = "EHR administration record". Verified standard. This is genuinely
# different from a prescription: an administration is direct evidence the drug
# entered the patient, which is the strongest exposure evidence FHIR offers.
DRUG_TYPE_ADMINISTRATION = 32818
OBSERVATION_TYPE_EHR = 32817
DEVICE_TYPE_EHR = 32817

EXCLUDED_MED_ADMIN_STATUSES = {"entered-in-error", "not-done", "cancelled"}
EXCLUDED_ALLERGY_VERIFICATION = {"refuted", "entered-in-error"}
EXCLUDED_DEVICE_STATUSES = {"entered-in-error"}


def _first_coding(concept: dict | None):
    if not concept:
        return None, None
    codings = concept.get("coding") or []
    if not codings:
        return None, None
    return codings[0].get("system"), codings[0].get("code")


def _status_code(concept: dict | None) -> str | None:
    _, code = _first_coding(concept)
    return code


def _resolve(con, concept: dict | None, issues: list[str], expected_domain: str | None):
    """Shared resolution + domain guard."""
    system, code = _first_coding(concept)
    vocabulary_id = omop_vocabulary_for(system)
    if vocabulary_id is None:
        issues.append(f"unknown code system {system!r}")
        return NO_MATCHING_CONCEPT, NO_MATCHING_CONCEPT, code

    resolution = resolve_to_standard(con, code, vocabulary_id)
    if resolution.note:
        issues.append(resolution.note)
    concept_id = resolution.concept_id

    if resolution.mapped and expected_domain is not None:
        domain = concept_domain(con, concept_id)
        if domain != expected_domain:
            issues.append(
                f"concept {concept_id} is domain {domain!r}, not {expected_domain}; "
                "concept set to 0"
            )
            concept_id = NO_MATCHING_CONCEPT

    return concept_id, resolution.source_concept_id, code


# --------------------------------------------------------- MedicationAdministration

@dataclass
class MappedAdministration:
    drug_exposure_id: int
    person_id: int
    drug_concept_id: int
    start_date: str
    drug_source_value: str | None
    drug_source_concept_id: int
    visit_occurrence_id: int | None
    source_id: str
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        return (
            self.drug_exposure_id, self.person_id, self.drug_concept_id,
            self.start_date, None, self.start_date, None, None,
            DRUG_TYPE_ADMINISTRATION,
            None, None, None, None, None, None, None, None,
            self.visit_occurrence_id, None,
            self.drug_source_value, self.drug_source_concept_id, None, None,
        )


def administration_subject(resource: dict) -> str | None:
    return resolve_reference(resource.get("subject"))


def administration_encounter(resource: dict) -> str | None:
    """MedicationAdministration names its encounter `.context`, not `.encounter`."""
    return resolve_reference(resource.get("context") or resource.get("encounter"))


def map_medication_administration(con, resource: dict, row_id: int,
                                  person_id: int, visit_occurrence_id: int | None):
    if resource.get("status") in EXCLUDED_MED_ADMIN_STATUSES:
        return None

    effective = resource.get("effectiveDateTime")
    if not effective:
        period = resource.get("effectivePeriod") or {}
        effective = period.get("start")
    if not effective:
        return None

    issues: list[str] = []
    concept_id, source_concept_id, code = _resolve(
        con, resource.get("medicationCodeableConcept"), issues, "Drug")

    return MappedAdministration(
        drug_exposure_id=row_id, person_id=person_id, drug_concept_id=concept_id,
        start_date=effective[:10],
        drug_source_value=code, drug_source_concept_id=source_concept_id,
        visit_occurrence_id=visit_occurrence_id,
        source_id=resource.get("id", "<no id>"), issues=issues,
    )


# ------------------------------------------------------------- AllergyIntolerance

@dataclass
class MappedAllergy:
    observation_id: int
    person_id: int
    observation_concept_id: int
    date: str
    source_value: str | None
    source_concept_id: int
    value_as_string: str | None
    source_id: str
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        return (
            self.observation_id, self.person_id, self.observation_concept_id,
            self.date, None, OBSERVATION_TYPE_EHR,
            None, self.value_as_string, None, None, None,
            None, None, None,
            self.source_value, self.source_concept_id,
            None, None, self.value_as_string, None, None,
        )


def allergy_patient(resource: dict) -> str | None:
    """AllergyIntolerance names its patient `.patient`, not `.subject`."""
    return resolve_reference(resource.get("patient"))


def map_allergy(con, resource: dict, row_id: int, person_id: int):
    verification = _status_code(resource.get("verificationStatus"))
    if verification in EXCLUDED_ALLERGY_VERIFICATION:
        return None

    date = (resource.get("recordedDate") or resource.get("onsetDateTime") or "")[:10]
    if not date:
        return None

    issues: list[str] = []
    # Allergy concepts sit in the Observation domain in OMOP; there is no
    # allergy table in the CDM.
    concept_id, source_concept_id, code = _resolve(
        con, resource.get("code"), issues, "Observation")

    return MappedAllergy(
        observation_id=row_id, person_id=person_id,
        observation_concept_id=concept_id, date=date,
        source_value=code, source_concept_id=source_concept_id,
        # Criticality is clinically meaningful and has no CDM column of its own.
        value_as_string=resource.get("criticality"),
        source_id=resource.get("id", "<no id>"), issues=issues,
    )


# ------------------------------------------------------------------------ Device

@dataclass
class MappedDevice:
    device_exposure_id: int
    person_id: int
    device_concept_id: int
    start_date: str
    device_source_value: str | None
    device_source_concept_id: int
    unique_device_id: str | None
    source_id: str
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        return (
            self.device_exposure_id, self.person_id, self.device_concept_id,
            self.start_date, None, None, None,
            DEVICE_TYPE_EHR,
            self.unique_device_id, None, None, None, None, None,
            self.device_source_value, self.device_source_concept_id,
            None, None, None,
        )


def device_patient(resource: dict) -> str | None:
    return resolve_reference(resource.get("patient"))


def map_device(con, resource: dict, row_id: int, person_id: int):
    if resource.get("status") in EXCLUDED_DEVICE_STATUSES:
        return None

    # A Device has no exposure date of its own. manufactureDate is the closest
    # available and is NOT when the patient received it -- recorded as an issue
    # so nobody mistakes device_exposure_start_date for an implant date.
    date = (resource.get("manufactureDate") or "")[:10]
    if not date:
        return None

    issues = ["device_exposure_start_date is manufactureDate; "
              "the source carries no implant or issue date"]
    concept_id, source_concept_id, code = _resolve(
        con, resource.get("type"), issues, "Device")

    udi = None
    carriers = resource.get("udiCarrier") or []
    if carriers:
        udi = carriers[0].get("deviceIdentifier")

    return MappedDevice(
        device_exposure_id=row_id, person_id=person_id,
        device_concept_id=concept_id, start_date=date,
        device_source_value=code, device_source_concept_id=source_concept_id,
        unique_device_id=udi,
        source_id=resource.get("id", "<no id>"), issues=issues,
    )
