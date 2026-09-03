"""Tests for Practitioner/Organization/address -> provider/care_site/location.

These are the only non-patient-scoped resources in the ETL, which makes
deduplication the thing worth testing.
"""

import duckdb
import pytest

from fhir_omop import cdm_remainder
from fhir_omop.mappings.organisation import (
    address_key, map_location, map_organization, map_practitioner,
)
from fhir_omop.vocab import GENDER_MALE, NO_MATCHING_CONCEPT, clear_caches


@pytest.fixture(autouse=True)
def _cache():
    clear_caches(); yield; clear_caches()


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE concept (concept_id INTEGER, concept_name VARCHAR)")
    c.executemany("INSERT INTO concept VALUES (?,?)", [(8507,"MALE"),(8532,"FEMALE")])
    return c


ADDRESS = {"line": ["88 WASHINGTON STREET"], "city": "TAUNTON",
           "state": "MA", "postalCode": "02780", "country": "US"}


class TestAddressKey:
    def test_identical_addresses_share_a_key(self):
        # Locations have no id in FHIR. Without a content key, every
        # practitioner at one hospital would create a separate location row.
        assert address_key(dict(ADDRESS)) == address_key(dict(ADDRESS))

    def test_different_addresses_differ(self):
        other = dict(ADDRESS, city="BOSTON")
        assert address_key(ADDRESS) != address_key(other)

    def test_empty_address_has_no_key(self):
        assert address_key({}) is None
        assert address_key(None) is None

    def test_partial_address_still_keys(self):
        assert address_key({"city": "TAUNTON"}) is not None


class TestLocation:
    def test_fields_map(self):
        loc = map_location(ADDRESS, 1)
        assert (loc.address_1, loc.city, loc.state, loc.zip) == \
               ("88 WASHINGTON STREET", "TAUNTON", "MA", "02780")

    def test_source_value_is_human_readable(self):
        assert "TAUNTON" in map_location(ADDRESS, 1).location_source_value

    def test_row_width(self):
        assert len(map_location(ADDRESS, 1).as_row()) == \
               cdm_remainder.LOCATION.count(",\n") + 1


class TestPractitioner:
    def practitioner(self, **kw):
        base = {"resourceType": "Practitioner", "id": "prac-1",
                "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi",
                                "value": "290"}],
                "name": [{"family": "Breitenberg", "given": ["Bennett"],
                          "prefix": ["Dr."]}],
                "gender": "male"}
        base.update(kw); return base

    def test_name_is_assembled(self, con):
        m = map_practitioner(con, self.practitioner(), 1, None)
        assert m.provider_name == "Dr. Bennett Breitenberg"

    def test_npi_extracted_from_the_right_identifier_system(self, con):
        p = self.practitioner()
        p["identifier"].insert(0, {"system": "http://example.org/local", "value": "XXX"})
        assert map_practitioner(con, p, 1, None).npi == "290"

    def test_no_npi(self, con):
        p = self.practitioner(); del p["identifier"]
        assert map_practitioner(con, p, 1, None).npi is None

    def test_gender_resolves(self, con):
        assert map_practitioner(con, self.practitioner(), 1, None).gender_concept_id == GENDER_MALE

    def test_unresolvable_gender_degrades(self, con):
        m = map_practitioner(con, self.practitioner(gender="other"), 1, None)
        assert m.gender_concept_id == NO_MATCHING_CONCEPT

    def test_source_id_preserved(self, con):
        assert map_practitioner(con, self.practitioner(), 1, None).provider_source_value == "prac-1"

    def test_no_id_is_rejected(self, con):
        p = self.practitioner(); del p["id"]
        assert map_practitioner(con, p, 1, None) is None

    def test_row_width(self, con):
        m = map_practitioner(con, self.practitioner(), 1, None)
        assert len(m.as_row()) == cdm_remainder.PROVIDER.count(",\n") + 1


class TestOrganization:
    def organization(self, **kw):
        base = {"resourceType": "Organization", "id": "org-1",
                "name": "MORTON HOSPITAL",
                "type": [{"coding": [{"code": "prov"}]}]}
        base.update(kw); return base

    def test_name_and_source(self):
        m = map_organization(self.organization(), 1, 7)
        assert m.care_site_name == "MORTON HOSPITAL"
        assert m.care_site_source_value == "org-1"
        assert m.location_id == 7

    def test_place_of_service_source_preserved_but_unmapped(self):
        # HL7 organization-type has no clean crosswalk to OMOP place_of_service
        # for Synthea's single "prov" value, so it is kept as source text only.
        m = map_organization(self.organization(), 1, None)
        assert m.place_of_service_source_value == "prov"
        assert m.as_row()[2] == NO_MATCHING_CONCEPT

    def test_no_id_rejected(self):
        o = self.organization(); del o["id"]
        assert map_organization(o, 1, None) is None

    def test_row_width(self):
        m = map_organization(self.organization(), 1, None)
        assert len(m.as_row()) == cdm_remainder.CARE_SITE.count(",\n") + 1
