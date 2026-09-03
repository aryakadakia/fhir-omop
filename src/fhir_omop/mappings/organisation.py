"""FHIR `Practitioner` -> `provider`, `Organization` -> `care_site`,
and the addresses of both -> `location`.

These are the first resources in this ETL that are NOT patient-scoped. Every
mapping so far handled resources belonging to exactly one patient and appearing
once. Practitioners and organizations are shared reference data: the same
practitioner appears in every bundle of every patient they treated.

They must therefore be DEDUPLICATED across the whole corpus by source id.
Loading them per-bundle would produce one provider row per patient treated,
which inflates provider counts and makes any "outcome by clinician" analysis
meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fhir_omop.vocab import NO_MATCHING_CONCEPT, map_gender

# FHIR Organization.type uses the HL7 organization-type code system. OMOP's
# place_of_service concepts are a different vocabulary with no clean crosswalk
# for Synthea's single "prov" value, so this is left unmapped rather than
# guessed -- the source text is preserved for a later pass.
US_NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"


@dataclass
class MappedLocation:
    location_id: int
    address_1: str | None
    city: str | None
    state: str | None
    zip: str | None
    country_source_value: str | None
    location_source_value: str

    def as_row(self) -> tuple:
        """Column order must match cdm_remainder.LOCATION."""
        return (
            self.location_id, self.address_1, None, self.city, self.state,
            self.zip, None, self.location_source_value,
            None,  # country_concept_id
            self.country_source_value,
            None, None,  # latitude, longitude
        )


@dataclass
class MappedProvider:
    provider_id: int
    provider_name: str | None
    npi: str | None
    gender_concept_id: int
    care_site_id: int | None
    provider_source_value: str
    gender_source_value: str | None
    issues: list[str] = field(default_factory=list)

    def as_row(self) -> tuple:
        """Column order must match cdm_remainder.PROVIDER."""
        return (
            self.provider_id, self.provider_name, self.npi,
            None,  # dea
            None,  # specialty_concept_id
            self.care_site_id,
            None,  # year_of_birth
            self.gender_concept_id,
            self.provider_source_value,
            None, None,  # specialty_source_value, specialty_source_concept_id
            self.gender_source_value,
            None,  # gender_source_concept_id
        )


@dataclass
class MappedCareSite:
    care_site_id: int
    care_site_name: str | None
    location_id: int | None
    care_site_source_value: str
    place_of_service_source_value: str | None

    def as_row(self) -> tuple:
        """Column order must match cdm_remainder.CARE_SITE."""
        return (
            self.care_site_id, self.care_site_name,
            NO_MATCHING_CONCEPT,  # place_of_service_concept_id -- see note above
            self.location_id, self.care_site_source_value,
            self.place_of_service_source_value,
        )


def address_key(address: dict | None) -> tuple | None:
    """A stable identity for an address, so locations can be deduplicated.

    Locations have no source id in FHIR -- an address is only ever an inline
    structure. Without a key derived from its contents, every practitioner and
    organization at the same hospital would create a separate location row.
    """
    if not address:
        return None
    line = (address.get("line") or [None])[0]
    parts = (line, address.get("city"), address.get("state"),
             address.get("postalCode"), address.get("country"))
    return parts if any(parts) else None


def map_location(address: dict, location_id: int) -> MappedLocation:
    line = (address.get("line") or [None])[0]
    return MappedLocation(
        location_id=location_id,
        address_1=line,
        city=address.get("city"),
        state=address.get("state"),
        zip=address.get("postalCode"),
        country_source_value=address.get("country"),
        location_source_value=" ".join(
            str(p) for p in (line, address.get("city"), address.get("state"),
                             address.get("postalCode")) if p
        ) or "<no address>",
    )


def _human_name(resource: dict) -> str | None:
    names = resource.get("name") or []
    if not names or not isinstance(names[0], dict):
        return None
    n = names[0]
    given = " ".join(n.get("given") or [])
    parts = [p for p in (n.get("prefix", [None])[0] if n.get("prefix") else None,
                         given, n.get("family")) if p]
    return " ".join(parts) or None


def _npi(practitioner: dict) -> str | None:
    for ident in practitioner.get("identifier") or []:
        if ident.get("system") == US_NPI_SYSTEM:
            return ident.get("value")
    return None


def map_practitioner(con, practitioner: dict, provider_id: int,
                     care_site_id: int | None) -> MappedProvider | None:
    source_id = practitioner.get("id")
    if not source_id:
        return None

    issues: list[str] = []
    gender = practitioner.get("gender")
    gender_concept_id = map_gender(gender, con)
    if gender and gender_concept_id == NO_MATCHING_CONCEPT:
        issues.append(f"practitioner gender {gender!r} did not resolve")

    return MappedProvider(
        provider_id=provider_id,
        provider_name=_human_name(practitioner),
        npi=_npi(practitioner),
        gender_concept_id=gender_concept_id,
        care_site_id=care_site_id,
        provider_source_value=source_id,
        gender_source_value=gender,
        issues=issues,
    )


def map_organization(organization: dict, care_site_id: int,
                     location_id: int | None) -> MappedCareSite | None:
    source_id = organization.get("id")
    if not source_id:
        return None

    types = organization.get("type") or []
    place_of_service = None
    if types and (types[0].get("coding") or []):
        place_of_service = types[0]["coding"][0].get("code")

    return MappedCareSite(
        care_site_id=care_site_id,
        care_site_name=organization.get("name"),
        location_id=location_id,
        care_site_source_value=source_id,
        place_of_service_source_value=place_of_service,
    )
