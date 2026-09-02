"""Derive OMOP `observation_period` from the loaded clinical tables.

`observation_period` has no FHIR counterpart. Nothing in a FHIR bundle says
"this patient was observable from X to Y" -- it must be DERIVED, and the
derivation is a judgement call that changes every rate computed downstream.

Why it matters. Suppose 330 of 1,180 people have hypertension. Is prevalence
28%? Only if all 1,180 were actually observable. If some appear in the data
for a single day, they were never at risk of being diagnosed with anything,
and including them inflates the denominator and deflates every rate.

`observation_period` is the table that answers "who was under observation, and
when". Without it a CDM is a pile of events with no population attached.

The derivation used here -- first to last recorded event per person -- is the
common convention for EHR-derived data. It is a floor, not a truth: it cannot
see the gap between a patient leaving a health service and their last visit.
Claims data supports a better derivation because enrolment spans are recorded
explicitly.
"""

from __future__ import annotations

# 44814724 = "Period covering healthcare encounters"
# The honest type concept for a period inferred from event dates rather than
# read from an enrolment record.
PERIOD_TYPE_INFERRED = 44814724

# Every table contributing an event date to the observable span.
EVENT_SOURCES = [
    ("condition_occurrence", "condition_start_date", "condition_end_date"),
    ("visit_occurrence", "visit_start_date", "visit_end_date"),
    ("measurement", "measurement_date", "measurement_date"),
    ("observation", "observation_date", "observation_date"),
    ("drug_exposure", "drug_exposure_start_date", "drug_exposure_end_date"),
]


def derive(con) -> int:
    """Populate observation_period. Returns the number of rows written."""
    unions = "\n UNION ALL ".join(
        f"SELECT person_id, {start} AS d FROM {table} "
        f"UNION ALL SELECT person_id, {end} AS d FROM {table}"
        for table, start, end in EVENT_SOURCES
    )

    con.execute(f"""
        INSERT INTO observation_period
        SELECT
            row_number() OVER (ORDER BY person_id) AS observation_period_id,
            person_id,
            min(d) AS observation_period_start_date,
            max(d) AS observation_period_end_date,
            {PERIOD_TYPE_INFERRED} AS period_type_concept_id
        FROM ({unions})
        WHERE d IS NOT NULL
        GROUP BY person_id
    """)

    return con.execute("SELECT count(*) FROM observation_period").fetchone()[0]
