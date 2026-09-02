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
    total = con.execute("SELECT count(*) FROM person").fetchone()[0]
    rows = []
    for column in ("gender_concept_id", "race_concept_id", "ethnicity_concept_id"):
        unmapped = con.execute(
            f"SELECT count(*) FROM person WHERE {column} = 0"
        ).fetchone()[0]
        pct = (unmapped / total * 100) if total else 0.0
        rows.append((column, unmapped, total, pct))
    return rows
