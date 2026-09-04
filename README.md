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

Not mapped, deliberately: `Claim` and `ExplanationOfBenefit` (billing — health
economics, a project of its own), `DiagnosticReport` (a grouping resource whose
member observations are already mapped), and `CarePlan`, `CareTeam`, `Goal`,
`ImagingStudy` (no home in CDM 5.4 core).

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
python scripts/run_etl.py --fhir data/fhir/example
pytest
```

That runs against one committed sample patient and needs no vocabulary
download. Output lands in `data/omop/omop.duckdb`, readable by any DuckDB
client.

For a real run, load a vocabulary first and point the ETL at bulk data:

```bash
python scripts/load_athena_vocab.py --zip ~/Downloads/vocabulary_download.zip
python scripts/run_etl.py --fhir data/fhir/bulk --vocab data/omop/vocab.duckdb
```

Optional, and requiring R:

```bash
Rscript scripts/run_dqd.R          # OHDSI Data Quality Dashboard, 2,533 checks
Rscript scripts/build_cohorts.R    # define and generate cohorts
```

## Vocabulary

The ETL needs an OMOP `concept` table to resolve codes against. The full
vocabulary is distributed by [OHDSI Athena](https://athena.ohdsi.org/), which
requires a free account. Select SNOMED, LOINC, RxNorm, RxNorm Extension, ATC,
CVX, ICD10, OMOP Extension, Gender, Race and Ethnicity.

`load_athena_vocab.py --merge` tops up an existing vocabulary with one you
missed, rather than rebuilding several gigabytes.

Without a vocabulary the ETL still runs; codes resolve to `concept_id 0` and
the unmapped rates report it.

## What went wrong, and how it was found

[`docs/bugs-found.md`](docs/bugs-found.md) records every defect found building
this, grouped by how it surfaced — by a data-quality check, by auditing an
assumption, by testing against real data, or before it could do damage. It also
lists the known limitations that are *not* defects.

The pattern worth taking from it: almost none of these raised an error. The
rows inserted, the foreign keys resolved, the tests passed, and the numbers
were quietly wrong.

## Data

`data/fhir/example/` holds one patient with a complete clinical trail, used by
the quick start. `data/fhir/fixtures/` holds deliberately awkward resources
that pin edge-case behaviour. Bulk input under `data/fhir/bulk/` and the
generated databases are gitignored — fetch the
[Synthea FHIR R4 sample](https://synthetichealth.github.io/synthea-sample-data/)
to reproduce the figures above.

## Scope

This is a learning project, and worth being clear about what that means. It
runs on one machine against one synthetic dataset, has no incremental loading,
and has never met a real EHR's quirks. It is not a production ETL.

What it does have is a complete audit trail: every mapping decision documented
where it is made, every defect found recorded along with how it surfaced, and
both a hand-written and the community-standard check suite run over the output.

## Documentation

- [`docs/design-decisions.md`](docs/design-decisions.md) — why the mappings are
  shaped the way they are
- [`docs/bugs-found.md`](docs/bugs-found.md) — every defect, and how it surfaced
- [`docs/manual.html`](docs/manual.html) — operating the pipeline
- [`docs/course.html`](docs/course.html) — the underlying standards

## License

MIT — see [LICENSE](LICENSE).
