#!/usr/bin/env python3
"""Run the FHIR -> OMOP ETL and print the data-quality report.

    python scripts/run_etl.py [--fhir DIR] [--out DB] [--vocab DB]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import duckdb

from fhir_omop import dq
from fhir_omop.etl import load

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VOCAB = Path.home() / "tools/omcp/synthetic_data/synthea.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fhir", type=Path, default=ROOT / "data/fhir")
    parser.add_argument("--out", type=Path, default=ROOT / "data/omop/omop.duckdb")
    parser.add_argument("--vocab", type=Path, default=DEFAULT_VOCAB)
    args = parser.parse_args()

    vocab = args.vocab if args.vocab.exists() else None
    if vocab is None:
        print(f"! vocabulary not found at {args.vocab} -- "
              "vocabulary-dependent checks will be skipped\n")

    report = load(args.fhir, args.out, vocab)

    print("=== ETL ===")
    print(report.summary())

    if report.rejected:
        print("\n--- rejected resources ---")
        for source_id, reason in report.rejected[:8]:
            print(f"  {source_id}: {reason}")

    if report.rejected:
        print(f"  ... {len(report.rejected)} total")

    if report.issues:
        print("\n--- mapping issues (by frequency) ---")
        for issue, count in sorted(report.issues.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {count:>6}  {issue}")

    con = duckdb.connect(str(args.out), read_only=True)

    print("\n=== data quality ===")
    failed = 0
    for check, violations in dq.run(con):
        status = "PASS" if violations == 0 else f"FAIL ({violations})"
        if violations:
            failed += 1
        print(f"  [{status:>9}] {check.category:<12} {check.name}")

    print("\n=== unmapped rates ===")
    for column, unmapped, total, pct in dq.unmapped_rates(con):
        print(f"  {column:<45} {unmapped}/{total}  ({pct:.1f}% unmapped)")

    con.close()

    print(f"\n{'OK' if failed == 0 else f'{failed} CHECK(S) FAILED'} -> {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
