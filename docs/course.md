# The FHIR–OMOP Bridge

Two standards govern clinical data. One moves it between systems; the other makes it answerable. This is how each works, why both exist, and what breaks when you connect them, taught against a pipeline that loaded 414,000 real FHIR resources.

*Companion to [`manual.md`](manual.md), which covers operating the pipeline, and [`bugs-found.md`](bugs-found.md), which records every defect found building it.*

---

## Modules

1.  [00 · What we are building, and why](#m0)
2.  [01 · Why two standards exist at all](#m1)
3.  [02 · FHIR: resources and how they point at each other](#m2)
4.  [03 · The FHIR resources worth knowing](#m2b)
5.  [04 · What a concept actually is](#m3)
6.  [05 · Relationships and hierarchy](#m3b)
7.  [06 · Surrogate keys and the index](#m4)
8.  [07 · The four rules of the bridge](#m5)
9.  [08 · Rates: numerator, denominator, observation_period](#m6)
10. [09 · Using it: defining a cohort](#m7)
11. [10 · What FHIR is for, beyond our ETL](#m8)
12. [11 · What OMOP is for, beyond our ETL](#m9)
13. [12 · Profiles, AU Core, and Australia](#m10)
14. [▤ · Where to look things up](#refs)
15. [△ · Glossary](#gloss)

## What we are building, and why

The question that should have come first.

Start with a concrete problem. A hospital has a database of patients. You want to answer: *how many of our diabetic patients over 60 were prescribed this drug, and what happened to them afterwards?*

You cannot ask that question of the hospital's system directly. Its data comes out in **FHIR** — a format built for handing one patient's record to one application at a time. Asking FHIR a population question means downloading every patient individually and doing the analysis yourself, from scratch, in a shape nobody else can reuse.

So the field standardised a second format, **OMOP CDM**, built purely for asking population questions. Same clinical facts, completely different shape.

The gap between those two formats is a real, permanent piece of infrastructure. Somebody has to build the converter. That converter is what this project is.

### The specific thing we are doing

input

#### 1,180 FHIR bundles

Synthetic patients from Synthea. Each is a JSON file holding one person's entire simulated lifetime — roughly 280 resources each, 331,000 in total.

process

#### The ETL — extract, transform, load

Read each bundle, decide what each resource becomes in OMOP, translate its codes, and write the rows. This is the code we wrote.

output

#### An OMOP CDM database

Six populated tables, 332,000 rows, every one traceable back to the FHIR resource that produced it.

result

#### Questions you can now ask

Prevalence, cohorts, comparisons — in SQL that would run unchanged against any OMOP database in the world.

### Which standard is doing what, here

-   **FHIR is the source format.** We only *read* it. Every design decision on the input side is about correctly interpreting what a FHIR resource means.
-   **OMOP is the target format.** We only *write* it. Every decision on the output side is about obeying the CDM's rules so that standard tools can read our result.
-   **The vocabulary sits between them**, translating codes from the source's terminology into OMOP's integer keyspace. It is the largest single piece of machinery in the whole thing.

### Why build this to learn, rather than just read about it

Because the two models disagree, and every disagreement forces a decision you must make consciously. FHIR says "this is a Condition"; OMOP says "that concept belongs in the observation table." FHIR permits a birth date of just a year; OMOP requires a year and permits null month. Reading a specification lets you nod past these. Writing the converter does not — you must resolve every one, and that is what turns knowledge into competence.

**The thesis of this course**

Almost every way this pipeline can be wrong produces **no error at all**. The rows insert. Foreign keys resolve. Tests pass. The numbers are simply wrong, quietly, forever.

That is why the data-quality suite exists, and why it is written *before* trusting a mapping rather than after.

## Why two standards exist at all

They are not competitors. Neither can do the other's job.

**FHIR answers: "give me this patient's data, now."** Built by HL7 for *exchange* — an app asking a hospital's server for one person's medications so it can render a screen. A REST API returning JSON. Optimised for one patient, in real time, over a network.

**OMOP answers: "how many people like this, across all our hospitals?"** Built by OHDSI for *analysis* — asking whether a drug is associated with an outcome across a hundred million records in twenty countries. A relational schema you write SQL against. Optimised for populations, retrospectively, on a warehouse.

#### FHIR

Body  
HL7 International

Shape  
JSON resources over REST

Unit of work  
One patient, one request

Timing  
Live, transactional

Terminology  
Whatever the sender uses

You build  
Apps, integrations, portals

#### OMOP CDM

Body  
OHDSI

Shape  
~40 relational tables

Unit of work  
A whole population

Timing  
Retrospective, batch

Terminology  
One normalised vocabulary

You build  
Studies, cohorts, evidence

### The consequence for a career

A digital health product almost always needs both. FHIR is how your software gets data out of an EHR and earns a place inside a hospital's systems. OMOP is how you demonstrate afterwards that the thing works — the evidence a regulator, a payer, or a journal will ask for. People who move between the two are rare, because the two communities barely overlap.

## FHIR: resources and how they point at each other

References in full, because everything downstream depends on them.

FHIR defines about 150 **resource types**. Each is a JSON object with a `resourceType` and an `id`. That is the whole model. The crucial consequence: **nothing contains anything**. There is no patient object holding a list of conditions — there is one `Patient` resource, and separate `Condition` resources that point at it.

### How a reference actually works

A reference is a JSON object with a `reference` string inside it. That string is a pointer. There are three forms you will meet, and they are not interchangeable:

the three forms of Reference.reference

    1. RELATIVE — the common form from a live server.
       "ResourceType/id". Resolve against the server's base URL.
    "subject": { "reference": "Patient/1234" }
       → fetch https://fhir.hospital.org/r4/Patient/1234

    2. ABSOLUTE — a full URL, possibly on a different server.
    "subject": { "reference": "https://other.org/fhir/Patient/1234" }

    3. URN / UUID — an INTRA-BUNDLE pointer. Not fetchable.
       It matches the `fullUrl` of another entry in the same Bundle.
    "subject": { "reference": "urn:uuid:5cbc121b-cd71-4428-b8b7-31e53eba8184" }

Synthea uses form 3, and this is worth understanding precisely. A Bundle is a list of `entry` objects. Each entry has a `fullUrl` and a `resource`. The `fullUrl` is the entry's address *within this bundle*:

how the pointer and the target line up

    {
      "resourceType": "Bundle",
      "entry": [
        {
          "fullUrl": "urn:uuid:5cbc121b-cd71-4428-b8b7-31e53eba8184",   ← the address
          "resource": { "resourceType": "Patient",
                        "id": "5cbc121b-cd71-4428-b8b7-31e53eba8184" }   ← same value
        },
        {
          "fullUrl": "urn:uuid:4e3be31c-bb2c-479c-b855-23e3103e42d5",
          "resource": { "resourceType": "Condition",
                        "code": { ... "Cardiac Arrest" ... },
                        "subject": { "reference":
                           "urn:uuid:5cbc121b-cd71-4428-b8b7-31e53eba8184" }  ← points back
          }
        }
      ]
    }

So resolving a reference is a *string match*, not a network call. You strip the `urn:uuid:` prefix and look for a resource with that `id`. There is no magic — and no validation. If the target is missing, nothing tells you; you just get a pointer into nowhere.

#### Which direction do references point?

Almost always **from the specific to the general**. A `Condition` points at its `Patient`; a `Patient` does not list its conditions. To find a patient's conditions on a live server you *search*: `GET /Condition?patient=1234`. The reference is stored on the many side, exactly like a foreign key in a relational database.

**Fails silently**

Assume one reference form and your code works perfectly against Synthea, then returns zero rows against a real Epic server — or the reverse. Both forms are legal. Handle both, or you will ship something that works only against the data you tested with.

### CodeableConcept: a code is meaningless alone

Clinical meaning travels in a `CodeableConcept`:

Condition.code

    "code": {
      "coding": [ {
        "system":  "http://snomed.info/sct",     which dictionary
        "code":    "410429000",                  the entry in it
        "display": "Cardiac Arrest"                a label — NOT authoritative
      } ],
      "text": "Cardiac Arrest"                     free text, human-entered
    }

`coding` is an *array* because the same idea can be sent in several code systems at once — SNOMED and ICD-10 and a local code, all describing one thing. `system` is a URI naming the dictionary. Never match on `display`: it is a convenience label the sender chose, and two systems will spell the same concept differently.

## The FHIR resources worth knowing

There are ~150. You need perhaps twenty.

Resources are grouped by what they describe. These are the ones that carry real weight in practice.

### The clinical core — what you map first

| Resource           | What it holds                                                     | OMOP target               |
|--------------------|-------------------------------------------------------------------|---------------------------|
| Patient            | Demographics. Exactly one per person.                             | person                    |
| Condition          | Diagnoses, problems, health concerns.                             | condition_occurrence     |
| Observation        | Labs, vitals, smoking status, survey answers. The biggest by far. | measurement / observation |
| Encounter          | A visit or admission. The spine other facts hang from.            | visit_occurrence         |
| MedicationRequest  | A prescription order.                                             | drug_exposure            |
| Procedure          | Something done to the patient.                                    | procedure_occurrence     |
| AllergyIntolerance | Allergies and reactions.                                          | observation               |
| Immunization       | Vaccines given.                                                   | drug_exposure            |

### The medication family — a common trap

FHIR splits medication into several resources by *what stage of the process* it represents. Getting this wrong misstates exposure badly:

-   **`MedicationRequest`** — a prescription was *written*. Says nothing about whether it was filled or taken.
-   **`MedicationDispense`** — a pharmacy *handed it over*. Stronger evidence.
-   **`MedicationAdministration`** — someone actually *gave* it, typically inpatient. Strongest.
-   **`MedicationStatement`** — a *report* that the patient is taking something, often self-reported. This is where over-the-counter and outside-prescriber drugs live, and a med list without it is incomplete.

### Documents and reports

-   **`DocumentReference`** — a pointer to a clinical note or PDF, with the content base64-encoded or behind a URL. This is where free text lives, and therefore where most of the clinically interesting detail hides.
-   **`DiagnosticReport`** — a grouping resource: a lab panel or radiology report bundling several `Observation`s plus a narrative conclusion.

### Administrative and financial

-   **`Practitioner`, `PractitionerRole`, `Organization`, `Location`** — the who and where. Map to `provider` and `care_site`.
-   **`Coverage`, `Claim`, `ExplanationOfBenefit`** — insurance and billing. Central to health-economics work; irrelevant to most clinical questions.
-   **`Appointment`, `Schedule`, `ServiceRequest`** — scheduling and orders.

### Infrastructure resources — not patient data at all

These describe the *system* rather than any person, and they are the ones people skip and later regret:

-   **`CapabilityStatement`** — what this server actually supports. Fetched from `/metadata`. Every vendor implements a different subset, so this is the first call you should make against any new endpoint.
-   **`StructureDefinition`** — the definition of a resource or a profile. This is what an implementation guide like AU Core is *made of*.
-   **`ValueSet` and `CodeSystem`** — the terminology machinery: which codes are permitted in a given field.
-   **`OperationOutcome`** — how FHIR returns errors.
-   **`Bundle`** — the envelope. Search results, transactions, and Synthea's per-patient files are all Bundles.

## What a concept actually is

The single most important idea in OMOP, taken slowly.

### The problem being solved

Consider the medical idea *high blood pressure that has no identifiable underlying cause*. Doctors call it essential hypertension. Now consider how different systems write it down:

one idea, many codes

    SNOMED CT     59621000       "Essential hypertension"
    ICD-10        I10            "Essential (primary) hypertension"
    ICD-9-CM      401.9          "Unspecified essential hypertension"
    Read v2       G20..          "Essential hypertension"
    some hospital HTN-1          "HTN, essential"

Five codes. One idea. If your database stores whatever code each hospital sent, you cannot count patients across hospitals — you would have to know all five, and next year there will be a sixth.

### The solution: an integer for the idea itself

OMOP creates a separate identifier for the *idea*, independent of any coding system. That identifier is a **concept_id**: an arbitrary integer, meaningful only within OMOP.

Essential hypertension is **`320128`**. Every one of those five source codes points at it. Store `320128` and your query works everywhere.

A "clinical idea" here just means a distinct thing you might want to count: a disease, a lab test, a drug ingredient, a procedure, a unit of measure, even "male". Anything that can appear in a coded field gets a concept.

### Anatomy of a real concept row

The `concept` table is one enormous dictionary. Here is an actual row from our database, field by field:

**concept_id** — **320128** — OMOP's own integer. This is what you store in your data.

**concept_name** — **Essential hypertension** — the human-readable label.

**domain_id** — **Condition** — which CDM table facts using this concept belong in.

**vocabulary_id** — **SNOMED** — which source dictionary this concept came from.

**concept_class_id** — **Disorder** — the vocabulary's own internal category.

**standard_concept** — **S** — this is the canonical concept for the idea. See below.

**concept_code** — **59621000** — the code as it exists in SNOMED. This is the bridge back to the source.

**valid_start_date  
valid_end_date** — **2002-01-31** to **2099-12-31** — when this concept is in force. A far-future end date means "still current".

**invalid_reason** — **NULL** — still valid. Non-null means deprecated.

### How a code turns into a concept_id

It is a lookup, and there is nothing clever about it. You have a code and you know which dictionary it came from. You search the `concept` table for that pair:

step 1 — the lookup

    SELECT concept_id, standard_concept, domain_id
    FROM   concept
    WHERE  concept_code   = '59621000'     from FHIR Coding.code
      AND  vocabulary_id  = 'SNOMED';      translated from Coding.system

    → 320128 | S | Condition

You need *both* halves. `concept_code` alone is ambiguous — different vocabularies reuse the same strings. The `vocabulary_id` comes from translating the FHIR `system` URI (`http://snomed.info/sct` → `SNOMED`), which is a small hard-coded table in our pipeline.

### "Designating canonical" — what standard_concept means

Because concepts are imported from many vocabularies, OMOP ends up holding several concepts that mean roughly the same thing — one from SNOMED, one from ICD-10, and so on. It would be chaos if analysts each picked a different one.

So OMOP nominates **one concept per idea as the standard** — marked `standard_concept = 'S'`. Everything else is non-standard and exists only to be translated *from*.

| standard_concept | Means                                                       | May you store it?                             |
|-------------------|-------------------------------------------------------------|-----------------------------------------------|
| 'S'               | Standard. The canonical concept for this idea.              | Yes — this is what belongs in `*_concept_id`. |
| 'C'               | Classification. A grouping concept, e.g. an ATC drug class. | Not in event tables; useful for rolling up.   |
| NULL              | Non-standard. A source code awaiting translation.           | Only in `*_source_concept_id`.                |

The convention that follows: OMOP stores **two** concept ids for every coded fact.

why both are kept

    condition_concept_id         320128      the STANDARD concept — analyse on this
    condition_source_concept_id  320128      the concept for the code as RECEIVED
    condition_source_value       '59621000'  the raw string that arrived

Here all three agree, because the incoming code was already standard. When it is not, the first two differ — and that difference is the entire subject of the next module.

## Relationships and hierarchy

`Maps to`, ancestors, descendants — the two tables that connect concepts.

Concepts alone are just a dictionary. Two further tables connect them, and they do different jobs.

### concept_relationship: a table of edges

This is simply a list of triples — *this concept, that concept, and how they relate*. Nothing more:

a real row from our database

    concept_id_1 │ concept_id_2 │ relationship_id │ valid_start │ valid_end
    ─────────────┼──────────────┼─────────────────┼─────────────┼───────────
        40316773 │      4311629 │ Maps to         │ 1970-01-01  │ 2099-12-31

Read it as a sentence: *concept 40316773 **Maps to** concept 4311629.* The `relationship_id` is just a label saying what kind of edge this is. Our vocabulary subset holds several kinds:

| relationship_id | Meaning                                                     | Rows  |
|------------------|-------------------------------------------------------------|-------|
| Maps to          | "When you see 1, use 2 for analysis." The translation edge. | 2,524 |
| Mapped from      | The same edge reversed. Every relationship has an inverse.  | 2,524 |
| Is a             | "1 is a kind of 2." The hierarchy edge — one step only.     | 2,973 |
| Subsumes         | The inverse of `Is a`.                                      | 2,973 |

### Why "Prediabetes" needs mapping — the actual reason

Look at the concept row for 40316773 and the cause is visible:

**concept_id** — **40316773**

**concept_name** — **Prediabetes**

**standard_concept** — **NULL** — not standard

**valid_end_date** — **2002-01-31** — this concept stopped being valid in 2002

**invalid_reason** — **U** — "Upgraded". SNOMED retired this code and replaced it.

SNOMED deprecated this code decades ago. Synthea still emits it, as real systems routinely do — clinical software is full of codes that were current when the software was written. OMOP keeps the dead concept in the dictionary *precisely so that old data can still be translated*, and records where it went with a `Maps to` edge.

step 2 — following the edge

    SELECT cr.concept_id_2
    FROM   concept_relationship cr
    JOIN   concept c ON c.concept_id = cr.concept_id_2
    WHERE  cr.concept_id_1    = 40316773          what we received
      AND  cr.relationship_id = 'Maps to'
      AND  c.standard_concept = 'S';              insist the target is standard

    → 4311629  "Impaired glucose tolerance"

So the full resolution is: code → concept → (if not standard) → standard concept. Two lookups. In our corpus this affects 388 patients.

**Fails silently**

Store `40316773` and nothing breaks. The row inserts, foreign keys resolve, checks pass. But every cohort definition in the OHDSI world searches for standard concepts, so those 388 patients are not *rejected* — they are **invisible**. You will never see an error, only a number that is too small.

### Where do the mappings come from?

A natural question: who decided that `Prediabetes` becomes `Impaired glucose tolerance`? **Not you.** This is the single most important thing to understand about the vocabulary — you never author mappings, you receive them.

They arrive from two places:

-   **The source vocabularies themselves.** When SNOMED retires a code it publishes what replaces it. When a code is deprecated, its successor is stated by the people who deprecated it. OMOP ingests these as `Maps to` edges — which is exactly why our Prediabetes row carries `invalid_reason = 'U'` and a 2002 end date.
-   **The OHDSI vocabulary team.** Cross-vocabulary mappings — ICD-10 to SNOMED, one drug terminology to another — are curated centrally, partly derived from UMLS, partly by hand, and versioned with each release.

**Athena is how that work is distributed.** Downloading the vocabulary is downloading years of other people's terminology curation. That is what makes OMOP worth the trouble, and why "just store the source codes" is not a shortcut — it is declining the mapping work rather than avoiding it.

**Two mechanisms, often confused**

`Maps to` handles **the same idea written differently**. SNOMED `59621000`, ICD-10 `I10` and a retired code all mean essential hypertension, so all three `Maps to` concept `320128`. Equivalence.

`concept_ancestor` handles **different ideas at different levels of specificity**. Pre-eclampsia is *not* the same as hypertensive disorder — it is a kind of it. Subsumption.

You need both, for different jobs. `Maps to` runs at ETL time, normalising what arrives. `concept_ancestor` runs at analysis time, letting you ask for a family of concepts at once.

### concept_ancestor: the hierarchy, precomputed

SNOMED is not a flat list. It is a hierarchy of *is-a* relationships: essential hypertension *is a* hypertensive disorder, which *is a* cardiovascular finding, and so on up to "Disease". Here is the real chain from our database:

everything ABOVE Essential hypertension (320128)

    lvl │ concept_id │ concept_name
    ────┼────────────┼──────────────────────────────────
      0 │     320128 │ Essential hypertension          ← itself
      1 │     316866 │ Hypertensive disorder
      2 │    4023995 │ Cardiovascular finding
      2 │     134057 │ Disorder of cardiovascular system
      3 │     441840 │ Clinical finding
      3 │    4180628 │ Disorder of body system
      4 │    4274025 │ Disease                         ← the top

The vocabulary is:

-   **Ancestor** — a concept *above* another. More general. "Hypertensive disorder" is an ancestor of "Essential hypertension".
-   **Descendant** — a concept *below* another. More specific. The reverse relation.
-   **`min_levels_of_separation`** — how many *is-a* steps apart they are. Level 0 means the concept and itself: **every concept is its own ancestor and descendant**, which is why a query for descendants includes the concept you asked about.

Note two concepts sit at level 2. A concept can have several parents — this is a directed graph, not a tree. That is normal in SNOMED and nothing to worry about.

#### Why a whole table for something derivable

`concept_relationship` already holds `Is a` edges, so in principle you could walk them recursively. In practice that is far too slow at analysis time, so OMOP ships `concept_ancestor` as a **precomputed transitive closure** — every ancestor–descendant pair at every distance, worked out in advance. Our small subset already has 115,241 rows; the full vocabulary has hundreds of millions.

### What the hierarchy buys you

Going *downwards* is the payoff. Ask for everything beneath "Hypertensive disorder":

everything BELOW Hypertensive disorder (316866)

    lvl │ concept_name
    ────┼───────────────────────────────────────
      0 │ Hypertensive disorder
      1 │ Essential hypertension
      1 │ Hypertension in the obstetric context
      1 │ Maternal hypertension
      2 │ Pregnancy-induced hypertension
      3 │ Pre-eclampsia

the query that produced it

    SELECT d.concept_name
    FROM   concept_ancestor ca
    JOIN   concept d ON d.concept_id = ca.descendant_concept_id
    WHERE  ca.ancestor_concept_id = 316866;

One line gives you every form of hypertension in the vocabulary, including ones you had not thought of. Written by hand you would have missed pre-eclampsia. **This is what OMOP gives you that raw EHR data never can**, and it is the reason the vocabulary is worth its considerable weight.

**Try it**

Pick any concept and walk both directions. Change the id and see how the shape of medicine changes around it:

    -- what is this concept a kind of?
    SELECT ca.min_levels_of_separation lvl, a.concept_name
    FROM concept_ancestor ca JOIN concept a ON a.concept_id = ca.ancestor_concept_id
    WHERE ca.descendant_concept_id = 320128 ORDER BY lvl;

    -- what kinds of this concept exist?
    SELECT ca.min_levels_of_separation lvl, d.concept_name
    FROM concept_ancestor ca JOIN concept d ON d.concept_id = ca.descendant_concept_id
    WHERE ca.ancestor_concept_id = 320128 ORDER BY lvl;

Our development vocabulary is a 32k-concept subset, so many concepts have few descendants. With the full Athena release the same queries return hundreds.

## Surrogate keys and the index

What "building an index" means, and why the pipeline needs passes.

### Two different kinds of identifier

FHIR and OMOP identify a patient in incompatible ways, and understanding why explains the whole architecture.

#### FHIR Patient.id

Looks like  
`5cbc121b-cd71-4428-b8b7-31e53eba8184`

Type  
String — often a UUID

Assigned by  
Whichever system created the resource

Scope  
Unique on that server

#### OMOP person_id

Looks like  
`1`

Type  
BIGINT — an integer

Assigned by  
*You*, during the ETL

Scope  
Unique in your CDM

The CDM specifies `person_id` as an integer, so a UUID cannot go there. There are also good reasons to want integers: joins across tables with hundreds of millions of rows are dramatically faster on integers, and if you later merge data from two hospitals their local ids may collide while your integers never will.

### What a surrogate key is

An identifier you invent, with no meaning outside your database, standing in for one whose form you cannot use. `person_id = 1` means nothing about the patient — it is simply the first person we loaded.

Inventing it creates an obligation: **never lose the original**. The CDM has a column for exactly this, and our pipeline also writes a provenance row:

real rows — the surrogate and its source

    person_id │ person_source_value
    ──────────┼──────────────────────────────────────
            1 │ 5cbc121b-cd71-4428-b8b7-31e53eba8184
            2 │ adccf2c3-9dc4-4067-ba23-98982c4875da
            3 │ 31191928-6acb-4d73-931c-e601cc3a13fa

Any row in the CDM can now be traced back to the exact FHIR resource that produced it — which is the difference between an auditable pipeline and an unusable one.

### What "building an index" means

Literally: a dictionary in memory, mapping the source identifier to the surrogate key you assigned.

the index, in full

    person_index = {
      "5cbc121b-cd71-4428-b8b7-31e53eba8184": 1,
      "adccf2c3-9dc4-4067-ba23-98982c4875da": 2,
      "31191928-6acb-4d73-931c-e601cc3a13fa": 3,
      ...
    }

That is the whole thing. One entry per patient, built as you write each `person` row.

### Why it resolves clinical resources

Now walk through what happens when a `Condition` arrives:

resolution, step by step

    1. The Condition tells you who it belongs to — as a FHIR reference.
       condition["subject"]["reference"]
       → "urn:uuid:5cbc121b-cd71-4428-b8b7-31e53eba8184"

    2. Strip the prefix to recover the Patient's id.
       → "5cbc121b-cd71-4428-b8b7-31e53eba8184"

    3. Look it up in the index.
       person_index["5cbc121b-..."]  →  1

    4. NOW you can write the row. person_id is NOT NULL —
       without step 3 there is nothing legal to put in it.
       INSERT INTO condition_occurrence (person_id, ...) VALUES (1, ...)

This is why the pipeline runs in passes. Step 3 can only work if the person already exists and is in the index. So every `Patient` must be processed before any `Condition`. The same applies to encounters, which is why there is a second index and a second pass:

pass 1

#### Patient → person

Write person rows. Build `person_index`: FHIR id → `person_id`.

pass 2

#### Encounter → visit_occurrence

Needs `person_index` (an encounter belongs to a patient). Builds `visit_index`: FHIR id → `visit_occurrence_id`.

pass 3

#### Conditions, observations, medications

Each needs *both* indexes — a patient and, usually, the visit it happened at.

pass 4

#### Derive observation_period

A function of every event date now loaded, so it must come last.

### What happens when resolution fails

A reference may point at something absent — a patient excluded earlier, or a genuinely broken bundle. That row is an **orphan**, and our pipeline refuses it, recording the reason.

**Fails silently**

The tempting shortcut is to invent a `person_id` rather than lose the row. That does not lose data — it attaches a real diagnosis to the *wrong patient*, permanently and undetectably. Rejecting the row and reporting it is the only defensible option.

## The four rules of the bridge

Everything that broke in our pipeline, and what each one teaches.

**Rule 1 — Build the reference index before loading anything.** Module 06 in full.

**Rule 2 — Translate the code system, then resolve to standard.** Modules 04 and 05 in full. Summarised:

resolution, end to end

    1. FHIR system URI → OMOP vocabulary_id
          "http://snomed.info/sct" → "SNOMED"

    2. (concept_code, vocabulary_id) → concept
          ("15777000", "SNOMED") → concept_id 40316773

    3. if standard_concept is not 'S', follow "Maps to"
          40316773 Prediabetes [deprecated 2002, invalid_reason='U']
            └─ Maps to → 4311629 Impaired glucose tolerance ✓

### Rule 3 — Route by domain, not by resource type

The rule that surprises everyone. A FHIR `Condition` does not necessarily become a `condition_occurrence`. The `domain_id` on the resolved concept decides the destination table:

| Arrived as  | Concept                     | domain_id      | Actually goes to      | Rows    |
|-------------|-----------------------------|-----------------|-----------------------|---------|
| Condition   | Essential hypertension      | Condition       | condition_occurrence | 330     |
| Condition   | Normal pregnancy            | **Observation** | observation           | 516     |
| Condition   | Body mass index 30+ obesity | **Observation** | observation           | 346     |
| Observation | Body Height (LOINC 8302-2)  | **Measurement** | measurement           | 208,513 |

#### Why OMOP "wins" when the two disagree

This is not a claim that OMOP's classification is more correct. "Normal pregnancy" is a perfectly sensible `Condition` in FHIR. It means something narrower and more practical:

Every downstream OHDSI tool — the ATLAS cohort builder, the HADES analysis packages, every published network study — is *written against the CDM conventions*. When such a tool looks for pregnancy it queries the `observation` table, because that is where the CDM says Observation-domain concepts live. If you put those 516 rows in `condition_occurrence` instead, the tools do not error; they look in the right place by their rules and find nothing.

"The ecosystem assumes it" means: the value of using a common data model comes entirely from other people's code being able to read your data. Deviate from the convention and you keep the schema but lose the reason you adopted it.

**Fails silently**

Load "Normal pregnancy" into `condition_occurrence` and the insert succeeds, foreign keys resolve, and no error is raised. You have added 516 rows to the numerator of every pregnancy-related query and to a denominator you will never think to check.

### Rule 4 — Unmapped means zero, never a guess

OMOP reserves `concept_id = 0` for "No matching concept". Using it is correct and spec-compliant. Guessing a plausible id is how a dataset becomes quietly, permanently wrong.

**0.0%** — measurement concepts unmapped

**35.6%** — condition concepts unmapped

**67.6%** — drug concepts unmapped

**100%** — race / ethnicity — by design

Those middling rates are a *vocabulary* limitation, not a logic error: our development vocabulary is a 31,976-concept Synthea subset. The full Athena release would resolve most of them. The race and ethnicity figure is different and deliberate — CDM 5.4 marks both `NOT NULL`, yet AU Core has no race extension at all, and Australian data records Indigenous status under a model that does not map onto OMOP's race/ethnicity pair. Zero is the honest answer.

Three of these four failure modes raise no error, fail no constraint, and survive code review. That is why the data-quality suite exists — and why the domain-routing bug in this project was caught by a check rather than by reading the specification.

## Rates: numerator, denominator, `observation_period`

Why the table with no FHIR counterpart is the one that matters most.

### The arithmetic, plainly

A **rate** is one number divided by another.

-   The **numerator** is the top: how many had the thing.
-   The **denominator** is the bottom: how many *could* have had it.

prevalence of hypertension in our data

                  people with a hypertension diagnosis        330
    prevalence =  ────────────────────────────────────  =  ─────  =  28.0%
                  people who could have been diagnosed      1,180

The numerator is easy — count rows in `condition_occurrence`. **The denominator is the hard part, and it is a judgement, not a fact.**

### Why the denominator is not just "everyone"

Someone who appears in your data for a single day was never realistically going to be diagnosed with anything. Their doctor had no opportunity. Counting them in the denominator makes the rate look lower than it truly is — you have added someone to the bottom of the fraction who could never have been added to the top.

Real EHR data is full of such people: one emergency visit, never seen again. This is not an edge case; in some datasets it is a large minority of rows.

### What observation_period is

One row per person, recording the window during which they were *observable* — during which, had something happened to them, your data would have recorded it.

**Nothing in FHIR corresponds to this.** No bundle says "this patient was observable from 1994 to 2019." It has to be *derived*, and how you derive it changes every rate you will ever publish.

our derived periods

    mean years observed      28.7
    minimum span             0 days   ← a single-encounter patient
    observed under 1 year    11 people

    hypertension prevalence
      naive (all 1,180)              28.0%
      restricted to ≥1yr observed    28.5%

Half a percentage point here, because Synthea generates full lifetimes for everyone. In real data, where many patients appear once, the same restriction routinely moves prevalence by several points. The technique is identical; only the magnitude differs.

### How we derive it, and the honesty required

We take each person's first-to-last recorded event across every domain table. That is the usual convention for EHR-derived data, and it is a *floor*, not a truth — it cannot see that a patient moved away three years before their last visit. Claims data supports a better derivation, because insurance enrolment spans are recorded explicitly rather than inferred.

The CDM lets you say which you did. We write `period_type_concept_id = 44814724`, "Period covering healthcare encounters" — a statement in the data itself that this was inferred from event dates, not read from an enrolment record.

## Using it: defining a cohort

The skill the whole exercise exists to enable.

A **cohort** is the atom of observational research: the set of people meeting criteria, over a period. Every OHDSI study is cohorts compared against cohorts.

cohort · adults with hypertension, observed ≥ 1 year

    -- 1. THE CONCEPT SET. concept_ancestor gives you the concept and
    --    everything beneath it, without enumerating codes by hand.
    WITH concept_set AS (
      SELECT descendant_concept_id AS concept_id
      FROM   concept_ancestor
      WHERE  ancestor_concept_id = 316866        -- Hypertensive disorder
    ),

    -- 2. QUALIFYING PEOPLE. The first matching diagnosis is the index date.
    indexed AS (
      SELECT co.person_id, min(co.condition_start_date) AS index_date
      FROM   condition_occurrence co
      JOIN   concept_set cs ON cs.concept_id = co.condition_concept_id
      GROUP  BY co.person_id
    )

    -- 3. THE DENOMINATOR, plus an age criterion applied at index.
    SELECT count(*) AS cohort_size
    FROM   indexed i
    JOIN   person p              ON p.person_id = i.person_id
    JOIN   observation_period op ON op.person_id = i.person_id
    WHERE  date_diff('year', op.observation_period_start_date,
                            op.observation_period_end_date) >= 1
      AND  year(i.index_date) - p.year_of_birth >= 18;

Three ideas generalise to every study you will ever write:

-   **The concept set.** Asking for one ancestor returns every descendant — including forms you had not thought of. Raw EHR data can never do this.
-   **The index date.** Nearly every cohort is anchored to a first occurrence. Follow-up, outcomes and washout windows are all measured from it.
-   **The denominator.** The `observation_period` join is what makes the count a rate rather than a tally.

Written this way, the same SQL runs unchanged against any OMOP database on earth. That portability is the reason the standard exists, and the reason a national programme like AHDEN is worth building.

## What FHIR is for, beyond our ETL

We used it as a file format. That is the smallest thing it does.

### SMART on FHIR — how an app gets inside a hospital

The single most commercially important thing in this module. SMART is a profile of OAuth2 that defines how a third-party application launches *inside* an EHR with permission to read a patient's data.

The flow: a clinician is looking at a patient in Epic and clicks your app. Epic redirects to your app with a `launch` token and the address of its authorisation server. Your app redirects the user to that server, which authenticates them and asks consent for the **scopes** you requested. You receive an access token — and crucially a *launch context* telling you which patient is on screen, so the clinician does not have to search again.

scopes — the permission grammar

    patient/Observation.rs   read+search Observations for the ONE patient in context
    user/Patient.rs          read+search any patient this USER may see
    system/*.rs              backend service, no user — for bulk pipelines
    offline_access           issue a refresh token so access outlives the session

Why it matters: this is the mechanism by which independent software is permitted into clinical workflow at all. Without it you are asking a hospital for a database extract, which is a procurement negotiation rather than an install.

### Bulk FHIR — population-scale export

The REST API hands you one patient at a time, which is hopeless for building a warehouse. Bulk Data (often written `$export`) is an asynchronous operation: you ask for a whole population, the server works in the background, and you poll until it hands you a set of newline-delimited JSON files, one per resource type.

**This is the realistic starting point for an OMOP build from a live EHR.**

**What this exposed when actually done**

Running the pipeline against a real bulk export rejected **23,075 of 23,707 resources** — every clinical row — and still reported a successful run with 35 of 36 checks passing.

The passes were nested inside one loop over files, which assumes each file is self-contained. True of a Synthea per-patient bundle; false of a bulk export, which splits resources by *type*, so conditions were processed before any patient existed.

The check suite could not object, because it inspects rows that exist and has nothing to say about a table being empty. This is the same lesson as the rest of the course arriving in its most extreme form.

### CDS Hooks — decision support at the point of care

A different shape entirely: instead of your app being opened, the EHR calls *you*. At defined moments — `patient-view`, `order-sign`, `medication-prescribe` — it posts context to your service, which may return "cards": a warning, a suggestion, or a link. This is how a risk model actually reaches a clinician rather than sitting in a dashboard nobody opens.

### Implementation Guides — see Module 12

The mechanism by which base FHIR is narrowed into something two systems can actually interoperate with. AU Core is Australia's, and it deserves its own section.

## What OMOP is for, beyond our ETL

We built the database. Here is what people do with one.

OHDSI organises analysis into three kinds of question. Nearly every study is one of them.

### 1 · Characterisation — "what is in this data?"

Descriptive. How many patients, what conditions, what drugs, what does the age distribution look like, how much is missing. Sounds unglamorous; it is where every project actually starts, because you cannot design a study against data whose shape you do not know.

Tooling: **Achilles** computes several thousand summary statistics over a CDM; **Data Quality Dashboard** runs a large standardised check suite — our 21-check suite is a small hand-built version of the same idea.

### 2 · Population-level estimation — "does X cause Y?"

Comparative effectiveness and safety. Does this drug raise the risk of that outcome, relative to an alternative? This is where the serious methodology lives: new-user cohort designs, propensity score matching to balance confounders, negative control outcomes to detect residual bias, self-controlled case series.

Tooling: the **HADES** family of R packages (`CohortMethod`, `SelfControlledCaseSeries`, `CohortIncidence`).

### 3 · Patient-level prediction — "what will happen to this person?"

Machine learning over the CDM: given everything known at an index date, predict an outcome within a horizon. The value of the CDM here is that the feature-extraction code is shared, so a model developed at one site can be externally validated at another without rewriting the data layer — the step most clinical ML never manages.

### The thing that makes it all worthwhile: network studies

This is OMOP's real payoff, and it is worth stating plainly. A researcher writes a study package once. It is shipped to twenty institutions in a dozen countries. Each runs it against *their own* CDM behind their own firewall, and returns only aggregate results.

**No patient-level data ever leaves any hospital.** That is what makes multi-national observational research legally and ethically possible at all — and it only works because every site's data is in the same shape with the same concept ids. Every convention in this course exists to serve that.

### ATLAS — the tool most OHDSI work is actually done in

A web application sitting over a CDM. You search the vocabulary to build concept sets, assemble cohort definitions through a UI, characterise a population, and run analyses — all without writing SQL, because ATLAS generates it for you.

There is a public instance running against a real CDM, no installation and no account:

[atlas-demo.ohdsi.org](https://atlas-demo.ohdsi.org/)

Worth an hour, and worth it *specifically now*, having written the underlying SQL by hand. You will recognise what every screen is doing, which is a very different experience from meeting it cold.

#### A route through the demo

1.  **Search** — type a condition name. You get concepts with their domain, class, standard flag and record counts in the underlying data. This is the `concept` table with a search box on it.
2.  **Concept Sets** — create one, add a concept, then tick *Descendants*. That checkbox is `concept_ancestor`. Click *Included Concepts* to see everything it just pulled in, which is the query from Module 05 with a UI on top.
3.  **Cohort Definitions** — build an entry event from your concept set, then add "with continuous observation of at least 365 days prior". Same two criteria as the cohort in Module 09.
4.  **Export → SQL** on that cohort. You will get the same shape of generated SQL that CirceR produces, because it is the same engine.
5.  **Data Sources** — someone else's CDM characterised: record counts per table, concept prevalence, age distributions. This is what Achilles produces, and it is what a site's data looks like before anyone asks a research question of it.

**Why this project used R instead**

ATLAS needs a backend (WebAPI) that supports only PostgreSQL, SQL Server, Oracle, Redshift and Snowflake. Not DuckDB. Running it locally would mean migrating the CDM and a 4.25M-concept vocabulary to Postgres and standing up three Docker containers — which teaches deployment, not OMOP.

Capr and CirceR drive the same `circe-be` engine and emit the same cohort JSON, so the machinery is identical. The demo instance is the right way to see the interface; R is the right way to actually use the engine against a DuckDB CDM.

### From Module 07's "not covered", what is actually worth learning

-   **Era tables** (`drug_era`, `condition_era`) — collapse overlapping records into continuous exposure periods. Usually what you actually want to analyse: twelve consecutive prescriptions are one treatment episode, not twelve events. Learn these early; they are cheap and change how you think.
-   **The Data Quality Dashboard** — the standardised version of what we hand-built. Highest value per hour of anything on this list.
-   **Propensity score methods** — if you want to make causal claims rather than describe. Genuinely hard, and the place where observational research earns or loses its credibility.
-   **SMART on FHIR**, on the other side — if you want to *ship* rather than analyse.

## Profiles, AU Core, and Australia

What "constrained profile" means, and why base FHIR alone is not enough.

### The problem with base FHIR

FHIR must work for a GP clinic in Adelaide, a US insurer and a Kenyan public health registry. To manage that, the base specification makes **almost every field optional**. A `Patient` resource with nothing but an `id` is valid FHIR.

Which creates the real problem: *two systems can both be perfectly valid FHIR and still be unable to exchange anything useful.* One sends a name and no identifier; the other requires an identifier and ignores names. Both conform. Neither interoperates.

### What a profile does

A **profile** narrows the base specification. It can only ever make the rules *stricter* — never add anything base FHIR forbids. Four things it does:

| Constraint              | Meaning                                            | Example                                           |
|-------------------------|----------------------------------------------------|---------------------------------------------------|
| **Cardinality**         | Make an optional field required, or limit repeats. | "`Patient.name` must be present" (base: optional) |
| **Terminology binding** | Restrict which code system a coded field may use.  | "`Condition.code` must use SNOMED CT-AU"          |
| **Extensions**          | Add a field base FHIR lacks, in a defined way.     | US Core's race extension                          |
| **Invariants**          | Rules across fields.                               | "if deceased, a date must be present"             |

Think of base FHIR as a form where every field is optional and every dropdown accepts free text. A profile is that same form with the mandatory fields marked and the dropdowns restricted to an agreed list. Nothing new is invented; choice is removed — and removing choice is precisely what makes two systems able to talk.

### What an Implementation Guide is

A published package of profiles plus value sets, examples, and narrative documentation, covering a whole use case or jurisdiction. **AU Core is Australia's**, produced by the **Sparked** accelerator — CSIRO with HL7 Australia. It states what an Australian system must support to be considered interoperable here: which resources, which fields are mandatory, which Australian terminologies to use.

Its US equivalent is US Core. **They are not compatible**, and this is exactly why the race/ethnicity decision in our pipeline matters: US Core defines a race extension, AU Core does not, because Australia records Indigenous status under a different model that does not map onto OMOP's race/ethnicity pair.

### The jurisdictional mapping

| United States        | Australia                                       |
|----------------------|-------------------------------------------------|
| US Core profiles     | **AU Core** — Sparked, CSIRO with HL7 Australia |
| USCDI data classes   | AU Core data set                                |
| HIPAA                | Privacy Act 1988; Australian Privacy Principles |
| ICD-10-CM            | ICD-10-AM                                       |
| CPT / HCPCS          | MBS item numbers                                |
| RxNorm               | AMT — Australian Medicines Terminology          |
| SNOMED CT US Edition | SNOMED CT-AU                                    |

**OMOP is a growth area in Australia right now.** The Australian Health Data Evidence Network is deploying OMOP CDM across Australian hospitals under a multi-year national initiative, with the AIHW standardising national admitted-patient data to the CDM. There is an OHDSI Australia chapter. These skills map onto work being funded and staffed today.

## Where to look things up

You asked how to learn what all these fields mean. These are the answers.

Every table and column in the CDM is formally documented. When you meet an unfamiliar field, you are meant to look it up rather than infer it — the specification is the reference, and it is good.

Specification

#### [OMOP CDM 5.4 spec](https://ohdsi.github.io/CommonDataModel/cdm54.html)

Every table, every column, its type, whether it is required, and what belongs in it. The direct answer to "what does this field mean". Bookmark it.

Textbook · free

#### [The Book of OHDSI](https://book.ohdsi.org/)

The canonical text for the whole ecosystem — data model, vocabulary, cohorts, study methods. Read chapters 4–6 for everything in Modules 04–09 here.

Browser

#### [Athena](https://athena.ohdsi.org/)

Search the vocabulary in a browser — concepts, domains, standard status, relationships. The fastest way to build intuition; use it alongside your SQL.

Specification

#### [FHIR R4 spec](https://hl7.org/fhir/R4/)

Every resource type with its fields, cardinalities and bound value sets. Dense but authoritative — go straight to the resource page you need.

Implementation guide

#### [AU Core](https://hl7.org.au/fhir/core/)

Australia's profiles. Read the Patient profile beside the base FHIR one to see exactly what "constrained" means in practice.

Course · free

#### [EHDEN Academy](https://academy.ehden.eu/)

Structured OMOP courses — CDM and vocabularies, ETL, OHDSI infrastructure. Course completion, not certification, but the standard curriculum.

## Glossary

Every term this course uses without assuming you have met it.

Resource  
The unit of FHIR. A JSON object with a `resourceType` and an `id`. About 150 types exist.

Bundle  
A FHIR envelope holding a list of resources. A container, not a record.

Reference  
A pointer from one resource to another. Three forms: `Type/id`, an absolute URL, or `urn:uuid:` within a bundle.

CodeableConcept  
FHIR's carrier for clinical meaning: an array of `{system, code, display}` plus free text.

Profile  
A constrained version of a FHIR resource — makes optional fields required, restricts code systems, adds extensions.

Implementation Guide  
A published package of profiles for a jurisdiction or use case. AU Core is Australia's.

SMART on FHIR  
The OAuth2 profile letting third-party apps launch inside an EHR with scoped access to a patient in context.

Bulk FHIR  
Asynchronous population-scale export (`$export`). The realistic input to an OMOP build.

CDM  
Common Data Model. The OMOP schema. Current version 5.4.

Concept  
One distinct clinical idea — a disease, a test, a drug, a unit. The row in the `concept` table.

concept_id  
OMOP's integer identifier for a concept. Arbitrary, and meaningful only inside OMOP.

concept_code  
The code as it exists in its *source* vocabulary — e.g. SNOMED `59621000`. The bridge back to the source system.

Standard concept  
The one concept OMOP designates canonical for an idea (`standard_concept = 'S'`). Cohorts match only these.

Domain  
A concept's category — Condition, Measurement, Drug, Observation, Procedure. Determines the destination table.

Maps to  
A row in `concept_relationship` meaning "when you receive concept 1, store concept 2 instead".

Ancestor / descendant  
Positions in the *is-a* hierarchy. Ancestors are more general, descendants more specific. Every concept is its own ancestor at level 0.

concept_ancestor  
The precomputed transitive closure of the hierarchy — every ancestor–descendant pair at every distance.

Surrogate key  
An identifier you invent (`person_id = 1`) standing in for a source id you cannot use. The original is kept in `*_source_value`.

Numerator / denominator  
Top and bottom of a rate: how many had the thing, over how many could have had it.

observation_period  
The window during which a person was observable. The denominator. Derived, never given.

Cohort  
A set of people meeting criteria over a period. The atom of observational research.

Index date  
The anchor event a cohort is measured from — usually a first occurrence.

ETL  
Extract, Transform, Load. Read from the source, reshape, write to the target. What this project is.

Athena  
OHDSI's vocabulary distribution service. Free account; some vocabularies need a UMLS licence.

ATLAS  
The OHDSI web application for building concept sets and cohort definitions over a CDM.

HADES  
The OHDSI R package family for the statistical analyses.

OHDSI  
Observational Health Data Sciences and Informatics. The community that maintains OMOP.

AHDEN  
Australian Health Data Evidence Network — running Australia's national OMOP deployment.

Worked against Synthea FHIR R4 sample data (1,180 synthetic patients, no real patient data) loaded into OMOP CDM 5.4. All figures quoted are from that run: 331,000 FHIR resources in, 332,000 CDM rows out, 21 of 21 data-quality checks passing. Unmapped rates reflect a 31,976-concept development vocabulary, not the full Athena release.
