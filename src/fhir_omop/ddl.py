"""OMOP CDM 5.4 table definitions.

Only the subset this ETL populates is defined here. Column names, types and
ordering follow the published CDM 5.4 specification so that the output is
readable by standard OHDSI tooling (Achilles, Data Quality Dashboard, ATLAS).

Reference: https://ohdsi.github.io/CommonDataModel/cdm54.html
"""

# CDM 5.4 `person`. Required (NOT NULL) fields per the spec:
#   person_id, gender_concept_id, year_of_birth,
#   race_concept_id, ethnicity_concept_id.
# Note that race and ethnicity are NOT NULL in the standard even though many
# jurisdictions do not collect them -- see mappings/person.py for how this is
# handled honestly rather than by inventing values.
PERSON = """
CREATE TABLE IF NOT EXISTS person (
    person_id                   BIGINT  NOT NULL,
    gender_concept_id           INTEGER NOT NULL,
    year_of_birth               INTEGER NOT NULL,
    month_of_birth              INTEGER,
    day_of_birth                INTEGER,
    birth_datetime              TIMESTAMP,
    race_concept_id             INTEGER NOT NULL,
    ethnicity_concept_id        INTEGER NOT NULL,
    location_id                 BIGINT,
    provider_id                 BIGINT,
    care_site_id                BIGINT,
    person_source_value         VARCHAR,
    gender_source_value         VARCHAR,
    gender_source_concept_id    INTEGER,
    race_source_value           VARCHAR,
    race_source_concept_id      INTEGER,
    ethnicity_source_value      VARCHAR,
    ethnicity_source_concept_id INTEGER
);
"""

# Provenance table. Not part of the CDM spec -- this is ours, and it exists so
# every mapped row can be traced back to the exact FHIR resource that produced
# it. Without this an ETL is unauditable, which is disqualifying in a
# regulated setting.
ETL_PROVENANCE = """
CREATE TABLE IF NOT EXISTS _etl_provenance (
    cdm_table        VARCHAR NOT NULL,
    cdm_pk           BIGINT  NOT NULL,
    source_file      VARCHAR NOT NULL,
    source_resource  VARCHAR NOT NULL,
    source_id        VARCHAR NOT NULL,
    loaded_at        TIMESTAMP DEFAULT current_timestamp
);
"""

# CDM 5.4 `condition_occurrence`. Required fields: condition_occurrence_id,
# person_id, condition_concept_id, condition_start_date, condition_type_concept_id.
CONDITION_OCCURRENCE = """
CREATE TABLE IF NOT EXISTS condition_occurrence (
    condition_occurrence_id       BIGINT  NOT NULL,
    person_id                     BIGINT  NOT NULL,
    condition_concept_id          INTEGER NOT NULL,
    condition_start_date          DATE    NOT NULL,
    condition_start_datetime      TIMESTAMP,
    condition_end_date            DATE,
    condition_end_datetime        TIMESTAMP,
    condition_type_concept_id     INTEGER NOT NULL,
    condition_status_concept_id   INTEGER,
    stop_reason                   VARCHAR,
    provider_id                   BIGINT,
    visit_occurrence_id           BIGINT,
    visit_detail_id               BIGINT,
    condition_source_value        VARCHAR,
    condition_source_concept_id   INTEGER,
    condition_status_source_value VARCHAR
);
"""

# CDM 5.4 `observation`. Receives coded facts whose concept belongs to the
# Observation domain, regardless of which FHIR resource type carried them.
OBSERVATION = """
CREATE TABLE IF NOT EXISTS observation (
    observation_id                BIGINT  NOT NULL,
    person_id                     BIGINT  NOT NULL,
    observation_concept_id        INTEGER NOT NULL,
    observation_date              DATE    NOT NULL,
    observation_datetime          TIMESTAMP,
    observation_type_concept_id   INTEGER NOT NULL,
    value_as_number               DOUBLE,
    value_as_string               VARCHAR,
    value_as_concept_id           INTEGER,
    qualifier_concept_id          INTEGER,
    unit_concept_id               INTEGER,
    provider_id                   BIGINT,
    visit_occurrence_id           BIGINT,
    visit_detail_id               BIGINT,
    observation_source_value      VARCHAR,
    observation_source_concept_id INTEGER,
    unit_source_value             VARCHAR,
    qualifier_source_value        VARCHAR,
    value_source_value            VARCHAR,
    observation_event_id          BIGINT,
    obs_event_field_concept_id    INTEGER
);
"""

ALL = [PERSON, CONDITION_OCCURRENCE, OBSERVATION, ETL_PROVENANCE]
