# fhir-omop

A FHIR R4 → OMOP CDM 5.4 ETL.

**FHIR** is the format hospitals use to hand out one patient's record at a time.
**OMOP CDM** is the format the research community uses to ask questions across a
whole population. This converts the first into the second.

## What it does

Twelve FHIR resource types are mapped, domain-routed against the full OHDSI
Athena vocabulary, with `observation_period`, `drug_era` and `condition_era`
derived afterwards. Every CDM row is traceable back to the FHIR resource that
produced it.

Input can be files on disk, a live FHIR server pulled patient by patient, or a
whole population via Bulk Data export.

Run against the Synthea FHIR R4 sample: 1,180 synthetic patients, 414,000
resources, no real patient data.

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

Not mapped, deliberately: `Claim` and `ExplanationOfBenefit` (billing data,
which is a project of its own), `DiagnosticReport` (a grouping resource whose
member observations are already mapped), and `CarePlan`, `CareTeam`, `Goal`
and `ImagingStudy` (no home in CDM 5.4 core).

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
python scripts/run_etl.py --fhir data/fhir/example
pytest
```

That runs against one committed sample patient, needs no downloads, and
finishes in seconds. Output lands in `data/omop/omop.duckdb`, readable by any
DuckDB client.

## Reproducing the full results

The figures above come from 1,180 patients and a 4.25M-concept vocabulary,
neither of which is in this repository: the first is 1.3 GB of freely
redistributable sample data, and the second is licensed and must be requested
under your own account. Both are a download away.

**1. Source data.** The Synthea FHIR R4 sample:

```bash
curl -L -o synthea.zip https://synthetichealth.github.io/synthea-sample-data/downloads/synthea_sample_data_fhir_r4_sep2019.zip
unzip -q synthea.zip -d data/fhir/bulk
```

**2. Vocabulary.** Requires a free [Athena](https://athena.ohdsi.org/) account.
Select **SNOMED, LOINC, RxNorm, RxNorm Extension, ATC, CVX, ICD10, OMOP
Extension, Gender, Race, Ethnicity** — skip CPT4, which needs a UMLS licence and
an extra tool. Past requests stay downloadable at
[athena.ohdsi.org/vocabulary/download-history](https://athena.ohdsi.org/vocabulary/download-history).

```bash
python scripts/load_athena_vocab.py --zip ~/Downloads/vocabulary_download.zip
```

**3. Run it.** About nine minutes.

```bash
python scripts/run_etl.py --fhir data/fhir/bulk --vocab data/omop/vocab.duckdb
```

**4. Optional, needing R 4.4+:**

```bash
Rscript -e 'install.packages(c("DatabaseConnector","duckdb","CirceR","CohortGenerator","remotes"))'
Rscript -e 'remotes::install_github(c("OHDSI/DataQualityDashboard","OHDSI/Capr"), upgrade="never")'

Rscript scripts/make_thresholds.R   # threshold overrides, with reasons
Rscript scripts/run_dqd.R           # 2,533 OHDSI quality checks
Rscript scripts/build_cohorts.R     # define and generate cohorts
```

To view the quality dashboard, from an R session in the repository root:

```r
DataQualityDashboard::viewDqDashboard(normalizePath("results/dqd/dqd-results.json"))
```

The path must be absolute. `viewDqDashboard` stores it and then Shiny changes
the working directory before reading it, so a relative path renders an empty
page with no error.

Two things worth knowing: `load_athena_vocab.py --merge` tops up an existing
vocabulary with one you missed rather than rebuilding several gigabytes, and
the ETL runs without a vocabulary at all — codes simply resolve to
`concept_id 0` and the unmapped rates report it.

[`docs/manual.md`](docs/manual.md) covers all of this in more detail, including
what each command produces and how to read the output.

To pull from a live FHIR server instead of files:

```bash
# one patient at a time, verified against the server's reported totals
python scripts/fetch_fhir.py --server https://launch.smarthealthit.org/v/r4/fhir --patients 5

# or a whole population via FHIR Bulk Data
python scripts/bulk_export.py --server https://bulk-data.smarthealthit.org/fhir
```

Both write files the ETL reads directly. Bundles, bare resources and
newline-delimited JSON are all accepted.

Optional, and requiring R:

```bash
Rscript scripts/run_dqd.R          # OHDSI Data Quality Dashboard, 2,533 checks
Rscript scripts/build_cohorts.R    # define and generate cohorts
```

## What went wrong, and how it was found

[`docs/bugs-found.md`](docs/bugs-found.md) records every defect found building
this, grouped by how it surfaced: by a data-quality check, by auditing an
assumption, by testing against real data, or before it could do damage. It also
lists the known limitations that are not defects.

Almost none of these raised an error. The rows inserted, the foreign keys
resolved, the tests passed, and the numbers were quietly wrong.

## What is and is not in this repository

Committed: all code, tests and documentation, the generated cohort definitions,
one complete example patient (`data/fhir/example/`) and a set of deliberately
awkward resources that pin edge-case behaviour (`data/fhir/fixtures/`).

Not committed, and why:

| | Why not |
|---|---|
| Synthea bulk data, 1.3 GB | Freely re-downloadable, see above |
| Athena vocabulary, 3.2 GB | Licensed. SNOMED in particular cannot be redistributed; request your own under your own account |
| `data/omop/*.duckdb`, 5.8 GB | Generated. Two commands rebuild them |
| `.venv`, `results/` | Generated |

Nothing needed to reproduce the results is missing; everything above is either
a download or a command.

## Scope

This is a learning project. It runs on one machine against one synthetic
dataset, has no incremental loading, and has never met a real EHR's quirks.
It is not a production ETL.

It does have a complete audit trail. Every mapping decision is documented where
it is made, every defect found is recorded along with how it surfaced, and both
a hand-written and the community-standard check suite run over the output.

## Documentation

- [`docs/design-decisions.md`](docs/design-decisions.md) — why the mappings are
  shaped the way they are
- [`docs/bugs-found.md`](docs/bugs-found.md) — every defect, and how it surfaced
- [`docs/manual.md`](docs/manual.md) — operating the pipeline: every file, every
  command, how to read the output, how to extend it, troubleshooting
- [`docs/course.md`](docs/course.md) — the underlying standards: what FHIR and
  OMOP each are, how the vocabulary works, why any of it matters
- [`docs/upstream-issue-duckdb-icu-not-loaded.md`](docs/upstream-issue-duckdb-icu-not-loaded.md)
  — a DatabaseConnector defect found while running the quality checks, reported
  upstream

## License

MIT — see [LICENSE](LICENSE).
