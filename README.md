# fhir-omop

A FHIR R4 → OMOP CDM 5.4 ETL, built to learn the two standards that matter most
in health data: **FHIR**, which moves clinical data between systems, and
**OMOP CDM**, which makes it analysable across institutions.

The interesting part of this problem is not the plumbing. It is the mapping
decisions — the places where a FHIR resource carries information the CDM has no
column for, or the CDM demands a value the source does not supply. Those
decisions are documented at the point they are made.

## What it does

Twelve FHIR resource types are mapped, domain-routed against the full OHDSI
Athena vocabulary, with `observation_period`, `drug_era` and `condition_era`
derived afterwards. Every CDM row is traceable to the FHIR resource that
produced it.

Run against the Synthea FHIR R4 sample — 1,180 synthetic patients, 414,000
resources, no real patient data:

```
person                  1,180      provider              1,036
observation_period      1,180      care_site             1,035
visit_occurrence       46,868      location                970
condition_occurrence    8,583      device_exposure          58
measurement           253,867
observation            14,862      drug_era             11,205
procedure_occurrence   28,401      condition_era         8,450
drug_exposure          30,041      cohort                  406
```

```
206 tests · 36 built-in quality checks · 2,530 of 2,533 OHDSI DQD checks
unmapped: conditions 1.3% · measurements 0.0% · drugs 0.1% · visits 0.0%
```

Not mapped, deliberately: `Claim` and `ExplanationOfBenefit` (billing —
health economics, a project of its own), `DiagnosticReport` (a grouping
resource whose member observations are already mapped), and `CarePlan`,
`CareTeam`, `Goal`, `ImagingStudy` (no home in CDM 5.4 core).

## What went wrong, and how it was found

[`docs/bugs-found.md`](docs/bugs-found.md) records every defect found building
this, grouped by how it surfaced — by a data-quality check, by auditing an
assumption, by testing against real data, or before it could do damage. It also
lists the known limitations that are *not* defects, so nobody mistakes them for
either bugs or correctness.

The pattern worth taking from it: almost none of these raised an error.

## The manual

[`docs/manual.html`](docs/manual.html) is the operator's manual: what every
file does, how the nine-pass ETL works, what each CDM table holds, every
command with what it expects and produces, how to read the output, how to
extend it with a new resource type, and a troubleshooting section covering
every failure actually hit while building it. Written for a reader who has
never seen either standard.

Also published at
<https://claude.ai/code/artifact/790f712d-55a1-4bc9-998d-29b834f1ee52>

## The course

`docs/course.html` is a written course on both standards, taught against this
pipeline's actual output — what FHIR and OMOP each exist for, how references,
concepts, `Maps to` and the vocabulary hierarchy work, and the four rules that
govern the bridge between them. Open it in a browser.

It is also published as a hosted page:
<https://claude.ai/code/artifact/956dbf95-8822-4193-956f-37a732738b38>

> Note for republishing: the page body is authored without `<html>`/`<head>`/
> `<body>` because the host wraps it at publish time. Browsers insert those
> automatically, so the file still opens correctly from disk. When republishing
> after an edit, pass that URL explicitly or a second, separate page is created.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
python scripts/run_etl.py --fhir data/fhir/example
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

The development vocabulary was a 32k-concept Synthea subset, against which
conditions ran at 35.6% unmapped, observations 73.9% and drugs 84.3%. Loading
the full [Athena](https://athena.ohdsi.org/) release took those to 1.3%, 1.1%
and 0.1%.

More usefully, the real vocabulary **found two bugs the subset could not**,
neither of which raised an error:

- LOINC carries Note- and Procedure-domain concepts. The Observation mapping
  sent anything non-Measurement to `observation`, violating the domain rule on
  102 rows.
- `map_gender` returned hardcoded concept ids without checking they exist.
  `Gender` is a separately selectable Athena vocabulary; omitting it put a
  dangling reference in all 1,180 person rows.

Both now resolve to `concept_id 0` with source values preserved, so the gap
appears in the unmapped rate instead of as broken referential integrity.

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

## Scope

This is a learning project, and worth being clear about what that means. It runs
on one machine against one synthetic dataset, has no incremental loading, and
has never met a real EHR's quirks. It is not a production ETL.

What it does have is a complete audit trail: every mapping decision documented
where it is made, every defect found recorded in `docs/bugs-found.md` along with
how it surfaced, and both a hand-written and the community-standard check suite
run over the output.

## License

MIT — see [LICENSE](LICENSE).
