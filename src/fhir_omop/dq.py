"""Data-quality checks over the loaded CDM.

Structured after the OHDSI Data Quality Dashboard's three categories:

  conformance   -- does the value obey the CDM's structural rules?
  completeness  -- is a required value present / how much is unmapped?
  plausibility  -- could the value be true of a real person?

Each check returns a row count of VIOLATIONS, so zero is always a pass. A
check that cannot fail is not a check, so every one of these is written to be
capable of firing on realistic bad input.
"""

from __future__ import annotations

from dataclasses import dataclass

# Upper bound on a plausible year of birth. Deliberately generous -- the intent
# is to catch parsing errors and sentinel dates, not to adjudicate longevity.
MAX_PLAUSIBLE_AGE = 120


@dataclass
class Check:
    name: str
    category: str
    description: str
    sql: str


CHECKS: list[Check] = [
    Check(
        "person_id_unique",
        "conformance",
        "person_id must be unique",
        "SELECT count(*) FROM ("
        "  SELECT person_id FROM person GROUP BY person_id HAVING count(*) > 1"
        ")",
    ),
    Check(
        "gender_concept_id_valid",
        "conformance",
        "gender_concept_id must exist in the vocabulary",
        "SELECT count(*) FROM person p "
        "LEFT JOIN concept c ON c.concept_id = p.gender_concept_id "
        "WHERE c.concept_id IS NULL",
    ),
    Check(
        "gender_concept_in_gender_domain",
        "conformance",
        "gender_concept_id must be a Gender-domain concept (or 0)",
        "SELECT count(*) FROM person p "
        "JOIN concept c ON c.concept_id = p.gender_concept_id "
        "WHERE p.gender_concept_id <> 0 AND c.domain_id <> 'Gender'",
    ),
    Check(
        "year_of_birth_present",
        "completeness",
        "year_of_birth is NOT NULL in CDM 5.4",
        "SELECT count(*) FROM person WHERE year_of_birth IS NULL",
    ),
    Check(
        "year_of_birth_plausible",
        "plausibility",
        f"year_of_birth within the last {MAX_PLAUSIBLE_AGE} years and not in the future",
        "SELECT count(*) FROM person WHERE year_of_birth > year(current_date) "
        f"OR year_of_birth < year(current_date) - {MAX_PLAUSIBLE_AGE}",
    ),
    Check(
        "month_of_birth_plausible",
        "plausibility",
        "month_of_birth between 1 and 12 when present",
        "SELECT count(*) FROM person "
        "WHERE month_of_birth IS NOT NULL AND month_of_birth NOT BETWEEN 1 AND 12",
    ),
    Check(
        "day_of_birth_plausible",
        "plausibility",
        "day_of_birth between 1 and 31 when present",
        "SELECT count(*) FROM person "
        "WHERE day_of_birth IS NOT NULL AND day_of_birth NOT BETWEEN 1 AND 31",
    ),
    Check(
        "condition_person_fk",
        "conformance",
        "every condition_occurrence.person_id exists in person",
        "SELECT count(*) FROM condition_occurrence co "
        "LEFT JOIN person p ON p.person_id = co.person_id "
        "WHERE p.person_id IS NULL",
    ),
    Check(
        "condition_concept_is_standard",
        "conformance",
        "condition_concept_id must be a standard concept (or 0)",
        "SELECT count(*) FROM condition_occurrence co "
        "JOIN concept c ON c.concept_id = co.condition_concept_id "
        "WHERE co.condition_concept_id <> 0 AND c.standard_concept IS DISTINCT FROM 'S'",
    ),
    Check(
        "condition_concept_in_condition_domain",
        "conformance",
        "condition_concept_id must be a Condition-domain concept (or 0)",
        "SELECT count(*) FROM condition_occurrence co "
        "JOIN concept c ON c.concept_id = co.condition_concept_id "
        "WHERE co.condition_concept_id <> 0 AND c.domain_id <> 'Condition'",
    ),
    Check(
        "condition_end_after_start",
        "plausibility",
        "condition_end_date must not precede condition_start_date",
        "SELECT count(*) FROM condition_occurrence "
        "WHERE condition_end_date IS NOT NULL "
        "AND condition_end_date < condition_start_date",
    ),
    Check(
        "condition_start_after_birth",
        "plausibility",
        "a condition cannot start before the person was born",
        "SELECT count(*) FROM condition_occurrence co "
        "JOIN person p ON p.person_id = co.person_id "
        "WHERE year(co.condition_start_date) < p.year_of_birth",
    ),
    Check(
        "every_person_has_observation_period",
        "completeness",
        "every person must have an observation period (the denominator)",
        "SELECT count(*) FROM person p LEFT JOIN observation_period op "
        "ON op.person_id = p.person_id WHERE op.person_id IS NULL",
    ),
    Check(
        "observation_period_end_after_start",
        "plausibility",
        "observation_period_end_date must not precede its start",
        "SELECT count(*) FROM observation_period "
        "WHERE observation_period_end_date < observation_period_start_date",
    ),
    Check(
        "events_within_observation_period",
        "plausibility",
        "condition events must fall inside the person's observation period",
        "SELECT count(*) FROM condition_occurrence co "
        "JOIN observation_period op ON op.person_id = co.person_id "
        "WHERE co.condition_start_date < op.observation_period_start_date "
        "   OR co.condition_start_date > op.observation_period_end_date",
    ),
    Check(
        "visit_end_after_start",
        "plausibility",
        "visit_end_date must not precede visit_start_date",
        "SELECT count(*) FROM visit_occurrence WHERE visit_end_date < visit_start_date",
    ),
    Check(
        "measurement_person_fk",
        "conformance",
        "every measurement.person_id exists in person",
        "SELECT count(*) FROM measurement m LEFT JOIN person p "
        "ON p.person_id = m.person_id WHERE p.person_id IS NULL",
    ),
    Check(
        "measurement_concept_in_measurement_domain",
        "conformance",
        "measurement_concept_id must be a Measurement-domain concept (or 0)",
        "SELECT count(*) FROM measurement m JOIN concept c "
        "ON c.concept_id = m.measurement_concept_id "
        "WHERE m.measurement_concept_id <> 0 AND c.domain_id <> 'Measurement'",
    ),
    Check(
        "drug_person_fk",
        "conformance",
        "every drug_exposure.person_id exists in person",
        "SELECT count(*) FROM drug_exposure d LEFT JOIN person p "
        "ON p.person_id = d.person_id WHERE p.person_id IS NULL",
    ),
    Check(
        "visit_fk_resolves",
        "conformance",
        "measurement.visit_occurrence_id must resolve when present",
        "SELECT count(*) FROM measurement m LEFT JOIN visit_occurrence v "
        "ON v.visit_occurrence_id = m.visit_occurrence_id "
        "WHERE m.visit_occurrence_id IS NOT NULL AND v.visit_occurrence_id IS NULL",
    ),
    Check(
        "drug_concept_in_drug_domain",
        "conformance",
        "drug_concept_id must be a Drug-domain concept (or 0)",
        "SELECT count(*) FROM drug_exposure d JOIN concept c "
        "ON c.concept_id = d.drug_concept_id "
        "WHERE d.drug_concept_id <> 0 AND c.domain_id <> 'Drug'",
    ),
    Check(
        "observation_concept_in_observation_domain",
        "conformance",
        "observation_concept_id must be an Observation-domain concept (or 0)",
        "SELECT count(*) FROM observation o JOIN concept c "
        "ON c.concept_id = o.observation_concept_id "
        "WHERE o.observation_concept_id <> 0 AND c.domain_id <> 'Observation'",
    ),
    Check(
        "all_event_concepts_are_standard",
        "conformance",
        "every non-zero event concept_id must be a standard concept",
        """
        SELECT count(*) FROM (
          SELECT measurement_concept_id AS cid FROM measurement
          UNION ALL SELECT observation_concept_id FROM observation
          UNION ALL SELECT drug_concept_id FROM drug_exposure
          UNION ALL SELECT condition_concept_id FROM condition_occurrence
        ) e JOIN concept c ON c.concept_id = e.cid
        WHERE e.cid <> 0 AND c.standard_concept IS DISTINCT FROM 'S'
        """,
    ),
    Check(
        "visit_fk_resolves_conditions",
        "conformance",
        "condition_occurrence.visit_occurrence_id must resolve when present",
        "SELECT count(*) FROM condition_occurrence co LEFT JOIN visit_occurrence v "
        "ON v.visit_occurrence_id = co.visit_occurrence_id "
        "WHERE co.visit_occurrence_id IS NOT NULL AND v.visit_occurrence_id IS NULL",
    ),
    Check(
        "drug_end_not_before_start",
        "plausibility",
        "drug_exposure_end_date must not precede its start",
        "SELECT count(*) FROM drug_exposure "
        "WHERE drug_exposure_end_date < drug_exposure_start_date",
    ),
    Check(
        "every_person_has_provenance",
        "completeness",
        "every person row traces back to a source resource",
        "SELECT count(*) FROM person p "
        "LEFT JOIN _etl_provenance e "
        "  ON e.cdm_table = 'person' AND e.cdm_pk = p.person_id "
        "WHERE e.cdm_pk IS NULL",
    ),
]


def run(con) -> list[tuple[Check, int]]:
    return [(check, con.execute(check.sql).fetchone()[0]) for check in CHECKS]


def unmapped_rates(con) -> list[tuple[str, int, int, float]]:
    """Share of rows sitting on concept_id 0, per mapped field.

    This is the single most useful number in an ETL. A pipeline can pass every
    conformance check while mapping almost nothing, and only this figure shows
    it.
    """
    rows = []
    for table, column in (
        ("person", "gender_concept_id"),
        ("person", "race_concept_id"),
        ("person", "ethnicity_concept_id"),
        ("condition_occurrence", "condition_concept_id"),
        ("measurement", "measurement_concept_id"),
        ("observation", "observation_concept_id"),
        ("drug_exposure", "drug_concept_id"),
        ("visit_occurrence", "visit_concept_id"),
    ):
        total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        unmapped = con.execute(
            f"SELECT count(*) FROM {table} WHERE {column} = 0"
        ).fetchone()[0]
        pct = (unmapped / total * 100) if total else 0.0
        rows.append((f"{table}.{column}", unmapped, total, pct))
    return rows
