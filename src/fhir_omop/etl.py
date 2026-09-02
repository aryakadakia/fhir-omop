"""FHIR bundle -> OMOP CDM loader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from fhir_omop import ddl
from fhir_omop.mappings.person import map_patient


@dataclass
class LoadReport:
    resources_seen: int = 0
    persons_loaded: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)
    issues: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Patient resources read : {self.resources_seen}",
            f"person rows loaded     : {self.persons_loaded}",
            f"rejected               : {len(self.rejected)}",
            f"rows with issues       : {len(set(s for s, _ in self.issues))}",
        ]
        return "\n".join(lines)


def iter_patients(path: Path):
    """Yield (source_file, Patient resource) from a bundle or a raw resource."""
    doc = json.loads(path.read_text())
    if doc.get("resourceType") == "Bundle":
        for entry in doc.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == "Patient":
                yield path.name, resource
    elif doc.get("resourceType") == "Patient":
        yield path.name, doc


def create_schema(con) -> None:
    for statement in ddl.ALL:
        con.execute(statement)


def attach_vocabulary(con, vocab_db: Path) -> None:
    """Copy the OMOP vocabulary tables in from an existing OMOP database.

    Copied rather than referenced so the output database is self-contained and
    portable -- a reviewer can open one file and see everything.
    """
    con.execute(f"ATTACH '{vocab_db}' AS src (READ_ONLY)")
    for table in ("concept", "concept_relationship", "concept_ancestor"):
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM src.base.{table}"
        )
    con.execute("DETACH src")


def load(fhir_dir: Path, out_db: Path, vocab_db: Path | None = None) -> LoadReport:
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    con = duckdb.connect(str(out_db))
    if vocab_db is not None:
        attach_vocabulary(con, vocab_db)
    create_schema(con)

    report = LoadReport()
    person_id = 0

    for path in sorted(fhir_dir.rglob("*.json")):
        for source_file, patient in iter_patients(path):
            report.resources_seen += 1
            person_id += 1
            mapped = map_patient(patient, person_id)

            if mapped is None:
                person_id -= 1  # do not burn an id on a rejected resource
                report.rejected.append(
                    (patient.get("id", "<no id>"), "missing id or birthDate year")
                )
                continue

            con.execute(
                "INSERT INTO person VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                list(mapped.as_row()),
            )
            con.execute(
                "INSERT INTO _etl_provenance "
                "(cdm_table, cdm_pk, source_file, source_resource, source_id) "
                "VALUES (?,?,?,?,?)",
                ["person", mapped.person_id, source_file, "Patient", mapped.person_source_value],
            )
            report.persons_loaded += 1
            for issue in mapped.issues:
                report.issues.append((mapped.person_source_value, issue))

    con.close()
    return report
