"""FHIR bundle -> OMOP CDM loader.

Two passes, and the ordering is not incidental:

  pass 1  Patient -> person, building an index of FHIR patient id -> person_id
  pass 2  Condition -> condition_occurrence, resolving subject references
          through that index

Clinical resources reference their patient by id, so the person rows and the
index must exist before anything clinical can be attached. A single-pass
loader either orphans rows or has to re-read every bundle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from fhir_omop import ddl
from fhir_omop.mappings.condition import map_condition, subject_id
from fhir_omop.mappings.person import map_patient

# Insert in batches rather than per row. At ~1,200 bundles the per-statement
# overhead dominates otherwise.
BATCH_SIZE = 1000


@dataclass
class LoadReport:
    bundles: int = 0
    patients_seen: int = 0
    persons_loaded: int = 0
    conditions_seen: int = 0
    conditions_loaded: int = 0
    observations_loaded: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)
    issues: dict[str, int] = field(default_factory=dict)

    def note(self, issue: str) -> None:
        self.issues[issue] = self.issues.get(issue, 0) + 1

    def summary(self) -> str:
        return "\n".join([
            f"bundles read           : {self.bundles}",
            f"Patient resources      : {self.patients_seen}",
            f"person rows loaded     : {self.persons_loaded}",
            f"Condition resources    : {self.conditions_seen}",
            f"condition rows loaded  : {self.conditions_loaded}",
            f"observation rows loaded: {self.observations_loaded}  (domain-routed)",
            f"rejected               : {len(self.rejected)}",
        ])


def iter_resources(path: Path, resource_type: str):
    """Yield resources of one type from a Bundle or a bare resource file."""
    doc = json.loads(path.read_text())
    if doc.get("resourceType") == "Bundle":
        for entry in doc.get("entry", []):
            resource = entry.get("resource") or {}
            if resource.get("resourceType") == resource_type:
                yield resource
    elif doc.get("resourceType") == resource_type:
        yield doc


def create_schema(con) -> None:
    for statement in ddl.ALL:
        con.execute(statement)


def attach_vocabulary(con, vocab_db: Path) -> None:
    """Copy vocabulary tables in so the output database is self-contained."""
    con.execute(f"ATTACH '{vocab_db}' AS src (READ_ONLY)")
    for table in ("concept", "concept_relationship", "concept_ancestor"):
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM src.base.{table}"
        )
    con.execute("DETACH src")
    # Without these the two-step standard-concept resolution does a full scan
    # of `concept` for every coded value.
    con.execute("CREATE INDEX IF NOT EXISTS idx_concept_code ON concept(concept_code, vocabulary_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cr_1 ON concept_relationship(concept_id_1, relationship_id)")


def _flush(con, sql: str, rows: list) -> None:
    if rows:
        con.executemany(sql, rows)
        rows.clear()


def load(fhir_dir: Path, out_db: Path, vocab_db: Path | None = None) -> LoadReport:
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    con = duckdb.connect(str(out_db))
    if vocab_db is not None:
        attach_vocabulary(con, vocab_db)
    create_schema(con)

    report = LoadReport()
    files = sorted(fhir_dir.rglob("*.json"))
    report.bundles = len(files)

    # ---- pass 1: persons -------------------------------------------------
    person_index: dict[str, int] = {}
    person_id = 0
    person_rows: list[tuple] = []
    provenance_rows: list[tuple] = []

    person_sql = "INSERT INTO person VALUES (" + ",".join("?" * 18) + ")"
    prov_sql = (
        "INSERT INTO _etl_provenance "
        "(cdm_table, cdm_pk, source_file, source_resource, source_id) VALUES (?,?,?,?,?)"
    )

    for path in files:
        for patient in iter_resources(path, "Patient"):
            report.patients_seen += 1
            mapped = map_patient(patient, person_id + 1)
            if mapped is None:
                report.rejected.append(
                    (patient.get("id", "<no id>"), "Patient: missing id or birthDate year")
                )
                continue
            person_id += 1
            person_index[mapped.person_source_value] = person_id
            person_rows.append(mapped.as_row())
            provenance_rows.append(
                ("person", person_id, path.name, "Patient", mapped.person_source_value)
            )
            for issue in mapped.issues:
                report.note(issue)
            if len(person_rows) >= BATCH_SIZE:
                _flush(con, person_sql, person_rows)
                _flush(con, prov_sql, provenance_rows)

    _flush(con, person_sql, person_rows)
    _flush(con, prov_sql, provenance_rows)
    report.persons_loaded = person_id

    # ---- pass 2: conditions ----------------------------------------------
    condition_id = 0
    observation_id = 0
    condition_rows: list[tuple] = []
    observation_rows: list[tuple] = []
    condition_sql = "INSERT INTO condition_occurrence VALUES (" + ",".join("?" * 16) + ")"
    observation_sql = "INSERT INTO observation VALUES (" + ",".join("?" * 21) + ")"

    for path in files:
        for condition in iter_resources(path, "Condition"):
            report.conditions_seen += 1

            patient_ref = subject_id(condition)
            person_pk = person_index.get(patient_ref) if patient_ref else None
            if person_pk is None:
                # An orphan: the condition references a patient that produced no
                # person row. Never load it -- person_id is a required foreign
                # key and a fabricated one silently attaches a diagnosis to the
                # wrong patient.
                report.rejected.append(
                    (condition.get("id", "<no id>"),
                     f"Condition: unresolved subject reference {patient_ref!r}")
                )
                continue

            mapped = map_condition(con, condition, condition_id + 1, person_pk)
            if mapped is None:
                report.rejected.append(
                    (condition.get("id", "<no id>"),
                     "Condition: refuted/entered-in-error, or no usable start date")
                )
                continue

            # Domain routing: a FHIR Condition lands in whichever CDM table
            # its standard concept's domain dictates.
            if mapped.target_table == "observation":
                observation_id += 1
                mapped.condition_occurrence_id = observation_id
                observation_rows.append(mapped.as_observation_row())
                provenance_rows.append(
                    ("observation", observation_id, path.name, "Condition", mapped.source_id)
                )
            else:
                condition_id += 1
                mapped.condition_occurrence_id = condition_id
                condition_rows.append(mapped.as_row())
                provenance_rows.append(
                    ("condition_occurrence", condition_id, path.name, "Condition", mapped.source_id)
                )

            for issue in mapped.issues:
                report.note(issue)
            if len(condition_rows) >= BATCH_SIZE:
                _flush(con, condition_sql, condition_rows)
                _flush(con, prov_sql, provenance_rows)
            if len(observation_rows) >= BATCH_SIZE:
                _flush(con, observation_sql, observation_rows)
                _flush(con, prov_sql, provenance_rows)

    _flush(con, condition_sql, condition_rows)
    _flush(con, observation_sql, observation_rows)
    _flush(con, prov_sql, provenance_rows)
    report.conditions_loaded = condition_id
    report.observations_loaded = observation_id

    con.close()
    return report
