# Bugs found, and how

A record of every defect found building this pipeline: what it was, how it
surfaced, and what now stops it coming back.

**Almost none of these raised an error.** The rows inserted, the foreign keys
resolved, the tests passed, and the numbers were quietly wrong. A pipeline like
this is not made correct by the absence of exceptions. It is made correct by
checks that can fail, and by testing against data large enough to be honest.

They are ordered by how they were found, since the finding method is the part
that transfers.

---

## Found by a data-quality check

These justify writing the check suite before trusting the mapping.

### 1. Condition domain routing — 862 rows in the wrong table

`Normal pregnancy` (516 rows) and `Body mass index 30+ obesity` (346) arrived
as FHIR `Condition` resources and were written to `condition_occurrence`. Both
are **Observation-domain** concepts in OMOP and belong in `observation`.

OMOP picks the destination table from the concept's `domain_id`, not from the
resource type that carried it. Both are perfectly reasonable things to send as
a FHIR `Condition`; OMOP's classification is what the downstream tooling
assumes.

*Failure mode:* silent. Rows insert, keys resolve, no error is raised. 516 rows
were added to the numerator of every pregnancy-related query, and to a
denominator nobody would think to check.

*Caught by:* `condition_concept_in_condition_domain`.
*Guarded by:* that check plus `TestDomainRouting` in `test_condition_mapping.py`.

### 2. LOINC Note- and Procedure-domain concepts — 102 rows

The same bug, one layer down, and invisible until the full vocabulary arrived.
`measurement.py` sent anything that was not Measurement-domain into
`observation`. Real LOINC also carries `Note` and `Procedure` concepts, which
belong in `note` and `procedure_occurrence`, tables this ETL does not
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

All three are legal FHIR. The code worked correctly against Synthea and would
have silently resolved nothing against a live Epic or Cerner server. This is the
classic bug that only appears against a different data source.

*Failure mode:* loud but wrong. Rows were rejected as orphans and reported, so
nothing was corrupted, but valid data would have been discarded wholesale.

*Fix:* one implementation in `references.py`, covering relative, versioned,
absolute, `urn:uuid`, `urn:oid` and bare-id forms. Contained and
identifier-only references return `None` rather than a false key.

*Guarded by:* 18 tests in `test_references.py`, one per form.

### 4. Drug mapping had no domain guard

`Condition` and `Observation` both checked the resolved concept's domain;
`drug.py` did not. The table happened to be clean by luck rather than design, since RxNorm does
not resolve exclusively into the Drug domain.

*Guarded by:* `drug_concept_in_drug_domain` plus
`test_non_drug_domain_is_refused`.

### 5. Standard-concept conformance was checked on conditions only

`measurement`, `observation` and `drug_exposure` had no equivalent of
`condition_concept_is_standard`. A non-standard concept in any of them would
be invisible to every cohort definition without being an error.

*Guarded by:* `all_event_concepts_are_standard`, across all four event tables.

### 6. Four mapping modules had no unit tests

A coverage audit found `visit.py`, `drug.py`, `measurement.py` and
`observation_period.py` untested, 321,082 rows, the majority of the database,
including the module where bug 2 had just been fixed. That fix was guarded only
by an integration-level check.

*Fix:* 59 tests added across the four modules.

---

## Found by testing against real data

The 32k-concept development vocabulary was convenient and misleading. Both of
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
rather than reasoning about which ones the pipeline consumed, the empirical
question rather than the remembered one.

*Fix:* CVX added to the bundle; all 15,013 now resolve.

---

## Found before they could do damage

### 9. Athena `concept_code` type inference

DuckDB infers column types from content. SNOMED codes are all digits, so a
SNOMED-heavy `CONCEPT.csv` can infer `concept_code` as `BIGINT`, and every
lookup, which passes a Python string, then matches nothing.

*Failure mode:* total and silent. Zero rows mapped, no error, on a pipeline
that otherwise appears to run correctly.

*Fix:* every column type declared explicitly per CDM 5.4, dates parsed with
`dateformat='%Y%m%d'`, and the loader aborts if `concept_code` lands as
anything but text.

### 10. Concept resolution had no cache, then had a stale one

Resolving a quarter of a million resources against ~1,000 distinct codes did
two SQL round-trips per row. Adding memoisation took the full corpus from
unbounded to under eight minutes, but a module-level cache would serve stale
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

## Found by the Data Quality Dashboard

The standardised OHDSI check suite, run once the CDM schema was complete.
2,533 checks against our 33. It found four defects the hand-written suite
missed. All four sit in categories the hand-written checks did not cover at
all, rather than being cases the existing checks got wrong.

### 12. Three non-standard type concepts

```
38000177  Prescription written                    standard = NULL
38000280  Observation recorded from EHR           standard = NULL
44814724  Period covering healthcare encounters   standard = NULL
```

Every `*_concept_id` in the CDM was checked for standardness by
`all_event_concepts_are_standard`. No `*_type_concept_id` was. A non-standard
type concept is invisible to any analysis filtering on it, in exactly the way a
non-standard condition concept is invisible to a cohort definition, the
failure this project documented at length and then committed itself.

One was correct: `32817 EHR`, the only type concept that had been verified
against the vocabulary before use. The three taken from memory were all wrong.

*Fix:* `32838 EHR prescription`, `32817 EHR`, and `32882 Standard algorithm
from EHR`. The last is semantically better than what it replaced, since an
inferred observation period is algorithm-derived.

*Guarded by:* `all_type_concepts_are_standard`, across all six type fields.

### 13. `birth_datetime` left null despite a full source date

`Patient.birthDate` gives year, month and day; the mapping stored the parts and
set `birth_datetime` to null. OHDSI tooling that needs a birth datetime
coalesces a null to **June 1st of the birth year**:

```sql
COALESCE(CAST(BIRTH_DATETIME AS DATE),
         CAST(strptime(CONCAT(year_of_birth,'0601'),'%Y%m%d') AS DATE))
```

That manufactures false "event before birth" findings for anyone born in the
second half of a year, 48 of them here. The data was not wrong, it was
discarded, which forces every downstream consumer to guess.

*Fix:* populated at full year-month-day precision, still null at partial
precision, because a guessed birth datetime is worse than an absent one.

*Guarded by:* `birth_datetime_matches_birth_date_parts` plus
`TestBirthDatetime`.

### 14. One DQD check errors on DuckDB — an upstream issue, not ours

`plausibleValueHigh` on `CDM_SOURCE.CDM_RELEASE_DATE` fails to compile:

```
Binder Error: No function matches the given name and argument types '(DATE, INTERVAL)'
  cast((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) as date)
```

The `DATE + INTERVAL` overload is provided by DuckDB's **ICU extension**.
Verified by reproducing it both ways in a bare R DuckDB session: the
expression fails without ICU and succeeds once `LOAD icu` has run. The same
SQL works in the DuckDB CLI, which autoloads extensions.

DatabaseConnector `INSTALL`s ICU on connect but does not `LOAD` it, and LOAD
is per-session. One check in 2,533, on a metadata field, so not worth working
around locally, but it is a small, well-scoped upstream contribution if
anyone wants one.

### 15. The dashboard renders empty when given a relative path

`viewDqDashboard` stashes the JSON path in an environment variable and then
calls `shiny::runApp`, which changes the working directory to the package's own
app folder. The app reads the path afterwards, so a relative path resolves
against the package directory rather than yours.

The JSON parse then fails and the dashboard renders its navigation shell with
every panel empty, no error, no warning, nothing in the console.

Reproduced from a different working directory: relative fails with a JSON
lexical error, absolute returns all 2,533 rows.

*Fix:* the runner now prints an absolute path via `normalizePath`.

### 16. A path bug in the DQD runner itself

`csvFile` is resolved relative to `outputFolder`, so passing a full path
produced `results/dqd/results/dqd/...` and the CSV write failed, with a
warning, not an error.

---

## What the Data Quality Dashboard flagged that was NOT a defect

Recorded because reading a check suite's output critically is the skill, rather
than obeying it.

- **Six `plausibleValueLow` date failures.** DQD's default threshold is
  `1950-01-01`. Synthea generates full lifetimes, so patients born in the
  1910s–40s legitimately have conditions before then. A threshold mismatch, not
  bad data.
- **`measurePersonCompleteness` on `procedure_occurrence`.** Vacuous: the table
  is deliberately empty because this ETL does not map `Procedure` yet.
- **`standardConceptRecordCompleteness` on `unit_concept_id`.** 100% of
  observations have no unit because qualitative observations do not have units.

---

## Found by pointing it at a live FHIR server

Everything above was found against files on disk. Connecting to a real server
(SMART Health IT sandbox, Smile CDR 2019.08, FHIR 4.0.0) surfaced a different
class of problem in minutes.

### 17. Relative references confirmed working

`references.py` handles six reference forms, but Synthea files only ever emit
`urn:uuid:`. The other five branches were written from the specification and had
never met real data.

A live server returns the relative form:

```
Synthea file:  "urn:uuid:5cbc121b-cd71-4428-b8b7-31e53eba8184"
Live server:   "Patient/1a96f2e7-7ce0-4164-b3f6-4fdffeca00fb"
```

Verbatim strings from the server resolve correctly, including
`Encounter.participant.individual` and `Encounter.serviceProvider`. Not a
defect, recorded because an untested branch that turns out to work is worth
knowing about as much as one that does not.

### 18. Half a patient loaded, and every check passed

`Patient/$everything` returns a paginated Bundle. The loader reads one file and
stops at the first page.

```
server has:  78 Encounters · 207 Observations
loaded:      35 Encounters · 104 Observations
rejected: 0        36/36 checks: PASS
```

Roughly half the record, silently, with a completely clean report.

This is the most complete illustration of the pattern running through this
whole document. Every check here verifies that the rows present are well
formed. **Nothing verifies that absent rows should have been present.** A
quality suite cannot detect missing data, only malformed data, and no amount of
adding checks changes that.

### 19. Referenced resources are not in the bundle

The same pull produced zero `provider` and zero `care_site` rows, and every
`visit_occurrence.provider_id` was null, even though the mapping handles both
correctly when the resources are present.

`$everything` returns the patient's clinical data but not the Practitioners and
Organizations that data references. The Encounters carry
`Practitioner/79a13b7d-...` and `Organization/b0e04623-...`; neither resource is
in the bundle.

### What 18 and 19 actually mean, and what was done about them

Neither is an ETL defect. Both say the same thing: the ETL was complete and the
fetcher did not exist. Reading FHIR from disk and reading it from a server are
different problems, and only the first was solved.

`scripts/fetch_fhir.py` closes both. Same patient, before and after:

```
                  $everything   fetcher   server reports
Encounter                  35        78               78
Observation               104       207              207
Procedure                  38        70               70
MedicationRequest          47       102              102
provider rows               0         1
care_site rows              0         1
visits with a provider      0        77 of 78
```

Three things make the difference.

**It verifies rather than assumes.** A type search returns `total`, so the
fetcher compares what it received against what the server says exists and
reports a warning per resource type when they differ. The whole failure being
guarded against is a partial pull that looks complete, so completeness is
asserted rather than hoped for.

**It does not use `$everything`.** That operation's paging is unreliable: this
sandbox returns 503 on its own `next` links while ordinary type-search paging
works on the same server. Per-type search also yields `total`, which
`$everything` does not.

**It fetches what the data references.** A second pass retrieves the
Practitioners and Organizations the clinical resources point at but the search
results omit.

### 20. The fetcher's first version lost everything on a transient failure

Written to raise on any HTTP error, it discarded 200 already-fetched resources
when page two returned 503. Now it retries with backoff, and on persistent
failure keeps what it has and reports the pull as incomplete. Both halves
matter: discarding good data is wasteful, and keeping it silently would
recreate exactly the problem in finding 18.

---

## Found by bulk export

Per-patient fetching is what an application does. Building a CDM needs the
opposite: everybody, once. That is a different protocol (FHIR Bulk Data), and
it produces a differently shaped payload -- newline-delimited JSON split by
resource type rather than one bundle per patient. Pointing the ETL at that
found the most serious structural defect in the project.

### 21. Every clinical row rejected, and the run reported success

A bulk export of 100 patients produced:

```
Encounters read            3,697        visit_occurrence written      0
Conditions read              639        condition_occurrence written  0
MedicationRequests read    2,386        drug_exposure written         0
rejected: 23,075                        checks passing: 35 of 36
```

The cause was structural. Passes 1, 2 and 3 were nested inside a single loop
over files:

```python
for path in files:
    doc = load(path)
    for patient in resources(doc, "Patient"):    ...   # pass 1
    for encounter in resources(doc, "Encounter"): ...  # pass 2
```

That quietly assumes **each file is self-contained**. It held perfectly for
Synthea, where every file is one patient's complete bundle, so a `Patient` is
always read before the `Encounter` that references it. A bulk export splits
resources by TYPE, so `Condition.ndjson` and `Encounter.ndjson` are processed
before `Patient.ndjson` exists, against an empty index.

*Failure mode:* loud but ignorable. Rejections were counted and printed, so
nothing was corrupted or hidden. But no check FAILED -- a quality suite
inspects the rows that exist and has nothing to say about a table being empty.
A run that rejects 23,075 of 23,707 resources should not be able to report
"OK".

*Fix:* each pass now loops over every file before the next begins. That is
correct regardless of source, and it makes the ETL genuinely format-agnostic
rather than accidentally compatible with one dataset's shape. 23,075
rejections became 0.

### 22. One unsupported type rejects the entire export

Bulk export is all-or-nothing on type validity. Requesting twelve resource
types from a server that lacks `MedicationAdministration` rejected the whole
request, and the error named only the first offender -- so the types a server
lacks are discovered one round-trip at a time.

*Fix:* read the `CapabilityStatement` first and request only what the server
declares. The same "ask before assuming" habit the course recommends, here
enforced by a hard failure rather than good manners.

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

Recorded so nobody mistakes them for defects, or for correctness.

- **`race_concept_id` and `ethnicity_concept_id` are 100% unmapped, by
  decision.** CDM 5.4 marks both `NOT NULL`, but AU Core has no race extension
  and Australian data records Indigenous status under a model that does not
  map onto OMOP's race/ethnicity pair. Zero is the honest answer; inventing
  categories would produce breakdowns describing the ETL rather than the
  population.
- **`drug_exposure_end_date` equals its start.** Synthea supplies no dispense
  duration. This understates exposure and is why chronic medications form no
  long drug eras, simvastatin collapses 2,363 exposures into 2,360 eras,
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
