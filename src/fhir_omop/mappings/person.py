"""FHIR R4 `Patient` -> OMOP CDM 5.4 `person`.

This is the simplest mapping in any FHIR-to-OMOP ETL and it still contains
three decisions that are easy to get quietly wrong. They are documented at the
point of decision rather than in a design doc nobody reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir_omop.vocab import NO_MATCHING_CONCEPT, map_gender

# US Core race/ethnicity extension URLs. Synthea emits these because Synthea
# models a US population.
US_CORE_RACE = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"
US_CORE_ETHNICITY = (
    "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity"
)


@dataclass
class MappedPerson:
    person_id: int
    gender_concept_id: int
    year_of_birth: int
    month_of_birth: int | None
    day_of_birth: int | None
    birth_datetime: str | None
    race_concept_id: int
    ethnicity_concept_id: int
    person_source_value: str
    gender_source_value: str | None
    race_source_value: str | None
    ethnicity_source_value: str | None
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        """Column order must match ddl.PERSON."""
        return (
            self.person_id,
            self.gender_concept_id,
            self.year_of_birth,
            self.month_of_birth,
            self.day_of_birth,
            self.birth_datetime,
            self.race_concept_id,
            self.ethnicity_concept_id,
            None,  # location_id    -- populated by the location mapping
            None,  # provider_id    -- populated by the provider mapping
            None,  # care_site_id   -- populated by the care_site mapping
            self.person_source_value,
            self.gender_source_value,
            None,  # gender_source_concept_id
            self.race_source_value,
            None,  # race_source_concept_id
            self.ethnicity_source_value,
            None,  # ethnicity_source_concept_id
        )


def _extension_text(patient: dict, url: str) -> str | None:
    """Pull the display text out of a US Core race/ethnicity extension."""
    for ext in patient.get("extension", []):
        if ext.get("url") != url:
            continue
        for sub in ext.get("extension", []):
            if sub.get("url") == "text":
                return sub.get("valueString")
            if sub.get("url") == "ombCategory":
                return sub.get("valueCoding", {}).get("display")
    return None


def _split_birth_date(value: str | None) -> tuple[int | None, int | None, int | None]:
    """FHIR `date` permits YYYY, YYYY-MM or YYYY-MM-DD -- all three are valid.

    An ETL that assumes a full date will crash or silently fabricate a
    January 1st on partial dates. OMOP requires year_of_birth and allows
    month and day to be null, which maps onto this precisely.
    """
    if not value:
        return None, None, None
    parts = value.split("-")
    year = int(parts[0]) if parts and parts[0].isdigit() else None
    month = int(parts[1]) if len(parts) > 1 else None
    day = int(parts[2]) if len(parts) > 2 else None
    return year, month, day


def map_patient(patient: dict, person_id: int, con=None) -> MappedPerson | None:
    """Map one FHIR Patient resource to an OMOP person row.

    Returns None when the resource cannot produce a valid CDM row -- the caller
    records it as a rejection. Dropping a row silently is never correct.
    """
    issues: list[str] = []

    source_id = patient.get("id")
    if not source_id:
        return None

    fhir_gender = patient.get("gender")
    gender_concept_id = map_gender(fhir_gender, con)
    if gender_concept_id == NO_MATCHING_CONCEPT:
        if fhir_gender is None:
            issues.append("gender element absent")
        elif fhir_gender.lower() in ("male", "female"):
            # A recognised code that still failed to resolve means the Gender
            # vocabulary is missing from the loaded vocabulary entirely.
            issues.append(
                f"gender {fhir_gender!r} recognised but its concept is absent "
                "from the vocabulary -- is the Gender vocabulary loaded?"
            )
        else:
            issues.append(f"gender {fhir_gender!r} has no standard OMOP concept")

    year, month, day = _split_birth_date(patient.get("birthDate"))
    if year is None:
        # year_of_birth is NOT NULL in CDM 5.4, so this resource cannot become
        # a person row at all. Reject it loudly instead of defaulting the year.
        return None
    if month is None:
        issues.append("birthDate has year precision only")

    # DECISION: race and ethnicity are NOT NULL in CDM 5.4, but a FHIR server
    # that is not US Core will not supply them. AU Core, for instance, has no
    # race extension at all -- Australian datasets record Indigenous status
    # under a different model entirely, which does not map onto the OMOP
    # race/ethnicity pair.
    #
    # The correct action is concept_id 0 ("No matching concept"), never a
    # guessed value. Any prevalence figure broken down by race in a dataset
    # built this way would be an artifact of the ETL, not of the population.
    race_text = _extension_text(patient, US_CORE_RACE)
    ethnicity_text = _extension_text(patient, US_CORE_ETHNICITY)
    if race_text is None:
        issues.append("no US Core race extension; race_concept_id set to 0")
    if ethnicity_text is None:
        issues.append("no US Core ethnicity extension; ethnicity_concept_id set to 0")

    # Populated only at full year-month-day precision. Leaving it null when we
    # have the date is a real loss: OHDSI tooling that needs a birth datetime
    # coalesces it to June 1st of the birth year, which produces false
    # "event before birth" findings for anyone born in the second half of a
    # year. At partial precision it stays null, because a guessed birth
    # datetime is worse than an absent one.
    birth_datetime = (
        f"{year:04d}-{month:02d}-{day:02d} 00:00:00"
        if month is not None and day is not None else None
    )
    if birth_datetime is None and month is not None:
        issues.append("birthDate lacks a day; birth_datetime left null")

    return MappedPerson(
        person_id=person_id,
        gender_concept_id=gender_concept_id,
        year_of_birth=year,
        month_of_birth=month,
        day_of_birth=day,
        birth_datetime=birth_datetime,
        # Mapping the US Core race/ethnicity text to OMOP Race/Ethnicity
        # concepts requires the full vocabulary, which the Synthea subset does
        # not carry. Left as 0 and the source text preserved, so a later pass
        # can resolve them without re-reading the source bundles.
        race_concept_id=NO_MATCHING_CONCEPT,
        ethnicity_concept_id=NO_MATCHING_CONCEPT,
        person_source_value=source_id,
        gender_source_value=fhir_gender,
        race_source_value=race_text,
        ethnicity_source_value=ethnicity_text,
        issues=issues,
    )
