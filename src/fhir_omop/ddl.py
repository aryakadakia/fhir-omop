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

ALL = [PERSON, ETL_PROVENANCE]
