"""FHIR bundle -> OMOP CDM loader.

Four passes, and the ordering is forced by the reference graph:

  1  Patient   -> person              + index: fhir patient id -> person_id
  2  Encounter -> visit_occurrence    + index: fhir encounter id -> visit id
  3  Condition -> condition_occurrence | observation   (domain-routed)
     Observation -> measurement | observation          (domain-routed)
     MedicationRequest -> drug_exposure
  4  derive observation_period from everything loaded

Clinical resources reference both a patient and an encounter, so both indexes
must exist before pass 3. observation_period is derived last because it is a
function of every event date in the database.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from fhir_omop import cdm_remainder, ddl
from fhir_omop.vocab import clear_caches
from fhir_omop.mappings import eras, observation_period
from fhir_omop.mappings.condition import map_condition, subject_id
from fhir_omop.mappings.drug import map_medication_request
from fhir_omop.mappings.immunization import map_immunization, patient_id
from fhir_omop.mappings.measurement import map_observation, reference_id
from fhir_omop.mappings.misc_clinical import (
    administration_encounter, administration_subject, allergy_patient,
    device_patient, map_allergy, map_device, map_medication_administration,
)
from fhir_omop.mappings.organisation import (
    address_key, map_location, map_organization, map_practitioner,
)
from fhir_omop.mappings.person import map_patient
from fhir_omop.mappings.procedure import map_procedure
from fhir_omop.mappings.procedure import subject_id as procedure_subject_id
from fhir_omop.mappings.visit import map_encounter

BATCH_SIZE = 5000

INSERT_SQL = {
    "person": "INSERT INTO person VALUES (" + ",".join("?" * 18) + ")",
    "visit_occurrence": "INSERT INTO visit_occurrence VALUES (" + ",".join("?" * 17) + ")",
    "condition_occurrence": "INSERT INTO condition_occurrence VALUES (" + ",".join("?" * 16) + ")",
    "observation": "INSERT INTO observation VALUES (" + ",".join("?" * 21) + ")",
    "measurement": "INSERT INTO measurement VALUES (" + ",".join("?" * 23) + ")",
    "drug_exposure": "INSERT INTO drug_exposure VALUES (" + ",".join("?" * 23) + ")",
    "procedure_occurrence": "INSERT INTO procedure_occurrence VALUES (" + ",".join("?" * 16) + ")",
    "device_exposure": "INSERT INTO device_exposure VALUES (" + ",".join("?" * 19) + ")",
    "provider": "INSERT INTO provider VALUES (" + ",".join("?" * 13) + ")",
    "care_site": "INSERT INTO care_site VALUES (" + ",".join("?" * 6) + ")",
    "location": "INSERT INTO location VALUES (" + ",".join("?" * 12) + ")",
    "_etl_provenance": (
        "INSERT INTO _etl_provenance "
        "(cdm_table, cdm_pk, source_file, source_resource, source_id) VALUES (?,?,?,?,?)"
    ),
}


@dataclass
class LoadReport:
    bundles: int = 0
    seen: Counter = field(default_factory=Counter)
    loaded: Counter = field(default_factory=Counter)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    issues: Counter = field(default_factory=Counter)
    observation_periods: int = 0
    drug_eras: int = 0
    condition_eras: int = 0
    exposures_rolled_up: int = 0

    def summary(self) -> str:
        lines = ["FHIR resources read:"]
        for name, count in sorted(self.seen.items()):
            lines.append(f"  {name:<22} {count:>7}")
        lines.append("CDM rows written:")
        for name, count in sorted(self.loaded.items()):
            lines.append(f"  {name:<22} {count:>7}")
        lines.append(f"  {'observation_period':<22} {self.observation_periods:>7}")
        lines.append(f"  {'drug_era':<22} {self.drug_eras:>7}"
                     f"   (from {self.exposures_rolled_up:,} exposures with an ingredient)")
        lines.append(f"  {'condition_era':<22} {self.condition_eras:>7}")
        lines.append(f"rejected: {len(self.rejected)}")
        return "\n".join(lines)


class Writer:
    """Batched inserts with per-table row buffers."""

    def __init__(self, con):
        self.con = con
        self.buffers: dict[str, list] = {name: [] for name in INSERT_SQL}

    def add(self, table: str, row: tuple) -> None:
        buf = self.buffers[table]
        buf.append(row)
        if len(buf) >= BATCH_SIZE:
            self.flush(table)

    def flush(self, table: str) -> None:
        buf = self.buffers[table]
        if buf:
            self.con.executemany(INSERT_SQL[table], buf)
            buf.clear()

    def flush_all(self) -> None:
        for table in self.buffers:
            self.flush(table)


def load_resources(path: Path) -> list[dict]:
    """Read one file into a flat list of FHIR resources.

    Three shapes arrive depending on where the data came from:

      Bundle          a search result or a Synthea per-patient file
      bare resource   a single resource fetched by id
      .ndjson         one resource per line, no envelope -- what Bulk Data
                      returns, since a population export is too large to hold
                      in one JSON document

    Flattening them here means every pass downstream works the same way
    regardless of source.
    """
    if path.suffix == ".ndjson":
        out = []
        with path.open() as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    # One malformed line should not discard the file, but it
                    # must be visible: silently skipping data is the failure
                    # this pipeline documents most often.
                    print(f"  ! {path.name} line {n}: {e}; line skipped")
        return out

    doc = json.loads(path.read_text())
    if doc.get("resourceType") == "Bundle":
        return [e.get("resource") or {} for e in doc.get("entry", [])]
    return [doc]


def iter_resources(resources: list[dict], resource_type: str):
    for resource in resources:
        if resource.get("resourceType") == resource_type:
            yield resource


def _has_table(con, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [name]
    ).fetchone()[0])


def create_schema(con) -> None:
    for statement in ddl.ALL:
        con.execute(statement)
    # The CDM 5.4 tables this ETL does not populate are created empty. OHDSI
    # tooling treats a missing table as an error but an empty one as a
    # legitimate finding, so this is what makes the output readable by the
    # Data Quality Dashboard, Achilles and ATLAS.
    for statement in cdm_remainder.ALL:
        con.execute(statement)


def attach_vocabulary(con, vocab_db: Path) -> None:
    con.execute(f"ATTACH '{vocab_db}' AS src (READ_ONLY)")
    # The first three are what the ETL itself reads. The rest are copied for
    # the benefit of downstream tooling, which checks them.
    required = ("concept", "concept_relationship", "concept_ancestor")
    optional = ("vocabulary", "domain", "concept_class", "relationship",
                "concept_synonym", "drug_strength")
    available = {r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = 'src'"
    ).fetchall()}
    for table in required + optional:
        if table in available:
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM src.base.{table}")
    con.execute("DETACH src")
    con.execute("CREATE INDEX IF NOT EXISTS idx_concept_code ON concept(concept_code, vocabulary_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_concept_id ON concept(concept_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cr_1 ON concept_relationship(concept_id_1, relationship_id)")


def load(fhir_dir: Path, out_db: Path, vocab_db: Path | None = None) -> LoadReport:
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    # The concept cache is module-level, so it must be dropped between loads:
    # a second run against a different vocabulary would otherwise serve stale
    # resolutions from the first.
    clear_caches()

    con = duckdb.connect(str(out_db))
    if vocab_db is not None:
        attach_vocabulary(con, vocab_db)
    create_schema(con)

    report = LoadReport()
    writer = Writer(con)
    files = sorted(list(fhir_dir.rglob("*.json")) + list(fhir_dir.rglob("*.ndjson")))
    report.bundles = len(files)

    # Parsing 1.3 GB of JSON twice is the dominant cost, so each bundle is read
    # once and all passes run over the parsed document.
    person_index: dict[str, int] = {}
    visit_index: dict[str, int] = {}
    ids = Counter()

    def next_id(table: str) -> int:
        ids[table] += 1
        return ids[table]

    # ---- pass 0: reference entities ----------------------------------------
    # Practitioners and organizations are shared reference data, not patient
    # data: the same practitioner appears in a bundle for every patient they
    # treated. They are deduplicated by source id across the whole corpus --
    # loading them per bundle would create one provider row per patient
    # treated and make any per-clinician analysis meaningless.
    provider_index: dict[str, int] = {}
    care_site_index: dict[str, int] = {}
    location_index: dict[tuple, int] = {}

    def location_for(address: dict | None) -> int | None:
        """Locations have no source id in FHIR, so they are keyed by content."""
        key = address_key(address)
        if key is None:
            return None
        if key not in location_index:
            lid = next_id("location")
            location_index[key] = lid
            writer.add("location", map_location(address, lid).as_row())
        return location_index[key]

    for path in files:
        doc = load_resources(path)

        for org in iter_resources(doc, "Organization"):
            report.seen["Organization"] += 1
            source_id = org.get("id")
            if not source_id or source_id in care_site_index:
                continue
            addresses = org.get("address") or []
            lid = location_for(addresses[0] if addresses else None)
            cid = next_id("care_site")
            mapped = map_organization(org, cid, lid)
            if mapped is None:
                ids["care_site"] -= 1
                continue
            care_site_index[source_id] = cid
            writer.add("care_site", mapped.as_row())
            writer.add("_etl_provenance", ("care_site", cid, path.name, "Organization", source_id))

        for prac in iter_resources(doc, "Practitioner"):
            report.seen["Practitioner"] += 1
            source_id = prac.get("id")
            if not source_id or source_id in provider_index:
                continue
            pid_new = next_id("provider")
            mapped = map_practitioner(con, prac, pid_new, None)
            if mapped is None:
                ids["provider"] -= 1
                continue
            provider_index[source_id] = pid_new
            writer.add("provider", mapped.as_row())
            writer.add("_etl_provenance", ("provider", pid_new, path.name, "Practitioner", source_id))
            report.issues.update(mapped.issues)

    writer.flush_all()
    report.loaded["provider"] = ids["provider"]
    report.loaded["care_site"] = ids["care_site"]
    report.loaded["location"] = ids["location"]

    # Each pass loops over every file before the next begins. Nesting them
    # inside one file loop only works if each file is self-contained -- true of
    # a Synthea per-patient bundle, false of a bulk export, which splits
    # resources BY TYPE across files. With Condition.ndjson read before
    # Patient.ndjson, every condition resolves against an empty index.
    for path in files:
        doc = load_resources(path)

        # ---- pass 1: person ------------------------------------------------
        for patient in iter_resources(doc, "Patient"):
            report.seen["Patient"] += 1
            mapped = map_patient(patient, ids["person"] + 1, con)
            if mapped is None:
                report.rejected.append((patient.get("id", "?"), "Patient: no id or birth year"))
                continue
            pid = next_id("person")
            person_index[mapped.person_source_value] = pid
            writer.add("person", mapped.as_row())
            writer.add("_etl_provenance",
                       ("person", pid, path.name, "Patient", mapped.person_source_value))
            report.issues.update(mapped.issues)
    report.loaded["person"] = ids["person"]
    writer.flush_all()

    for path in files:
        doc = load_resources(path)

        # ---- pass 2: visits ------------------------------------------------
        for encounter in iter_resources(doc, "Encounter"):
            report.seen["Encounter"] += 1
            pid = person_index.get(reference_id(encounter.get("subject")))
            if pid is None:
                report.rejected.append((encounter.get("id", "?"), "Encounter: unresolved subject"))
                continue
            mapped = map_encounter(encounter, ids["visit_occurrence"] + 1, pid)
            if mapped is None:
                report.rejected.append((encounter.get("id", "?"), "Encounter: excluded status or no start"))
                continue
            vid = next_id("visit_occurrence")
            mapped.visit_occurrence_id = vid
            mapped.care_site_id = care_site_index.get(
                reference_id(encounter.get("serviceProvider")))
            participants = encounter.get("participant") or []
            if participants:
                mapped.provider_id = provider_index.get(
                    reference_id(participants[0].get("individual")))
            visit_index[mapped.source_id] = vid
            writer.add("visit_occurrence", mapped.as_row())
            writer.add("_etl_provenance",
                       ("visit_occurrence", vid, path.name, "Encounter", mapped.source_id))
            report.issues.update(mapped.issues)

    writer.flush_all()

    for path in files:
        doc = load_resources(path)

        # ---- pass 3a: conditions -------------------------------------------
        for condition in iter_resources(doc, "Condition"):
            report.seen["Condition"] += 1
            pid = person_index.get(subject_id(condition))
            if pid is None:
                report.rejected.append((condition.get("id", "?"), "Condition: unresolved subject"))
                continue
            mapped = map_condition(con, condition, 0, pid)
            if mapped is None:
                report.rejected.append((condition.get("id", "?"), "Condition: not asserted or no start date"))
                continue
            table = mapped.target_table
            rid = next_id(table)
            mapped.condition_occurrence_id = rid
            row = mapped.as_observation_row() if table == "observation" else mapped.as_row()
            writer.add(table, row)
            writer.add("_etl_provenance", (table, rid, path.name, "Condition", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3b: observations -> measurement | observation ------------
        for obs in iter_resources(doc, "Observation"):
            report.seen["Observation"] += 1
            pid = person_index.get(reference_id(obs.get("subject")))
            if pid is None:
                report.rejected.append((obs.get("id", "?"), "Observation: unresolved subject"))
                continue
            vid = visit_index.get(reference_id(obs.get("encounter")))
            mapped = map_observation(con, obs, 0, pid, vid)
            if mapped is None:
                report.rejected.append((obs.get("id", "?"), "Observation: excluded status or no date"))
                continue
            table = mapped.target_table
            rid = next_id(table)
            mapped.row_id = rid
            row = (mapped.as_measurement_row() if table == "measurement"
                   else mapped.as_observation_row())
            writer.add(table, row)
            writer.add("_etl_provenance", (table, rid, path.name, "Observation", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3c: medications ------------------------------------------
        for request in iter_resources(doc, "MedicationRequest"):
            report.seen["MedicationRequest"] += 1
            pid = person_index.get(reference_id(request.get("subject")))
            if pid is None:
                report.rejected.append((request.get("id", "?"), "MedicationRequest: unresolved subject"))
                continue
            vid = visit_index.get(reference_id(request.get("encounter")))
            mapped = map_medication_request(con, request, ids["drug_exposure"] + 1, pid, vid)
            if mapped is None:
                report.rejected.append((request.get("id", "?"), "MedicationRequest: excluded status or no date"))
                continue
            did = next_id("drug_exposure")
            mapped.drug_exposure_id = did
            writer.add("drug_exposure", mapped.as_row())
            writer.add("_etl_provenance",
                       ("drug_exposure", did, path.name, "MedicationRequest", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3d: procedures -> domain-routed --------------------------
        for proc in iter_resources(doc, "Procedure"):
            report.seen["Procedure"] += 1
            pid = person_index.get(procedure_subject_id(proc))
            if pid is None:
                report.rejected.append((proc.get("id", "?"), "Procedure: unresolved subject"))
                continue
            vid = visit_index.get(reference_id(proc.get("encounter")))
            mapped = map_procedure(con, proc, 0, pid, vid)
            if mapped is None:
                report.rejected.append((proc.get("id", "?"), "Procedure: not performed, or no date"))
                continue
            table = mapped.target_table
            rid = next_id(table)
            mapped.row_id = rid
            row = {
                "procedure_occurrence": mapped.as_procedure_row,
                "observation": mapped.as_observation_row,
                "measurement": mapped.as_measurement_row,
            }[table]()
            writer.add(table, row)
            writer.add("_etl_provenance", (table, rid, path.name, "Procedure", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3e: immunizations -> drug_exposure ----------------------
        for imm in iter_resources(doc, "Immunization"):
            report.seen["Immunization"] += 1
            pid = person_index.get(patient_id(imm))
            if pid is None:
                report.rejected.append((imm.get("id", "?"), "Immunization: unresolved patient"))
                continue
            vid = visit_index.get(reference_id(imm.get("encounter")))
            mapped = map_immunization(con, imm, ids["drug_exposure"] + 1, pid, vid)
            if mapped is None:
                report.rejected.append((imm.get("id", "?"), "Immunization: not given, or no usable date"))
                continue
            did = next_id("drug_exposure")
            mapped.drug_exposure_id = did
            writer.add("drug_exposure", mapped.as_row())
            writer.add("_etl_provenance",
                       ("drug_exposure", did, path.name, "Immunization", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3f: medication administrations -> drug_exposure ---------
        for admin in iter_resources(doc, "MedicationAdministration"):
            report.seen["MedicationAdministration"] += 1
            pid = person_index.get(administration_subject(admin))
            if pid is None:
                report.rejected.append((admin.get("id", "?"), "MedicationAdministration: unresolved subject"))
                continue
            vid = visit_index.get(administration_encounter(admin))
            mapped = map_medication_administration(con, admin, 0, pid, vid)
            if mapped is None:
                report.rejected.append((admin.get("id", "?"), "MedicationAdministration: not given, or no date"))
                continue
            did = next_id("drug_exposure")
            mapped.drug_exposure_id = did
            writer.add("drug_exposure", mapped.as_row())
            writer.add("_etl_provenance", ("drug_exposure", did, path.name, "MedicationAdministration", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3g: allergies -> observation -----------------------------
        for allergy in iter_resources(doc, "AllergyIntolerance"):
            report.seen["AllergyIntolerance"] += 1
            pid = person_index.get(allergy_patient(allergy))
            if pid is None:
                report.rejected.append((allergy.get("id", "?"), "AllergyIntolerance: unresolved patient"))
                continue
            mapped = map_allergy(con, allergy, 0, pid)
            if mapped is None:
                report.rejected.append((allergy.get("id", "?"), "AllergyIntolerance: refuted, or no date"))
                continue
            oid = next_id("observation")
            mapped.observation_id = oid
            writer.add("observation", mapped.as_row())
            writer.add("_etl_provenance", ("observation", oid, path.name, "AllergyIntolerance", mapped.source_id))
            report.issues.update(mapped.issues)

        # ---- pass 3h: devices -> device_exposure ---------------------------
        for device in iter_resources(doc, "Device"):
            report.seen["Device"] += 1
            pid = person_index.get(device_patient(device))
            if pid is None:
                report.rejected.append((device.get("id", "?"), "Device: unresolved patient"))
                continue
            mapped = map_device(con, device, 0, pid)
            if mapped is None:
                report.rejected.append((device.get("id", "?"), "Device: excluded status or no date"))
                continue
            deid = next_id("device_exposure")
            mapped.device_exposure_id = deid
            writer.add("device_exposure", mapped.as_row())
            writer.add("_etl_provenance", ("device_exposure", deid, path.name, "Device", mapped.source_id))
            report.issues.update(mapped.issues)

    writer.flush_all()
    for table in ("person", "visit_occurrence", "condition_occurrence",
                  "observation", "measurement", "drug_exposure",
                  "procedure_occurrence", "device_exposure"):
        report.loaded[table] = ids[table]

    # cdm_source carries the version and vocabulary metadata that downstream
    # tooling reads to decide which checks apply. One row, written last so the
    # vocabulary version can be read from what was actually loaded.
    vocab_version = con.execute(
        "SELECT coalesce(max(vocabulary_version), 'unknown') FROM vocabulary "
        "WHERE vocabulary_id = 'None'"
    ).fetchone()[0] if _has_table(con, "vocabulary") else "unknown"
    con.execute("DELETE FROM cdm_source")
    con.execute("""
        INSERT INTO cdm_source VALUES (
            'Synthea FHIR R4 sample', 'SYNTHEA', 'local',
            'Synthetic patients generated by Synthea, mapped from FHIR R4',
            'https://synthetichealth.github.io/synthea-sample-data/',
            'https://github.com/ -- see README',
            current_date, current_date, 'CDM v5.4', 798878, ?
        )
    """, [vocab_version])

    # ---- pass 4: derive observation_period and eras -------------------------
    # All three are functions of every event already loaded, so they run last.
    report.observation_periods = observation_period.derive(con)
    report.drug_eras, report.exposures_rolled_up = eras.derive_drug_era(con)
    report.condition_eras = eras.derive_condition_era(con)

    con.close()
    return report
