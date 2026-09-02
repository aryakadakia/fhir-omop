#!/usr/bin/env python3
"""Load a full OHDSI Athena vocabulary bundle into a DuckDB database.

Athena (https://athena.ohdsi.org/) requires a free account, and some
vocabularies (CPT4, and anything UMLS-derived) additionally require a personal
UMLS licence. The download therefore cannot be automated -- you request a
bundle in the browser and receive a zip of tab-delimited CSVs.

Usage:
    python scripts/load_athena_vocab.py --zip ~/Downloads/vocabulary_download.zip
    python scripts/load_athena_vocab.py --dir ~/Downloads/athena_csv/

Then point the ETL at the result:
    python scripts/run_etl.py --fhir data/fhir/bulk --vocab data/omop/vocab.duckdb

The full vocabulary is roughly 6-10 GB unzipped and contains several million
concepts, versus the 31,976 in the Synthea development subset.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb

# The Athena bundle always ships these as tab-delimited files with a header
# row. Only the tables this ETL reads are loaded; the rest are ignored.
REQUIRED = ["CONCEPT", "CONCEPT_RELATIONSHIP"]
OPTIONAL = ["CONCEPT_ANCESTOR", "VOCABULARY", "DOMAIN", "CONCEPT_CLASS",
            "RELATIONSHIP", "CONCEPT_SYNONYM", "DRUG_STRENGTH"]


def load_csv(con, csv_path: Path, table: str) -> int:
    # Athena files are tab-delimited with quoting disabled -- drug names
    # legitimately contain double quotes, so quote handling must be off or the
    # parser desynchronises mid-file.
    con.execute(f"""
        CREATE OR REPLACE TABLE {table} AS
        SELECT * FROM read_csv(
            '{csv_path}',
            delim = '\t',
            header = true,
            quote = '',
            escape = '',
            nullstr = '',
            sample_size = -1,
            ignore_errors = false
        )
    """)
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def load_directory(csv_dir: Path, out_db: Path) -> None:
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()
    con = duckdb.connect(str(out_db))

    # The ETL reads tables unqualified; `base` matches the omcp Synthea layout
    # so both vocabularies are drop-in interchangeable.
    con.execute("CREATE SCHEMA IF NOT EXISTS base")

    found = {p.stem.upper(): p for p in csv_dir.rglob("*.csv")}
    missing = [t for t in REQUIRED if t not in found]
    if missing:
        sys.exit(f"missing required Athena files: {', '.join(missing)}\n"
                 f"looked in {csv_dir}")

    for table in REQUIRED + OPTIONAL:
        if table not in found:
            print(f"  skip   {table.lower()} (not in bundle)")
            continue
        rows = load_csv(con, found[table], f"base.{table.lower()}")
        print(f"  loaded {table.lower():<22} {rows:>12,} rows")

    print("\nindexing...")
    con.execute("CREATE INDEX idx_concept_code ON base.concept(concept_code, vocabulary_id)")
    con.execute("CREATE INDEX idx_concept_id ON base.concept(concept_id)")
    con.execute("CREATE INDEX idx_cr_1 ON base.concept_relationship(concept_id_1, relationship_id)")

    vocabs = con.execute(
        "SELECT vocabulary_id, count(*) n FROM base.concept "
        "GROUP BY 1 ORDER BY n DESC LIMIT 15"
    ).fetchall()
    print("\ntop vocabularies:")
    for name, n in vocabs:
        print(f"  {name:<16} {n:>12,}")

    con.close()
    print(f"\nwrote {out_db}")


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path, help="Athena vocabulary_download.zip")
    source.add_argument("--dir", type=Path, help="already-extracted Athena CSV directory")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data/omop/vocab.duckdb")
    args = parser.parse_args()

    if args.dir:
        load_directory(args.dir, args.out)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            print(f"extracting {args.zip} ...")
            with zipfile.ZipFile(args.zip) as z:
                z.extractall(tmp)
            load_directory(Path(tmp), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
