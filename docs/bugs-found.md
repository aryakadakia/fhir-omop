# Bugs found, and how

A record of every defect found building this pipeline: what it was, how it
surfaced, and what now stops it coming back.

It exists because of a pattern that turned out to be the most important thing
this project taught. **Almost none of these raised an error.** The rows
inserted, the foreign keys resolved, the tests passed, and the numbers were
quietly wrong. A pipeline like this is not made correct by the absence of
exceptions; it is made correct by checks that can fail and by testing against
data large enough to be honest.

Ordered by how they were found, because the *finding* is the transferable part.

---

## Found by a data-quality check

These are the ones that justify writing the check suite before trusting the
mapping.

### 1. Condition domain routing — 862 rows in the wrong table

`Normal pregnancy` (516 rows) and `Body mass index 30+ obesity` (346) arrived
as FHIR `Condition` resources and were written to `condition_occurrence`. Both
are **Observation-domain** concepts in OMOP and belong in `observation`.

OMOP picks the destination table from the concept's `domain_id`, not from the
resource type that carried it. Both are perfectly reasonable things to send as
a FHIR `Condition`; OMOP's classification is what the downstream tooling
assumes.

*Failure mode:* silent. Rows insert, keys resolve, no error. 516 rows added to
the numerator of every pregnancy-related query and to a denominator nobody
would think to check.

*Caught by:* `condition_concept_in_condition_domain`.
*Guarded by:* that check plus `TestDomainRouting` in `test_condition_mapping.py`.

### 2. LOINC Note- and Procedure-domain concepts — 102 rows

The same bug, one layer down, and invisible until the full vocabulary arrived.
`measurement.py` sent anything that was not Measurement-domain into
`observation`. Real LOINC also carries `Note` and `Procedure` concepts, which
belong in `note` and `procedure_occurrence` — tables this ETL does not
populate.

*Fix:* the fact and its source concept are kept; the standard concept is
refused. Never write a concept into a table its domain forbids.

*Caught by:* `observation_concept_in_observation_domain`, but only once the
4.25M-concept vocabulary was loaded. The 32k development subset contained no
Note-domain LOINC concepts at all.

*Guarded by:* that check plus `TestDomainRouting` in
`test_measurement_mapping.py`.

---

## Found by asking "do we actually handle that?"

### 3. Divergent reference resolution — two implementations, three unhandled forms

`condition.py` and `measurement.py` had each grown their own copy of FHIR
reference parsing. Between them they handled `urn:uuid:` and `Type/id` only.
These fell through and returned unusable strings:

```
Patient/1234/_history/2            → "1234/_history/2"
https://ex.org/fhir/Patient/1234   → the whole URL
#contained-1                       → "contained-1"  (a false lookup key)
```

All three are legal FHIR. The code worked perfectly against Synthea and would
have silently resolved nothing against a live Epic or Cerner server — the
classic bug that only appears against a different data source.

*Failure mode:* loud but wrong. Rows were rejected as orphans and reported, so
nothing corrupted — but valid data would have been discarded wholesale.

*Fix:* one implementation in `references.py`, covering relative, versioned,
absolute, `urn:uuid`, `urn:oid` and bare-id forms. Contained and
identifier-only references return `None` rather than a false key.

*Guarded by:* 18 tests in `test_references.py`, one per form.

### 4. Drug mapping had no domain guard

`Condition` and `Observation` both checked the resolved concept's domain;
`drug.py` did not. The table happened to be clean, by luck rather than design
— RxNorm does not resolve exclusively into the Drug domain.

*Guarded by:* `drug_concept_in_drug_domain` plus
`test_non_drug_domain_is_refused`.

### 5. Standard-concept conformance was checked on conditions only

`measurement`, `observation` and `drug_exposure` had no equivalent of
`condition_concept_is_standard`. A non-standard concept in any of them would
be invisible to every cohort definition without being an error.

*Guarded by:* `all_event_concepts_are_standard`, across all four event tables.

### 6. Four mapping modules had no unit tests

A coverage audit found `visit.py`, `drug.py`, `measurement.py` and
`observation_period.py` untested — 321,082 rows, the majority of the database,
including the module where bug 2 had just been fixed. That fix was guarded only
by an integration-level check.

*Fix:* 59 tests added across the four modules.

---

## Found by testing against real data

The 32k-concept development vocabulary was convenient and dishonest. Both of
these appeared within minutes of loading the real one.

### 7. Gender concepts written without verifying they exist

`map_gender` returned hardcoded `8507` / `8532`. `Gender` is a **separately
selectable** Athena vocabulary and was omitted from the first download, so all
1,180 person rows carried a dangling concept id.

*Fix:* the concept is verified when a connection is available. A missing
`Gender` vocabulary now degrades to `concept_id 0`, which shows up in the
unmapped rate, instead of breaking referential integrity where only a check
would reveal it.

*Guarded by:* `gender_concept_id_valid` plus `TestGenderVocabularyPresence`.

### 8. CVX absent from the vocabulary selection

15,013 immunizations mapped at 100% unmapped because the vocabulary contained
no CVX concepts. Found by scanning the corpus for every code system in use
rather than reasoning about which ones the pipeline consumed — the empirical
question rather than the remembered one.

*Fix:* CVX added to the bundle; all 15,013 now resolve.

---

## Found before they could do damage

### 9. Athena `concept_code` type inference

DuckDB infers column types from content. SNOMED codes are all digits, so a
SNOMED-heavy `CONCEPT.csv` can infer `concept_code` as `BIGINT` — and every
lookup, which passes a Python string, then matches nothing.

*Failure mode:* total and silent. Zero rows mapped, no error, on a pipeline
that otherwise appears to run correctly.

*Fix:* every column type declared explicitly per CDM 5.4, dates parsed with
`dateformat='%Y%m%d'`, and the loader aborts if `concept_code` lands as
anything but text.

### 10. Concept resolution had no cache, then had a stale one

Resolving a quarter of a million resources against ~1,000 distinct codes did
two SQL round-trips per row. Adding memoisation took the full corpus from
unbounded to under eight minutes — but a module-level cache would serve stale
resolutions if a second load used a different vocabulary.

*Fix:* `clear_caches()` on every load and in an autouse test fixture.

### 11. `--merge` did not exist

Topping up a vocabulary with a missed selection would have deleted and rebuilt
the output, discarding 4.25M concepts.

*Fix:* `--merge` anti-joins each table on its key. Verified: adds 7 concepts
to a full vocabulary, leaves SNOMED's 1,106,309 and `concept_ancestor`'s
38,994,976 rows untouched, idempotent on re-run, refuses to merge into a
database that does not exist.

---

## Repository hygiene

Not pipeline defects, but real and worth recording.

| Problem | Fix |
|---|---|
| 81 MB Synthea zip committed to history | Untracked, `.gitignore` widened to `*.zip`, history rewritten with `git filter-repo` (78 MB → 236 K) |
| Four `.DS_Store` files committed | Ignored and removed from history |
| `omop.duckdb.wal` tracked | `.gitignore` covered `*.duckdb` but not the WAL beside it |
| Commit message describing a diff it no longer had | Amended after the history rewrite |

---

## Known limitations — not bugs, and not fixed

Recorded so nobody mistakes them for defects or for correctness.

- **`race_concept_id` and `ethnicity_concept_id` are 100% unmapped, by
  decision.** CDM 5.4 marks both `NOT NULL`, but AU Core has no race extension
  and Australian data records Indigenous status under a model that does not
  map onto OMOP's race/ethnicity pair. Zero is the honest answer; inventing
  categories would produce breakdowns describing the ETL rather than the
  population.
- **`drug_exposure_end_date` equals its start.** Synthea supplies no dispense
  duration. This understates exposure and is why chronic medications form no
  long drug eras — simvastatin collapses 2,363 exposures into 2,360 eras,
  while chemotherapy, genuinely administered in clusters, collapses nearly 2:1.
  Era quality depends entirely on real `days_supply`.
- **`gap_days` overstates gaps when exposures overlap.** Computed as era span
  minus summed exposure length. Exact for this dataset, where every exposure is
  a single day.
- **Vaccines produce no drug eras.** CVX concepts are outside the RxNorm
  ingredient hierarchy, so 15,013 immunizations roll up to nothing. This is the
  standard OHDSI definition; `drug_era` covers a strict subset of
  `drug_exposure` and the counts will never reconcile.
- **`observation_period` is inferred from first-to-last event.** A floor, not a
  truth: it cannot see a patient who moved away years before their last visit.
  Recorded in the data as `period_type_concept_id = 44814724`.
