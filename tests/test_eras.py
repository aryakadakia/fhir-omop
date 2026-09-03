"""Tests for era derivation.

Eras are built by a gap-and-island query, which is easy to get subtly wrong in
ways that produce plausible-looking output. Each test here is a specific way
the algorithm can be broken.
"""

import duckdb
import pytest

from fhir_omop import ddl
from fhir_omop.mappings import eras


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    for stmt in (ddl.DRUG_ERA, ddl.CONDITION_ERA, ddl.CONDITION_OCCURRENCE,
                 ddl.DRUG_EXPOSURE):
        c.execute(stmt)
    c.execute(
        "CREATE TABLE concept (concept_id INTEGER, concept_name VARCHAR, "
        "concept_class_id VARCHAR, standard_concept VARCHAR)"
    )
    c.executemany("INSERT INTO concept VALUES (?,?,?,?)", [
        (1000, "Lisinopril", "Ingredient", "S"),
        (1001, "Lisinopril 10 MG tablet", "Clinical Drug", "S"),
        (1002, "Lisinopril 20 MG tablet", "Clinical Drug", "S"),
        (2000, "A vaccine", "CVX", "S"),
    ])
    c.execute(
        "CREATE TABLE concept_ancestor (ancestor_concept_id INTEGER, "
        "descendant_concept_id INTEGER)"
    )
    # Both strengths roll up to the same ingredient. The vaccine rolls up to
    # nothing, as CVX concepts do in the real vocabulary.
    c.executemany("INSERT INTO concept_ancestor VALUES (?,?)",
                  [(1000, 1001), (1000, 1002), (1000, 1000)])
    return c


def add_exposure(con, person, concept, start, end=None, eid=[0]):
    eid[0] += 1
    con.execute(
        "INSERT INTO drug_exposure (drug_exposure_id, person_id, drug_concept_id, "
        "drug_exposure_start_date, drug_exposure_end_date, drug_type_concept_id) "
        "VALUES (?,?,?,?,?,?)",
        [eid[0], person, concept, start, end or start, 32817])


def add_condition(con, person, concept, start, end=None, cid=[0]):
    cid[0] += 1
    con.execute(
        "INSERT INTO condition_occurrence (condition_occurrence_id, person_id, "
        "condition_concept_id, condition_start_date, condition_end_date, "
        "condition_type_concept_id) VALUES (?,?,?,?,?,?)",
        [cid[0], person, concept, start, end, 32817])


def eras_of(con):
    return con.execute(
        "SELECT person_id, drug_concept_id, drug_era_start_date, "
        "drug_era_end_date, drug_exposure_count FROM drug_era "
        "ORDER BY person_id, drug_era_start_date"
    ).fetchall()


class TestPersistenceWindow:
    def test_exposures_inside_the_window_form_one_era(self, con):
        add_exposure(con, 1, 1001, "2020-01-01")
        add_exposure(con, 1, 1001, "2020-01-20")   # 19 days later, < 30
        eras.derive_drug_era(con)
        rows = eras_of(con)
        assert len(rows) == 1
        assert rows[0][4] == 2                      # both exposures counted

    def test_exposures_beyond_the_window_form_two_eras(self, con):
        add_exposure(con, 1, 1001, "2020-01-01")
        add_exposure(con, 1, 1001, "2020-06-01")   # months later
        eras.derive_drug_era(con)
        assert len(eras_of(con)) == 2

    def test_boundary_exactly_at_the_window_does_not_split(self, con):
        # 30 days later is within the window; 31 would not be.
        add_exposure(con, 1, 1001, "2020-01-01")
        add_exposure(con, 1, 1001, "2020-01-31")
        eras.derive_drug_era(con)
        assert len(eras_of(con)) == 1


class TestIngredientRollup:
    def test_different_strengths_collapse_to_one_ingredient_era(self, con):
        # The point of rolling up: 10 MG and 20 MG lisinopril are the same
        # treatment, not two unrelated ones.
        add_exposure(con, 1, 1001, "2020-01-01")
        add_exposure(con, 1, 1002, "2020-01-10")
        eras.derive_drug_era(con)
        rows = eras_of(con)
        assert len(rows) == 1
        assert rows[0][1] == 1000                   # the ingredient

    def test_concepts_without_an_ingredient_produce_no_era(self, con):
        # CVX vaccines are not in the RxNorm ingredient hierarchy. Producing
        # no era for them is the standard definition, not a dropped row.
        add_exposure(con, 1, 2000, "2020-01-01")
        rows, rolled_up = eras.derive_drug_era(con)
        assert rows == 0
        assert rolled_up == 0

    def test_unmapped_concepts_are_excluded(self, con):
        add_exposure(con, 1, 0, "2020-01-01")
        assert eras.derive_drug_era(con)[0] == 0


class TestSeparation:
    def test_different_people_never_share_an_era(self, con):
        add_exposure(con, 1, 1001, "2020-01-01")
        add_exposure(con, 2, 1001, "2020-01-02")
        eras.derive_drug_era(con)
        assert len(eras_of(con)) == 2

    def test_enclosed_exposure_does_not_split_an_era(self, con):
        # A long exposure enclosing a later short one. Comparing against the
        # PREVIOUS row's end rather than the running maximum would wrongly
        # start a new era at the third exposure.
        add_exposure(con, 1, 1001, "2020-01-01", "2020-12-31")
        add_exposure(con, 1, 1001, "2020-02-01", "2020-02-02")
        add_exposure(con, 1, 1001, "2020-11-01", "2020-11-02")
        eras.derive_drug_era(con)
        rows = eras_of(con)
        assert len(rows) == 1
        assert str(rows[0][3]) == "2020-12-31"      # end is the running max


class TestConditionEra:
    def test_repeated_diagnoses_collapse(self, con):
        add_condition(con, 1, 320128, "2020-01-01")
        add_condition(con, 1, 320128, "2020-01-15")
        assert eras.derive_condition_era(con) == 1

    def test_unresolved_condition_uses_its_start_date(self, con):
        # condition_end_date is null until a condition resolves; treating null
        # as "no end" would make max(end) null and break the era boundary.
        add_condition(con, 1, 320128, "2020-01-01", None)
        eras.derive_condition_era(con)
        row = con.execute(
            "SELECT condition_era_start_date, condition_era_end_date FROM condition_era"
        ).fetchone()
        assert str(row[0]) == "2020-01-01"
        assert str(row[1]) == "2020-01-01"

    def test_distant_recurrences_are_separate_eras(self, con):
        add_condition(con, 1, 320128, "2020-01-01", "2020-01-05")
        add_condition(con, 1, 320128, "2023-01-01", "2023-01-05")
        assert eras.derive_condition_era(con) == 2


class TestGapDays:
    def test_contiguous_exposures_have_no_gap(self, con):
        add_exposure(con, 1, 1001, "2020-01-01", "2020-01-10")
        add_exposure(con, 1, 1001, "2020-01-11", "2020-01-20")
        eras.derive_drug_era(con)
        assert con.execute("SELECT gap_days FROM drug_era").fetchone()[0] == 0

    def test_gap_within_an_era_is_counted(self, con):
        # Two single-day exposures 20 days apart: one era, 19 uncovered days.
        add_exposure(con, 1, 1001, "2020-01-01")
        add_exposure(con, 1, 1001, "2020-01-21")
        eras.derive_drug_era(con)
        assert con.execute("SELECT gap_days FROM drug_era").fetchone()[0] == 19
