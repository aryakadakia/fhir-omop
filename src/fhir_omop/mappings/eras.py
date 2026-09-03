"""Derive OMOP `drug_era` and `condition_era` from the loaded event tables.

Era tables answer a question the raw event tables cannot: not "when was this
drug prescribed" but "for how long was this person ON it".

Twelve monthly prescriptions of the same drug are twelve rows in
drug_exposure. Clinically they are one twelve-month course of treatment.
Counting the rows tells you about prescribing behaviour; counting the era
tells you about exposure. Almost every question worth asking -- how long were
people treated, did treatment precede the outcome, how many switched -- is a
question about eras.

Two ideas do the work.

PERSISTENCE WINDOW. Two exposures separated by less than the window are one
era; a longer gap starts a new one. 30 days is the OHDSI convention: it
tolerates a late refill without merging genuinely separate courses of
treatment years apart.

INGREDIENT ROLLUP (drugs only). Drug concepts arrive at many levels of
specificity -- "Lisinopril 10 MG Oral Tablet", a branded pack, a quantified
form. Eras are built at the INGREDIENT level, so all forms of lisinopril
collapse into one era rather than three unrelated ones. The rollup uses
concept_ancestor, which is the same hierarchy machinery cohort definitions
use.

Conditions get no rollup: there is no diagnosis equivalent of an ingredient,
so condition_era uses condition_concept_id directly.
"""

from __future__ import annotations

# OHDSI convention. Exposures closer together than this are one era.
PERSISTENCE_WINDOW_DAYS = 30

# The gap-and-island pattern, used identically for both era types:
#
#   1. for each (person, concept), look back at the furthest end date seen so
#      far in date order
#   2. flag a row as starting a new era if its start is beyond that running
#      maximum plus the persistence window
#   3. a running sum of those flags numbers the eras
#   4. group by that number
#
# Step 1 uses a running MAX rather than the previous row's end date, because a
# long exposure can enclose several shorter later ones; comparing against only
# the immediately preceding row would split an era that never actually lapsed.
_ERA_SQL = """
WITH ordered AS (
    SELECT
        person_id,
        {concept_col} AS concept_id,
        {start_col}   AS start_date,
        {end_col}     AS end_date,
        max({end_col}) OVER (
            PARTITION BY person_id, {concept_col}
            ORDER BY {start_col}, {end_col}
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS prior_max_end
    FROM {source}
    WHERE {concept_col} <> 0
      {extra_where}
),
flagged AS (
    SELECT *,
        CASE
            WHEN prior_max_end IS NULL THEN 1
            WHEN start_date > prior_max_end + INTERVAL {window} DAY THEN 1
            ELSE 0
        END AS starts_era
    FROM ordered
),
numbered AS (
    SELECT *,
        sum(starts_era) OVER (
            PARTITION BY person_id, concept_id
            ORDER BY start_date, end_date
            ROWS UNBOUNDED PRECEDING
        ) AS era_number
    FROM flagged
)
SELECT
    person_id,
    concept_id,
    min(start_date) AS era_start,
    max(end_date)   AS era_end,
    count(*)        AS event_count,
    -- Days inside the era not covered by any exposure. Computed as the era
    -- span minus the summed length of its exposures, which OVERSTATES the gap
    -- when exposures overlap. Exact for this dataset, where every exposure is
    -- a single day; documented rather than silently approximated.
    greatest(
        0,
        date_diff('day', min(start_date), max(end_date)) + 1
        - sum(date_diff('day', start_date, end_date) + 1)
    ) AS gap_days
FROM numbered
GROUP BY person_id, concept_id, era_number
"""


def _era_query(source, concept_col, start_col, end_col, extra_where=""):
    return _ERA_SQL.format(
        source=source, concept_col=concept_col, start_col=start_col,
        end_col=end_col, window=PERSISTENCE_WINDOW_DAYS, extra_where=extra_where,
    )


def derive_drug_era(con) -> tuple[int, int]:
    """Populate drug_era. Returns (era rows, exposures that rolled up).

    Only exposures whose concept has an Ingredient ancestor produce eras. That
    excludes vaccines: CVX concepts are not part of the RxNorm ingredient
    hierarchy, so immunizations legitimately yield no drug eras. This is the
    standard OHDSI definition, not an omission -- but it means drug_era covers
    a strict subset of drug_exposure, and the counts should never be expected
    to reconcile.
    """
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _exposure_ingredient AS
        SELECT
            d.person_id,
            ca.ancestor_concept_id AS ingredient_concept_id,
            d.drug_exposure_start_date,
            d.drug_exposure_end_date
        FROM drug_exposure d
        JOIN concept_ancestor ca ON ca.descendant_concept_id = d.drug_concept_id
        JOIN concept c ON c.concept_id = ca.ancestor_concept_id
        WHERE d.drug_concept_id <> 0
          AND c.concept_class_id = 'Ingredient'
          AND c.standard_concept = 'S'
    """)
    rolled_up = con.execute("SELECT count(*) FROM _exposure_ingredient").fetchone()[0]

    query = _era_query(
        "_exposure_ingredient", "ingredient_concept_id",
        "drug_exposure_start_date", "drug_exposure_end_date",
    )
    con.execute(f"""
        INSERT INTO drug_era
        SELECT row_number() OVER (ORDER BY person_id, concept_id, era_start),
               person_id, concept_id, era_start, era_end, event_count, gap_days
        FROM ({query})
    """)
    con.execute("DROP TABLE _exposure_ingredient")
    rows = con.execute("SELECT count(*) FROM drug_era").fetchone()[0]
    return rows, rolled_up


def derive_condition_era(con) -> int:
    """Populate condition_era. Returns the number of era rows."""
    # condition_end_date is null for conditions that have not resolved; an
    # unresolved condition is treated as a point event at its start date for
    # the purpose of era boundaries, which is the OHDSI convention.
    query = _era_query(
        "(SELECT person_id, condition_concept_id, condition_start_date, "
        " coalesce(condition_end_date, condition_start_date) AS condition_end_date "
        " FROM condition_occurrence)",
        "condition_concept_id", "condition_start_date", "condition_end_date",
    )
    con.execute(f"""
        INSERT INTO condition_era
        SELECT row_number() OVER (ORDER BY person_id, concept_id, era_start),
               person_id, concept_id, era_start, era_end, event_count
        FROM ({query})
    """)
    return con.execute("SELECT count(*) FROM condition_era").fetchone()[0]
