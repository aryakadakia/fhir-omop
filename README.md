# fhir-omop

A FHIR R4 → OMOP CDM 5.4 ETL, built to learn the two standards that matter most
in health data: **FHIR**, which moves clinical data between systems, and
**OMOP CDM**, which makes it analysable across institutions.

The interesting part of this problem is not the plumbing. It is the mapping
decisions — the places where a FHIR resource carries information the CDM has no
column for, or the CDM demands a value the source does not supply. Those
decisions are documented at the point they are made.

## Status

Implemented: `Patient` → `person`, `Condition` → `condition_occurrence` /
`observation` (domain-routed), with two-step standard-concept resolution,
provenance tracking, and a 13-check data-quality suite.

Run against the Synthea FHIR R4 sample (1,180 patients):

```
person                 1180
condition_occurrence   7904
observation             862   <- domain-routed out of condition_occurrence
_etl_provenance        9946
13/13 data-quality checks pass
```

Not yet implemented: `Observation` → `measurement`, `Encounter` →
`visit_occurrence`, `MedicationRequest` → `drug_exposure`,
`observation_period` derivation.

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

## Domain routing: the rule that is easy to miss

**A FHIR `Condition` does not always become a `condition_occurrence`.** OMOP
decides the destination table from the `domain_id` of the resolved standard
concept, not from the source resource type.

In the Synthea corpus, 862 of 8,766 `Condition` resources carry concepts that
OMOP places in the Observation domain:

| concept | domain | rows |
|---|---|---|
| Normal pregnancy | Observation | 516 |
| Body mass index 30+ - obesity | Observation | 346 |

Both are clinically reasonable things to send as a FHIR `Condition`. Both
belong in `observation` under the CDM. Loading them into
`condition_occurrence` fails silently — the rows insert, every foreign key
resolves, and prevalence queries return a wrong denominator with no error.

This was caught by the `condition_concept_in_condition_domain` check rather
than by reading the spec, which is the argument for writing the checks first.

## Two-step concept resolution

Resolving a source code takes two steps, and the second is the one that gets
dropped:

1. find the concept for `(code, vocabulary_id)`
2. if it is **not standard**, follow the `Maps to` relationship

388 conditions in this corpus resolve to non-standard `Prediabetes`
(`concept_id 40316773`) and must be redirected to its standard target. Used
directly, those rows would never match a cohort definition — cohort
definitions are written against standard concepts — so the patients would be
silently invisible rather than rejected.

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

**Current unmapped rate for conditions is 35.6%**, because the development
vocabulary is a Synthea-derived subset carrying only 834 SNOMED concepts. This
is a limitation of the vocabulary, not of the mapping logic — loading the full
[Athena](https://athena.ohdsi.org/) vocabulary is expected to resolve most of
it. It is reported rather than hidden, because an unmapped rate that is quietly
excluded from the report is how a broken ETL passes review.

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
