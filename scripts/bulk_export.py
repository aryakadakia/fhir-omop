#!/usr/bin/env python3
"""Bulk FHIR export: fetch a whole population, not one patient at a time.

    python scripts/bulk_export.py --server https://bulk-data.smarthealthit.org/fhir

`scripts/fetch_fhir.py` retrieves one patient per request, which is what an
application does. Building a CDM needs the opposite: everybody, once. That is
a different protocol.

Bulk Data (FHIR R4 "Asynchronous Request Pattern") works in four steps:

  1. KICKOFF   GET [base]/Patient/$export with `Prefer: respond-async`.
               The server replies 202 and a `Content-Location` status URL.
               Nothing is returned yet -- the export runs in the background.

  2. POLL      GET the status URL. 202 means still working, and the server may
               send `X-Progress` and `Retry-After`. 200 means finished, and the
               body is a manifest listing one file per resource type.

  3. DOWNLOAD  Each manifest entry is newline-delimited JSON (.ndjson): one
               complete FHIR resource per line, no enclosing Bundle. Large
               exports are split across several files per type.

  4. CLEAN UP  DELETE the status URL, which tells the server it can discard the
               files. Optional, and worth doing on someone else's machine.

The manifest carries a `count` per file, so completeness is verifiable here in
the same way the per-patient fetcher verifies it: compare what arrived against
what the server said it was sending.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "fhir-omop-bulk/1.0 (+https://github.com/aryakadakia/fhir-omop)"

# The resource types the ETL maps. Asking for only these keeps the export small
# and quick; omitting _type asks the server for everything it holds.
DEFAULT_TYPES = [
    "Patient", "Encounter", "Condition", "Observation", "Procedure",
    "MedicationRequest", "MedicationAdministration", "Immunization",
    "AllergyIntolerance", "Device", "Practitioner", "Organization",
]

POLL_INTERVAL_DEFAULT = 3
POLL_TIMEOUT_SECONDS = 900


def request(url: str, method: str = "GET", headers: dict | None = None, timeout: int = 120):
    req = urllib.request.Request(url, method=method, headers={
        "Accept": "application/fhir+json",
        "User-Agent": USER_AGENT,
        **(headers or {}),
    })
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def supported_types(server: str) -> set[str] | None:
    """Ask the server which resource types it actually has.

    Bulk export is all-or-nothing on type validity: naming one type the server
    does not hold rejects the whole request, and the error names only the first
    offender, so you discover them one round-trip at a time. Reading the
    CapabilityStatement first turns that into a single question.
    """
    status, _, body = request(f"{server}/metadata")
    if status != 200:
        print(f"  could not read CapabilityStatement (HTTP {status}); "
              "requesting all types and letting the server object")
        return None
    try:
        cs = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    found = {
        r.get("type")
        for rest in cs.get("rest", [])
        for r in rest.get("resource", [])
        if r.get("type")
    }
    return found or None


def kickoff(server: str, types: list[str], since: str | None) -> str:
    url = f"{server}/Patient/$export?_type={','.join(types)}"
    if since:
        url += f"&_since={since}"
    print(f"kickoff: {url}")
    status, headers, body = request(url, headers={"Prefer": "respond-async"})

    if status != 202:
        detail = body.decode("utf-8", "replace")[:400]
        raise SystemExit(
            f"kickoff returned {status}, expected 202.\n{detail}\n\n"
            "Not every FHIR server supports Bulk Data. It is a separate\n"
            "capability from the REST API, and many sandboxes lack it."
        )

    location = headers.get("Content-Location") or headers.get("content-location")
    if not location:
        raise SystemExit("server returned 202 but no Content-Location status URL")
    print(f"  accepted, status URL: {location}")
    return location


def poll(status_url: str) -> dict:
    waited = 0
    while waited < POLL_TIMEOUT_SECONDS:
        status, headers, body = request(status_url)

        if status == 200:
            print(f"  complete after {waited}s")
            return json.loads(body.decode("utf-8"))

        if status != 202:
            raise SystemExit(f"status endpoint returned {status}: "
                             f"{body.decode('utf-8', 'replace')[:300]}")

        progress = headers.get("X-Progress") or headers.get("x-progress") or "working"
        # Servers advise a polling interval; honouring it is basic courtesy and
        # some will rate-limit if you do not.
        retry = headers.get("Retry-After") or headers.get("retry-after")
        interval = int(retry) if (retry or "").isdigit() else POLL_INTERVAL_DEFAULT
        print(f"  [{waited:>4}s] {progress}")
        time.sleep(interval)
        waited += interval

    raise SystemExit(f"export did not finish within {POLL_TIMEOUT_SECONDS}s")


def download(manifest: dict, out_dir: Path) -> tuple[int, list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    total = 0

    # The server reports errors as ndjson files of OperationOutcome, separate
    # from the data. Silence here does not mean success.
    for err in manifest.get("error", []) or []:
        warnings.append(f"server reported an error file: {err.get('url')}")

    outputs = manifest.get("output", []) or []
    if not outputs:
        warnings.append("manifest contained no output files")

    print(f"\ndownloading {len(outputs)} file(s):")
    for entry in outputs:
        rtype = entry.get("type", "Unknown")
        expected = entry.get("count")
        status, _, body = request(entry["url"], timeout=300)
        if status != 200:
            warnings.append(f"{rtype}: download returned {status}")
            print(f"  {rtype:<26} ! HTTP {status}")
            continue

        text = body.decode("utf-8")
        lines = [l for l in text.splitlines() if l.strip()]
        # One file per type may be split; append rather than overwrite.
        path = out_dir / f"{rtype}.ndjson"
        with path.open("a") as f:
            f.write(text if text.endswith("\n") else text + "\n")

        total += len(lines)
        if expected is not None and len(lines) != expected:
            warnings.append(f"{rtype}: got {len(lines)} lines, manifest said {expected}")
            print(f"  {rtype:<26} {len(lines):>6} of {expected}  ! MISMATCH")
        else:
            print(f"  {rtype:<26} {len(lines):>6}")

    return total, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="FHIR R4 base URL supporting Bulk Data")
    ap.add_argument("--out", type=Path, default=Path("data/fhir/bulk_export"))
    ap.add_argument("--type", action="append", help="resource type; repeatable. Default: the types this ETL maps")
    ap.add_argument("--since", help="only resources changed after this instant, e.g. 2020-01-01T00:00:00Z")
    ap.add_argument("--keep-server-files", action="store_true",
                    help="skip the DELETE that tells the server to discard the export")
    args = ap.parse_args()

    server = args.server.rstrip("/")
    types = args.type or DEFAULT_TYPES

    print("checking what the server supports ...")
    available = supported_types(server)
    if available is not None:
        missing = [t for t in types if t not in available]
        types = [t for t in types if t in available]
        if missing:
            print(f"  not available here, dropped: {', '.join(missing)}")
        if not types:
            raise SystemExit("none of the requested types are available on this server")
        print(f"  requesting {len(types)} type(s)")

    # Start clean: download() appends, so a stale directory would double counts.
    if args.out.exists():
        for f in args.out.glob("*.ndjson"):
            f.unlink()

    status_url = kickoff(server, types, args.since)
    print("\npolling:")
    manifest = poll(status_url)

    print(f"\ntransactionTime: {manifest.get('transactionTime')}")
    total, warnings = download(manifest, args.out)

    if not args.keep_server_files:
        code, _, _ = request(status_url, method="DELETE")
        print(f"\ncleaned up export on server (HTTP {code})")

    print(f"\n{total} resources -> {args.out}")
    if warnings:
        print(f"\n! {len(warnings)} WARNING(S):")
        for w in warnings:
            print(f"    {w}")
    else:
        print("complete: every file matched the count in the manifest")

    print(f"\nnext:\n  python scripts/run_etl.py --fhir {args.out} --vocab data/omop/vocab.duckdb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
