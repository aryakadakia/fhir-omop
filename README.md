# fhir-omop

A FHIR R4 → OMOP CDM 5.4 ETL, built to learn the two standards that matter most
in health data: **FHIR**, which moves clinical data between systems, and
**OMOP CDM**, which makes it analysable across institutions.

The interesting part of this problem is not the plumbing. It is the mapping
decisions — the places where a FHIR resource carries information the CDM has no
column for, or the CDM demands a value the source does not supply. Those
decisions are documented at the point they are made.

## Status

Implemented: `Patient` → `person`, with vocabulary lookup, provenance tracking,
and a data-quality suite.

Not yet implemented: `Condition` → `condition_occurrence`, `Observation` →
`measurement` / `observation`, `Encounter` → `visit_occurrence`,
`MedicationRequest` → `drug_exposure`, `observation_period` derivation.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
python scripts/run_etl.py --fhir data/fhir/fixtures
pytest
```

Output lands in `data/omop/omop.duckdb`, readable by any DuckDB client.

## What it does

```
FHIR bundles ──► map_patient() ──► person ──► data-quality checks
                      │                │
                 vocabulary       _etl_provenance
                  lookup          (every row traceable)
```

- **`src/fhir_omop/vocab.py`** — all concept resolution in one place. No
  concept ids are hard-coded at their use sites.
- **`src/fhir_omop/mappings/person.py`** — the mapping, with each non-obvious
  decision explained inline.
- **`src/fhir_omop/dq.py`** — checks grouped as *conformance*, *completeness*
  and *plausibility*, following the OHDSI Data Quality Dashboard's structure.
- **`src/fhir_omop/ddl.py`** — CDM 5.4 DDL for the tables populated so far,
  plus a non-standard `_etl_provenance` table so every CDM row can be traced
  back to the FHIR resource that produced it.

## Three decisions worth reading

**1. Unmapped values become concept_id 0, never a guess.**
OMOP reserves `concept_id = 0` for "No matching concept". FHIR's
`Patient.gender` value set includes `other` and `unknown`, and OMOP has no
standard concept for either. Both map to 0, with the original code preserved in
`gender_source_value`. Choosing `male` or `female` instead would fabricate data
that no downstream analyst could detect.

**2. Partial dates are not filled in.**
FHIR `date` legally permits `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. CDM 5.4 requires
`year_of_birth` and permits null month and day — which maps onto FHIR's
precision model exactly. The common bug is defaulting to January 1st, which
invents a birthday for every year-precision record.

**3. Race and ethnicity are `NOT NULL` in the CDM, and often absent in reality.**
Synthea emits US Core race/ethnicity extensions because it models a US
population. **AU Core has no race extension at all** — Australian datasets
record Indigenous status under a different model that does not map onto OMOP's
race/ethnicity pair. This ETL sets both to 0 and preserves any source text,
rather than inventing a category. A prevalence figure broken down by race in a
dataset built otherwise would be an artifact of the ETL, not of the population.

The `unmapped rates` block in the ETL output exists for exactly this reason: a
pipeline can pass every conformance check while mapping almost nothing, and
only that figure reveals it.

## Vocabulary

The ETL needs an OMOP `concept` table. By default it copies one from a local
OMOP database (`--vocab`), so the output is a single self-contained file. The
full vocabulary is distributed by [OHDSI Athena](https://athena.ohdsi.org/);
the subset used in development carries SNOMED, LOINC, RxNorm, ATC and UCUM.

Without `--vocab`, the ETL still runs and the vocabulary-dependent checks are
skipped.

## Australian context

The standards are international; the code systems and regulations are not.

| US | Australia |
|---|---|
| US Core | [AU Core](https://hl7.org.au/fhir/core/) (Sparked / HL7 AU) |
| ICD-10-CM | ICD-10-AM |
| CPT / HCPCS | MBS items |
| RxNorm | AMT; SNOMED CT-AU |
| HIPAA | Privacy Act 1988, Australian Privacy Principles |

OMOP CDM is being deployed across Australian hospitals by
[AHDEN](https://ardc.edu.au/), with AIHW national hospital data being
standardised to the CDM.

## Data

`data/fhir/fixtures/` holds small hand-authored bundles chosen to exercise the
edge cases above — they are committed so the test suite runs with no download.
Bulk FHIR input under `data/fhir/` is gitignored.

## License

MIT
