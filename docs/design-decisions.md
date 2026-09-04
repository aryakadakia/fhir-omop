# Design decisions

Why the mappings do what they do. Extracted from the README, which answers
"what is this and how do I run it" instead.

Companion documents: `manual.html` for how to operate the pipeline,
`course.html` for the underlying standards, `bugs-found.md` for what went
wrong while building it.

---

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
`condition_occurrence` fails silently, the rows insert, every foreign key
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
directly, those rows would never match a cohort definition, cohort
definitions are written against standard concepts, so the patients would be
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
`year_of_birth` and permits null month and day, which maps onto FHIR's
precision model exactly. The common bug is defaulting to January 1st, which
invents a birthday for every year-precision record.

**3. Race and ethnicity are `NOT NULL` in the CDM, and often absent in reality.**
Synthea emits US Core race/ethnicity extensions because it models a US
population. **AU Core has no race extension at all**, Australian datasets
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
