"""Resolving FHIR references to the id of the resource they point at.

There is one implementation here because there was previously one per mapping
module, and they had already diverged on which forms they accepted -- the exact
way a codebase acquires a bug that only shows up against a different server.

FHIR permits several reference forms, all legal, and which one you see depends
entirely on who produced the data:

    Patient/1234                             relative     (live servers)
    Patient/1234/_history/2                  versioned    (any server)
    urn:uuid:5cbc121b-cd71-...               intra-bundle (Synthea, transactions)
    https://ex.org/fhir/Patient/1234         absolute     (cross-server links)
    #contained-1                             contained    (inline resources)

Handling only the two you happen to have tested with produces code that works
perfectly against one source and silently returns nothing against another.
"""

from __future__ import annotations

# Reference.reference is the pointer; Reference.identifier is a logical
# reference used when the target has no server id. We read the pointer and
# report the identifier case rather than silently returning nothing.
_URN_UUID = "urn:uuid:"
_URN_OID = "urn:oid:"


def resolve(node: dict | None) -> str | None:
    """Return the bare resource id a FHIR Reference points at, or None.

    The id returned is what you look up in the ETL's index. It is deliberately
    NOT namespaced by resource type: within one bundle, ids are unique, and the
    caller already knows which index it is searching.
    """
    if not node:
        return None

    ref = node.get("reference")
    if not ref or not isinstance(ref, str):
        return None

    ref = ref.strip()
    if not ref:
        return None

    # Contained resources live inside the referring resource, not in any index.
    # Returning the fragment would produce a false lookup, so refuse it.
    if ref.startswith("#"):
        return None

    if ref.startswith(_URN_UUID):
        return ref[len(_URN_UUID):] or None
    if ref.startswith(_URN_OID):
        return ref[len(_URN_OID):] or None

    # Strip a version suffix before anything else: Patient/1234/_history/2
    # must resolve to 1234, not to "1234/_history/2".
    if "/_history/" in ref:
        ref = ref.split("/_history/", 1)[0]

    # Absolute URL: keep only the path, then fall through to the relative case.
    if ref.startswith("http://") or ref.startswith("https://"):
        without_scheme = ref.split("://", 1)[1]
        ref = without_scheme.split("/", 1)[1] if "/" in without_scheme else ""

    # Relative form is "<ResourceType>/<id>", and the id is the final segment.
    # Taking the last segment also handles a base path of any depth.
    if "/" in ref:
        return ref.rsplit("/", 1)[1] or None

    # A bare id with no type prefix. Unusual but legal in some bundles.
    return ref or None


def has_logical_reference(node: dict | None) -> bool:
    """True when a Reference carries only an identifier and no pointer.

    This is legal FHIR -- it means "the thing with this business identifier",
    e.g. an MRN -- but it cannot be resolved without an identifier index.
    Callers report it rather than treating it as an absent reference.
    """
    if not node:
        return False
    return not node.get("reference") and bool(node.get("identifier"))
