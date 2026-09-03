"""Tests for observation_period derivation.

observation_period has no FHIR counterpart -- it is derived, and it is the
denominator for every rate computed from the CDM. Getting it wrong silently
changes every published number.
"""

import duckdb
import pytest

from fhir_omop import ddl
from fhir_omop.mappings import observation_period


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    for stmt in (ddl.PERSON, ddl.OBSERVATION_PERIOD, ddl.VISIT_OCCURRENCE,
                 ddl.CONDITION_OCCURRENCE, ddl.MEASUREMENT, ddl.OBSERVATION,
                 ddl.DRUG_EXPOSURE):
        c.execute(stmt)
    return c


def add_condition(con, person, start, end=None, cid=[0]):
    cid[0] += 1
    con.execute(
        "INSERT INTO condition_occurrence (condition_occurrence_id, person_id, "
        "condition_concept_id, condition_start_date, condition_end_date, "
        "condition_type_concept_id) VALUES (?,?,?,?,?,?)",
        [cid[0], person, 1, start, end, 32817])


def add_measurement(con, person, date, mid=[0]):
    mid[0] += 1
    con.execute(
        "INSERT INTO measurement (measurement_id, person_id, measurement_concept_id, "
        "measurement_date, measurement_type_concept_id) VALUES (?,?,?,?,?)",
        [mid[0], person, 1, date, 32817])


def periods(con):
    return con.execute(
        "SELECT person_id, observation_period_start_date, observation_period_end_date "
        "FROM observation_period ORDER BY person_id"
    ).fetchall()


class TestSpan:
    def test_span_covers_first_to_last_event(self, con):
        add_condition(con, 1, "2010-01-01", "2010-01-05")
        add_condition(con, 1, "2015-06-01", "2015-06-02")
        observation_period.derive(con)
        p = periods(con)[0]
        assert str(p[1]) == "2010-01-01"
        assert str(p[2]) == "2015-06-02"

    def test_span_draws_from_every_domain_table(self, con):
        # A person whose only late event is a measurement must still have the
        # period extended -- otherwise their observable window is understated
        # and their events fall outside it.
        add_condition(con, 1, "2010-01-01", "2010-01-05")
        add_measurement(con, 1, "2020-12-31")
        observation_period.derive(con)
        assert str(periods(con)[0][2]) == "2020-12-31"

    def test_single_event_gives_a_zero_length_period(self, con):
        # Legitimate and important: a one-day period marks someone who was
        # never realistically at risk of being diagnosed with anything.
        add_condition(con, 1, "2010-01-01", "2010-01-01")
        observation_period.derive(con)
        p = periods(con)[0]
        assert p[1] == p[2]

    def test_null_end_dates_are_ignored_not_propagated(self, con):
        # An unresolved condition has a null end. If nulls reached max() the
        # period end would be wrong or null.
        add_condition(con, 1, "2010-01-01", None)
        add_condition(con, 1, "2012-01-01", "2012-02-01")
        observation_period.derive(con)
        assert str(periods(con)[0][2]) == "2012-02-01"


class TestSeparation:
    def test_each_person_gets_one_period(self, con):
        add_condition(con, 1, "2010-01-01", "2010-01-02")
        add_condition(con, 2, "2011-01-01", "2011-01-02")
        assert observation_period.derive(con) == 2
        assert len(periods(con)) == 2

    def test_people_do_not_share_spans(self, con):
        add_condition(con, 1, "2010-01-01", "2010-01-02")
        add_condition(con, 2, "2020-01-01", "2020-01-02")
        observation_period.derive(con)
        rows = periods(con)
        assert str(rows[0][2]) == "2010-01-02"
        assert str(rows[1][1]) == "2020-01-01"

    def test_person_with_no_events_gets_no_period(self, con):
        # Nothing to infer a span from. Such a person must not silently receive
        # a period, or they would be counted in a denominator they never
        # belonged to.
        con.execute("INSERT INTO person (person_id, gender_concept_id, "
                    "year_of_birth, race_concept_id, ethnicity_concept_id) "
                    "VALUES (99, 8507, 1980, 0, 0)")
        assert observation_period.derive(con) == 0


class TestTypeConcept:
    def test_period_is_marked_as_inferred(self, con):
        # The CDM records HOW the period was determined. Ours is inferred from
        # event dates, not read from an enrolment record, and says so.
        add_condition(con, 1, "2010-01-01", "2010-01-02")
        observation_period.derive(con)
        t = con.execute("SELECT period_type_concept_id FROM observation_period").fetchone()[0]
        assert t == observation_period.PERIOD_TYPE_INFERRED
