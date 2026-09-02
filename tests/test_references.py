"""Tests for FHIR reference resolution.

Each case is a reference form that appears in real data. The bug these guard
against is code that works against one server and silently resolves nothing
against another.
"""

import pytest

from fhir_omop.references import has_logical_reference, resolve


def ref(value):
    return {"reference": value}


class TestRelativeForm:
    def test_type_and_id(self):
        assert resolve(ref("Patient/1234")) == "1234"

    def test_any_resource_type(self):
        assert resolve(ref("Encounter/abc-def")) == "abc-def"


class TestVersionedForm:
    def test_history_suffix_is_stripped(self):
        # Patient/1234/_history/2 must resolve to 1234, not "1234/_history/2".
        assert resolve(ref("Patient/1234/_history/2")) == "1234"

    def test_versioned_absolute(self):
        assert resolve(ref("https://ex.org/base/r4/Patient/9/_history/3")) == "9"


class TestUrnForm:
    def test_uuid(self):
        assert resolve(ref("urn:uuid:5cbc121b-cd71-4428-b8b7-31e53eba8184")) \
            == "5cbc121b-cd71-4428-b8b7-31e53eba8184"

    def test_oid(self):
        assert resolve(ref("urn:oid:1.2.3.4")) == "1.2.3.4"


class TestAbsoluteForm:
    def test_https(self):
        assert resolve(ref("https://ex.org/fhir/Patient/1234")) == "1234"

    def test_http(self):
        assert resolve(ref("http://ex.org/fhir/Patient/1234")) == "1234"

    def test_deep_base_path(self):
        assert resolve(ref("https://ex.org/a/b/c/fhir/Patient/77")) == "77"


class TestUnresolvable:
    def test_contained_reference_returns_none(self):
        # "#x" points inside the referring resource, not into any index.
        # Returning "contained-1" would produce a false index lookup.
        assert resolve(ref("#contained-1")) is None

    def test_absent_node(self):
        assert resolve(None) is None

    def test_absent_reference(self):
        assert resolve({}) is None

    def test_empty_string(self):
        assert resolve(ref("")) is None

    def test_whitespace_only(self):
        assert resolve(ref("   ")) is None

    def test_non_string_reference(self):
        assert resolve({"reference": 1234}) is None


class TestLogicalReference:
    def test_identifier_only_is_flagged(self):
        # Legal FHIR: "the patient with this MRN". Unresolvable without an
        # identifier index, and must not be mistaken for an absent reference.
        node = {"identifier": {"system": "http://hosp/mrn", "value": "A1"}}
        assert has_logical_reference(node) is True
        assert resolve(node) is None

    def test_pointer_is_not_a_logical_reference(self):
        assert has_logical_reference(ref("Patient/1")) is False


class TestBareId:
    def test_bare_id_passes_through(self):
        assert resolve(ref("1234")) == "1234"
