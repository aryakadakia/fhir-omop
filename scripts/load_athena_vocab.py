#!/usr/bin/env python3
"""Load a full OHDSI Athena vocabulary bundle into a DuckDB database.

Athena (https://athena.ohdsi.org/) requires its own free account -- separate
from, and unrelated to, a UMLS licence. The download cannot be automated: you
select vocabularies in the browser and receive a zip of tab-delimited CSVs.

CPT4 is the ONLY vocabulary needing a UMLS API key, because OHDSI cannot
redistribute CPT4 descriptions. Its codes ship with blank descriptions and a
separate cpt4.jar utility fetches them. Every other vocabulary, including all
of SNOMED, LOINC, RxNorm, ATC and UCUM, ships complete and needs no UMLS
involvement whatsoever.

Usage:
    python scripts/load_athena_vocab.py --zip ~/Downloads/vocabulary_download.zip
    python scripts/load_athena_vocab.py --dir ~/Downloads/athena_csv/

    # top up an existing vocabulary with vocabularies missed the first time,
    # without re-downloading gigabytes:
    python scripts/load_athena_vocab.py --zip ~/Downloads/gender_bundle.zip --merge

Then point the ETL at the result:
    python scripts/run_etl.py --fhir data/fhir/bulk --vocab data/omop/vocab.duckdb
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb

# Column types are declared explicitly rather than inferred, for two reasons
# that both cause silent, total failure if got wrong:
#
#   concept_code MUST be VARCHAR. SNOMED codes are all digits ("59621000"), so
#   a SNOMED-only bundle infers BIGINT -- and every lookup, which passes a
#   Python string, then matches nothing at all. No error, zero rows mapped.
#
#   Athena writes dates as YYYYMMDD with no separators, which infers as an
#   integer. `dateformat` below parses them properly.
#
# Types follow the CDM 5.4 specification.
SCHEMAS: dict[str, dict[str, str]] = {
    "CONCEPT": {
        "concept_id": "INTEGER", "concept_name": "VARCHAR", "domain_id": "VARCHAR",
        "vocabulary_id": "VARCHAR", "concept_class_id": "VARCHAR",
        "standard_concept": "VARCHAR", "concept_code": "VARCHAR",
        "valid_start_date": "DATE", "valid_end_date": "DATE", "invalid_reason": "VARCHAR",
    },
    "CONCEPT_RELATIONSHIP": {
        "concept_id_1": "INTEGER", "concept_id_2": "INTEGER",
        "relationship_id": "VARCHAR", "valid_start_date": "DATE",
        "valid_end_date": "DATE", "invalid_reason": "VARCHAR",
    },
    "CONCEPT_ANCESTOR": {
        "ancestor_concept_id": "INTEGER", "descendant_concept_id": "INTEGER",
        "min_levels_of_separation": "INTEGER", "max_levels_of_separation": "INTEGER",
    },
    "VOCABULARY": {
        "vocabulary_id": "VARCHAR", "vocabulary_name": "VARCHAR",
        "vocabulary_reference": "VARCHAR", "vocabulary_version": "VARCHAR",
        "vocabulary_concept_id": "INTEGER",
    },
    "DOMAIN": {
        "domain_id": "VARCHAR", "domain_name": "VARCHAR", "domain_concept_id": "INTEGER",
    },
    "CONCEPT_CLASS": {
        "concept_class_id": "VARCHAR", "concept_class_name": "VARCHAR",
        "concept_class_concept_id": "INTEGER",
    },
    "RELATIONSHIP": {
        "relationship_id": "VARCHAR", "relationship_name": "VARCHAR",
        "is_hierarchical": "VARCHAR", "defines_ancestry": "VARCHAR",
        "reverse_relationship_id": "VARCHAR", "relationship_concept_id": "INTEGER",
    },
    "CONCEPT_SYNONYM": {
        "concept_id": "INTEGER", "concept_synonym_name": "VARCHAR",
        "language_concept_id": "INTEGER",
    },
}

REQUIRED = ["CONCEPT", "CONCEPT_RELATIONSHIP"]
OPTIONAL = [t for t in SCHEMAS if t not in REQUIRED]


def load_csv(con, csv_path: Path, table: str, columns: dict[str, str]) -> int:
    column_spec = ", ".join(f"'{name}': '{sql_type}'" for name, sql_type in columns.items())
    con.execute(f"""
        CREATE OR REPLACE TABLE {table} AS
        SELECT * FROM read_csv(
            '{csv_path}',
            delim = '\t',
            header = true,
            -- Athena does not quote fields, and drug names legitimately
            -- contain double quotes. Quote handling MUST be off or the parser
            -- desynchronises partway through the file.
            quote = '',
            escape = '',
            nullstr = '',
            dateformat = '%Y%m%d',
            columns = {{{column_spec}}}
        )
    """)
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# Rows are matched on these keys when merging, so a top-up bundle can be
# folded into an existing vocabulary without duplicating what is already there.
MERGE_KEYS = {
    "concept": ["concept_id"],
    "concept_relationship": ["concept_id_1", "concept_id_2", "relationship_id"],
    "concept_ancestor": ["ancestor_concept_id", "descendant_concept_id"],
    "vocabulary": ["vocabulary_id"],
    "domain": ["domain_id"],
    "concept_class": ["concept_class_id"],
    "relationship": ["relationship_id"],
    "concept_synonym": ["concept_id", "concept_synonym_name"],
}


def merge_table(con, staging: str, target: str) -> int:
    """Insert rows from `staging` that are not already in `target`.

    Anti-joined on the table's key rather than replacing wholesale, so an
    existing vocabulary is never partially overwritten by a smaller bundle.
    """
    keys = MERGE_KEYS[target.split(".")[-1]]
    condition = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    before = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    con.execute(f"""
        INSERT INTO {target}
        SELECT s.* FROM {staging} s
        WHERE NOT EXISTS (SELECT 1 FROM {target} t WHERE {condition})
    """)
    after = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    return after - before


def load_directory(csv_dir: Path, out_db: Path, merge: bool = False) -> None:
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if merge and not out_db.exists():
        sys.exit(f"--merge given but {out_db} does not exist; run without it first")
    if not merge and out_db.exists():
        out_db.unlink()
    con = duckdb.connect(str(out_db))

    # The ETL reads tables under `base`, matching the omcp Synthea layout, so
    # the two vocabularies are drop-in interchangeable.
    con.execute("CREATE SCHEMA IF NOT EXISTS base")

    found = {p.stem.upper(): p for p in csv_dir.rglob("*.csv")}
    missing = [t for t in REQUIRED if t not in found]
    if missing:
        sys.exit(f"missing required Athena files: {', '.join(missing)}\nlooked in {csv_dir}")

    for table in REQUIRED + OPTIONAL:
        if table not in found:
            print(f"  skip   {table.lower()} (not in bundle)")
            continue
        size_mb = found[table].stat().st_size / 1024 / 1024
        name = table.lower()
        print(f"  {'merge ' if merge else 'load  '} {name:<22} ({size_mb:,.0f} MB) ...",
              end="", flush=True)
        if merge:
            load_csv(con, found[table], f"base._staging_{name}", SCHEMAS[table])
            added = merge_table(con, f"base._staging_{name}", f"base.{name}")
            con.execute(f"DROP TABLE base._staging_{name}")
            print(f"\r  merged {name:<22} {added:>14,} new rows      ")
        else:
            rows = load_csv(con, found[table], f"base.{name}", SCHEMAS[table])
            print(f"\r  loaded {name:<22} {rows:>14,} rows          ")

    if not merge:
        print("\nindexing (this is what makes the ETL fast) ...")
        con.execute("CREATE INDEX idx_concept_code ON base.concept(concept_code, vocabulary_id)")
        con.execute("CREATE INDEX idx_concept_id ON base.concept(concept_id)")
        con.execute("CREATE INDEX idx_cr_1 ON base.concept_relationship(concept_id_1, relationship_id)")
        if "CONCEPT_ANCESTOR" in found:
            con.execute("CREATE INDEX idx_ca_anc ON base.concept_ancestor(ancestor_concept_id)")

    # Guard against the failure this loader exists to prevent. If concept_code
    # did not land as text, every lookup in the ETL would return nothing while
    # reporting no error at all.
    code_type = con.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'concept' AND column_name = 'concept_code'"
    ).fetchone()[0]
    if code_type.upper() not in ("VARCHAR", "TEXT", "STRING"):
        sys.exit(f"concept_code loaded as {code_type}, expected VARCHAR -- lookups would silently fail")

    print("\nvocabularies loaded:")
    for name, n in con.execute(
        "SELECT vocabulary_id, count(*) n FROM base.concept GROUP BY 1 ORDER BY n DESC LIMIT 15"
    ).fetchall():
        print(f"  {name:<20} {n:>12,}")

    total = con.execute("SELECT count(*) FROM base.concept").fetchone()[0]
    standard = con.execute(
        "SELECT count(*) FROM base.concept WHERE standard_concept = 'S'"
    ).fetchone()[0]
    print(f"\n  {total:,} concepts, {standard:,} standard")

    con.close()
    print(f"\nwrote {out_db}")
    print(f"\nnext:\n  python scripts/run_etl.py --fhir data/fhir/bulk --vocab {out_db}")


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path, help="Athena vocabulary_download.zip")
    source.add_argument("--dir", type=Path, help="already-extracted Athena CSV directory")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data/omop/vocab.duckdb")
    parser.add_argument("--merge", action="store_true",
                        help="fold this bundle into an existing vocabulary instead of "
                             "rebuilding it -- for topping up with vocabularies missed "
                             "in an earlier download")
    args = parser.parse_args()

    if args.dir:
        load_directory(args.dir, args.out, args.merge)
    else:
        # Extracted to a temp dir rather than into the repo: the CSVs are far
        # larger than the DuckDB file they become, and are not worth keeping.
        with tempfile.TemporaryDirectory() as tmp:
            print(f"extracting {args.zip} ...")
            with zipfile.ZipFile(args.zip) as z:
                z.extractall(tmp)
            load_directory(Path(tmp), args.out, args.merge)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
