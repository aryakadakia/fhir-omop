#!/usr/bin/env python3
"""Fetch complete patient records from a live FHIR server.

    python scripts/fetch_fhir.py --server https://launch.smarthealthit.org/v/r4/fhir --patients 5

Reading FHIR from disk and reading it from a server are different problems, and
the ETL only solves the first. Two things break when you point a file reader at
an API, and both were found by doing exactly that:

  PAGINATION. A server returns results in pages with a `next` link. Reading the
  first page and stopping loaded roughly half of one patient's record, with zero
  rejections and every data-quality check passing. A quality suite verifies that
  the rows present are well formed; nothing in it can notice rows that are
  absent.

  UNRESOLVED REFERENCES. `Patient/$everything` returns a patient's clinical data
  but not the Practitioners and Organizations that data points at. Loading it
  produced zero providers and zero care sites, and a null provider_id on every
  visit, despite those mappings being correct.

So this fetcher paginates to exhaustion, then makes a second pass to retrieve
the resources the first pass only referenced.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

# Resource types worth chasing when something references them. Deliberately
# narrow: following every reference in a record walks most of the server.
REFERENCE_TYPES_TO_RESOLVE = {"Practitioner", "Organization", "Location", "Medication"}

# The resource types the ETL maps. Fetched with an ordinary type search rather
# than Patient/$everything, for two reasons.
#
# A type search returns `total`, so completeness can be VERIFIED rather than
# assumed. That matters more here than anywhere: the failure this script exists
# to prevent is a partial pull that looks complete.
#
# And $everything's paging is not reliably implemented. The SMART sandbox
# returns 503 on its own `next` links while ordinary search paging works fine on
# the same server.
PATIENT_RESOURCE_TYPES = [
    "Encounter", "Condition", "Observation", "Procedure",
    "MedicationRequest", "MedicationAdministration",
    "Immunization", "AllergyIntolerance", "Device",
]

USER_AGENT = "fhir-omop-fetcher/1.0 (+https://github.com/aryakadakia/fhir-omop)"

# A public sandbox is someone else's machine. Pause between requests.
POLITE_DELAY_SECONDS = 0.2

# Servers fail transiently, and a paging endpoint is a particularly good place
# for it: the sandbox this was written against returns 503 on its own `next`
# links often enough to matter.
MAX_RETRIES = 4
BACKOFF_SECONDS = 2


class FetchError(Exception):
    """A request failed after retries. Raised rather than exiting, so a caller
    can keep what it has already collected."""


def get(url: str, timeout: int = 60) -> dict:
    last = ""
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "Accept": "application/fhir+json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
            time.sleep(POLITE_DELAY_SECONDS)
            break
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            # 4xx is our fault and will not improve by asking again.
            if e.code < 500:
                raise FetchError(f"{last} for {url}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = type(e).__name__
        if attempt < MAX_RETRIES:
            wait = BACKOFF_SECONDS * attempt
            print(f"      {last}, retrying in {wait}s ({attempt}/{MAX_RETRIES - 1})",
                  flush=True)
            time.sleep(wait)
    else:
        raise FetchError(f"{last} for {url} after {MAX_RETRIES} attempts")

    # A FHIR server reports errors as an OperationOutcome with a 200 in some
    # configurations, so the status code alone is not enough.
    if body.get("resourceType") == "OperationOutcome":
        issues = "; ".join(i.get("diagnostics", i.get("code", "?"))
                           for i in body.get("issue", []))
        raise SystemExit(f"server returned an OperationOutcome for {url}\n  {issues}")
    return body


def next_link(bundle: dict) -> str | None:
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            return link.get("url")
    return None


def fetch_all_pages(url: str, label: str, max_pages: int = 200) -> list[dict]:
    """Follow `next` links to exhaustion. This is the whole point of the script."""
    entries: list[dict] = []
    pages = 0
    while url and pages < max_pages:
        try:
            bundle = get(url)
        except FetchError as e:
            # Keep what we have and say so. Losing 200 already-fetched
            # resources because page 2 was unavailable would be worse, but a
            # partial pull that looks complete is the failure this whole script
            # exists to prevent, so it is reported loudly.
            print(f"    ! {e}")
            print(f"    ! {label} is INCOMPLETE: {len(entries)} resources from "
                  f"{pages} page(s) before the failure")
            return entries
        page_entries = bundle.get("entry", []) or []
        entries.extend(page_entries)
        pages += 1
        url = next_link(bundle)
        print(f"    page {pages}: {len(page_entries):>4} entries"
              f"{'  (more)' if url else '  (last)'}", flush=True)
    if url:
        print(f"    ! stopped at max_pages={max_pages}; {label} may be incomplete")
    return entries


def collect_references(entries: list[dict]) -> set[str]:
    """Every `Type/id` reference in the fetched resources, for the second pass."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("reference")
            if isinstance(ref, str) and "/" in ref and not ref.startswith(("urn:", "#")):
                # Strip any version suffix and absolute prefix.
                bare = ref.split("/_history/")[0]
                if bare.startswith("http"):
                    bare = "/".join(bare.split("/")[-2:])
                rtype = bare.split("/")[0]
                if rtype in REFERENCE_TYPES_TO_RESOLVE:
                    found.add(bare)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for e in entries:
        walk(e.get("resource", {}))
    return found


def fetch_patient(server: str, patient_id: str) -> tuple[dict, list[str]]:
    """Returns the bundle and a list of completeness warnings."""
    print(f"  {patient_id}")
    entries: list[dict] = []
    warnings: list[str] = []

    patient = get(f"{server}/Patient/{patient_id}")
    entries.append({"fullUrl": f"{server}/Patient/{patient_id}", "resource": patient})

    for rtype in PATIENT_RESOURCE_TYPES:
        url = f"{server}/{rtype}?patient={patient_id}&_count=200"
        try:
            first = get(url)
        except FetchError as e:
            warnings.append(f"{rtype}: {e}")
            print(f"    {rtype:<26} ! {e}")
            continue

        expected = first.get("total")
        got = list(first.get("entry", []) or [])
        nxt = next_link(first)
        pages = 1
        while nxt:
            try:
                page = get(nxt)
            except FetchError as e:
                warnings.append(f"{rtype}: incomplete, {e}")
                print(f"    {rtype:<26} ! stopped after {pages} page(s): {e}")
                break
            got.extend(page.get("entry", []) or [])
            nxt = next_link(page)
            pages += 1

        entries.extend(got)

        # The server told us how many exist. Check we have them.
        if expected is not None and len(got) != expected:
            warnings.append(f"{rtype}: got {len(got)} of {expected}")
            print(f"    {rtype:<26} {len(got):>4} of {expected}  ! INCOMPLETE")
        elif got:
            print(f"    {rtype:<26} {len(got):>4}"
                  + (f"  ({pages} pages)" if pages > 1 else ""))

    # Second pass: retrieve what the first pass only pointed at.
    present = {f"{e['resource']['resourceType']}/{e['resource']['id']}"
               for e in entries if e.get("resource", {}).get("id")}
    wanted = collect_references(entries) - present
    if wanted:
        print(f"    resolving {len(wanted)} referenced resources ...")
        for ref in sorted(wanted):
            try:
                resource = get(f"{server}/{ref}")
            except FetchError as e:
                print(f"      skip {ref}: {e}")
                continue
            entries.append({"fullUrl": f"{server}/{ref}", "resource": resource})

    return {"resourceType": "Bundle", "type": "collection", "entry": entries}, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="FHIR R4 base URL")
    ap.add_argument("--out", type=Path, default=Path("data/fhir/live"))
    ap.add_argument("--patients", type=int, default=5,
                    help="how many patients to fetch (ignored with --patient-id)")
    ap.add_argument("--patient-id", action="append",
                    help="fetch specific patient ids; repeatable")
    args = ap.parse_args()

    server = args.server.rstrip("/")
    args.out.mkdir(parents=True, exist_ok=True)

    if args.patient_id:
        ids = args.patient_id
    else:
        print(f"listing patients from {server} ...")
        try:
            bundle = get(f"{server}/Patient?_count={args.patients}")
        except FetchError as e:
            raise SystemExit(str(e))
        ids = [e["resource"]["id"] for e in bundle.get("entry", [])]
        print(f"  found {len(ids)}")

    print("\nfetching:")
    census: Counter = Counter()
    all_warnings: list[str] = []
    for pid in ids:
        bundle, warnings = fetch_patient(server, pid)
        all_warnings.extend(f"{pid}: {w}" for w in warnings)
        for e in bundle["entry"]:
            census[e.get("resource", {}).get("resourceType", "?")] += 1
        path = args.out / f"{pid}.json"
        path.write_text(json.dumps(bundle, indent=1))
        print(f"    wrote {path}  ({len(bundle['entry'])} resources)")

    print("\ncensus across all patients:")
    for rtype, n in census.most_common():
        print(f"  {rtype:<26} {n:>6}")
    print(f"\n{sum(census.values())} resources in {len(ids)} bundles -> {args.out}")

    if all_warnings:
        print(f"\n! {len(all_warnings)} COMPLETENESS WARNING(S) -- this pull is partial:")
        for w in all_warnings:
            print(f"    {w}")
        print("\n  A partial pull that looks complete is the failure this script")
        print("  exists to prevent. Do not load it and report a prevalence.")
    else:
        print("\ncomplete: every resource count matched the server's reported total")
    print(f"\nnext:\n  python scripts/run_etl.py --fhir {args.out} --vocab data/omop/vocab.duckdb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
