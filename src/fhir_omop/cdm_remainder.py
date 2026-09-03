"""The CDM 5.4 tables this ETL does not populate.

The OHDSI tooling — Data Quality Dashboard, Achilles, ATLAS — expects a
complete CDM 5.4 schema. A missing table is an error to those tools, whereas an
EMPTY table is a legitimate state they report on: "this site records no
specimens" is a real and common answer.

Creating them empty is therefore not a workaround. It is the difference between
a CDM that tooling refuses to read and one it reads and characterises honestly.

Definitions follow https://ohdsi.github.io/CommonDataModel/cdm54.html
"""

from __future__ import annotations

# ---------------------------------------------------------------- clinical
DEATH = """
CREATE TABLE IF NOT EXISTS death (
    person_id               BIGINT  NOT NULL,
    death_date              DATE    NOT NULL,
    death_datetime          TIMESTAMP,
    death_type_concept_id   INTEGER,
    cause_concept_id        INTEGER,
    cause_source_value      VARCHAR,
    cause_source_concept_id INTEGER
);
"""

PROCEDURE_OCCURRENCE = """
CREATE TABLE IF NOT EXISTS procedure_occurrence (
    procedure_occurrence_id     BIGINT  NOT NULL,
    person_id                   BIGINT  NOT NULL,
    procedure_concept_id        INTEGER NOT NULL,
    procedure_date              DATE    NOT NULL,
    procedure_datetime          TIMESTAMP,
    procedure_end_date          DATE,
    procedure_end_datetime      TIMESTAMP,
    procedure_type_concept_id   INTEGER NOT NULL,
    modifier_concept_id         INTEGER,
    quantity                    INTEGER,
    provider_id                 BIGINT,
    visit_occurrence_id         BIGINT,
    visit_detail_id             BIGINT,
    procedure_source_value      VARCHAR,
    procedure_source_concept_id INTEGER,
    modifier_source_value       VARCHAR
);
"""

DEVICE_EXPOSURE = """
CREATE TABLE IF NOT EXISTS device_exposure (
    device_exposure_id            BIGINT  NOT NULL,
    person_id                     BIGINT  NOT NULL,
    device_concept_id             INTEGER NOT NULL,
    device_exposure_start_date    DATE    NOT NULL,
    device_exposure_start_datetime TIMESTAMP,
    device_exposure_end_date      DATE,
    device_exposure_end_datetime  TIMESTAMP,
    device_type_concept_id        INTEGER NOT NULL,
    unique_device_id              VARCHAR,
    production_id                 VARCHAR,
    quantity                      INTEGER,
    provider_id                   BIGINT,
    visit_occurrence_id           BIGINT,
    visit_detail_id               BIGINT,
    device_source_value           VARCHAR,
    device_source_concept_id      INTEGER,
    unit_concept_id               INTEGER,
    unit_source_value             VARCHAR,
    unit_source_concept_id        INTEGER
);
"""

VISIT_DETAIL = """
CREATE TABLE IF NOT EXISTS visit_detail (
    visit_detail_id                BIGINT  NOT NULL,
    person_id                      BIGINT  NOT NULL,
    visit_detail_concept_id        INTEGER NOT NULL,
    visit_detail_start_date        DATE    NOT NULL,
    visit_detail_start_datetime    TIMESTAMP,
    visit_detail_end_date          DATE    NOT NULL,
    visit_detail_end_datetime      TIMESTAMP,
    visit_detail_type_concept_id   INTEGER NOT NULL,
    provider_id                    BIGINT,
    care_site_id                   BIGINT,
    visit_detail_source_value      VARCHAR,
    visit_detail_source_concept_id INTEGER,
    admitted_from_concept_id       INTEGER,
    admitted_from_source_value     VARCHAR,
    discharged_to_source_value     VARCHAR,
    discharged_to_concept_id       INTEGER,
    preceding_visit_detail_id      BIGINT,
    parent_visit_detail_id         BIGINT,
    visit_occurrence_id            BIGINT  NOT NULL
);
"""

NOTE = """
CREATE TABLE IF NOT EXISTS note (
    note_id                   BIGINT  NOT NULL,
    person_id                 BIGINT  NOT NULL,
    note_date                 DATE    NOT NULL,
    note_datetime             TIMESTAMP,
    note_type_concept_id      INTEGER NOT NULL,
    note_class_concept_id     INTEGER NOT NULL,
    note_title                VARCHAR,
    note_text                 VARCHAR,
    encoding_concept_id       INTEGER NOT NULL,
    language_concept_id       INTEGER NOT NULL,
    provider_id               BIGINT,
    visit_occurrence_id       BIGINT,
    visit_detail_id           BIGINT,
    note_source_value         VARCHAR,
    note_event_id             BIGINT,
    note_event_field_concept_id INTEGER
);
"""

NOTE_NLP = """
CREATE TABLE IF NOT EXISTS note_nlp (
    note_nlp_id                BIGINT  NOT NULL,
    note_id                    BIGINT  NOT NULL,
    section_concept_id         INTEGER,
    snippet                    VARCHAR,
    "offset"                   VARCHAR,
    lexical_variant            VARCHAR NOT NULL,
    note_nlp_concept_id        INTEGER,
    note_nlp_source_concept_id INTEGER,
    nlp_system                 VARCHAR,
    nlp_date                   DATE    NOT NULL,
    nlp_datetime               TIMESTAMP,
    term_exists                VARCHAR,
    term_temporal              VARCHAR,
    term_modifiers             VARCHAR
);
"""

SPECIMEN = """
CREATE TABLE IF NOT EXISTS specimen (
    specimen_id                 BIGINT  NOT NULL,
    person_id                   BIGINT  NOT NULL,
    specimen_concept_id         INTEGER NOT NULL,
    specimen_type_concept_id    INTEGER NOT NULL,
    specimen_date               DATE    NOT NULL,
    specimen_datetime           TIMESTAMP,
    quantity                    DOUBLE,
    unit_concept_id             INTEGER,
    anatomic_site_concept_id    INTEGER,
    disease_status_concept_id   INTEGER,
    specimen_source_id          VARCHAR,
    specimen_source_value       VARCHAR,
    unit_source_value           VARCHAR,
    anatomic_site_source_value  VARCHAR,
    disease_status_source_value VARCHAR
);
"""

FACT_RELATIONSHIP = """
CREATE TABLE IF NOT EXISTS fact_relationship (
    domain_concept_id_1 INTEGER NOT NULL,
    fact_id_1           BIGINT  NOT NULL,
    domain_concept_id_2 INTEGER NOT NULL,
    fact_id_2           BIGINT  NOT NULL,
    relationship_concept_id INTEGER NOT NULL
);
"""

# ----------------------------------------------------------- health system
LOCATION = """
CREATE TABLE IF NOT EXISTS location (
    location_id           BIGINT  NOT NULL,
    address_1             VARCHAR,
    address_2             VARCHAR,
    city                  VARCHAR,
    state                 VARCHAR,
    zip                   VARCHAR,
    county                VARCHAR,
    location_source_value VARCHAR,
    country_concept_id    INTEGER,
    country_source_value  VARCHAR,
    latitude              DOUBLE,
    longitude             DOUBLE
);
"""

CARE_SITE = """
CREATE TABLE IF NOT EXISTS care_site (
    care_site_id                  BIGINT  NOT NULL,
    care_site_name                VARCHAR,
    place_of_service_concept_id   INTEGER,
    location_id                   BIGINT,
    care_site_source_value        VARCHAR,
    place_of_service_source_value VARCHAR
);
"""

PROVIDER = """
CREATE TABLE IF NOT EXISTS provider (
    provider_id                 BIGINT  NOT NULL,
    provider_name               VARCHAR,
    npi                         VARCHAR,
    dea                         VARCHAR,
    specialty_concept_id        INTEGER,
    care_site_id                BIGINT,
    year_of_birth               INTEGER,
    gender_concept_id           INTEGER,
    provider_source_value       VARCHAR,
    specialty_source_value      VARCHAR,
    specialty_source_concept_id INTEGER,
    gender_source_value         VARCHAR,
    gender_source_concept_id    INTEGER
);
"""

# ------------------------------------------------------------------ economics
PAYER_PLAN_PERIOD = """
CREATE TABLE IF NOT EXISTS payer_plan_period (
    payer_plan_period_id          BIGINT  NOT NULL,
    person_id                     BIGINT  NOT NULL,
    payer_plan_period_start_date  DATE    NOT NULL,
    payer_plan_period_end_date    DATE    NOT NULL,
    payer_concept_id              INTEGER,
    payer_source_value            VARCHAR,
    payer_source_concept_id       INTEGER,
    plan_concept_id               INTEGER,
    plan_source_value             VARCHAR,
    plan_source_concept_id        INTEGER,
    sponsor_concept_id            INTEGER,
    sponsor_source_value          VARCHAR,
    sponsor_source_concept_id     INTEGER,
    family_source_value           VARCHAR,
    stop_reason_concept_id        INTEGER,
    stop_reason_source_value      VARCHAR,
    stop_reason_source_concept_id INTEGER
);
"""

COST = """
CREATE TABLE IF NOT EXISTS cost (
    cost_id                   BIGINT  NOT NULL,
    cost_event_id             BIGINT  NOT NULL,
    cost_domain_id            VARCHAR NOT NULL,
    cost_type_concept_id      INTEGER NOT NULL,
    currency_concept_id       INTEGER,
    total_charge              DOUBLE,
    total_cost                DOUBLE,
    total_paid                DOUBLE,
    paid_by_payer             DOUBLE,
    paid_by_patient           DOUBLE,
    paid_patient_copay        DOUBLE,
    paid_patient_coinsurance  DOUBLE,
    paid_patient_deductible   DOUBLE,
    paid_by_primary           DOUBLE,
    paid_ingredient_cost      DOUBLE,
    paid_dispensing_fee       DOUBLE,
    payer_plan_period_id      BIGINT,
    amount_allowed            DOUBLE,
    revenue_code_concept_id   INTEGER,
    revenue_code_source_value VARCHAR,
    drg_concept_id            INTEGER,
    drg_source_value          VARCHAR
);
"""

# -------------------------------------------------------------------- derived
DOSE_ERA = """
CREATE TABLE IF NOT EXISTS dose_era (
    dose_era_id         BIGINT  NOT NULL,
    person_id           BIGINT  NOT NULL,
    drug_concept_id     INTEGER NOT NULL,
    unit_concept_id     INTEGER NOT NULL,
    dose_value          DOUBLE  NOT NULL,
    dose_era_start_date DATE    NOT NULL,
    dose_era_end_date   DATE    NOT NULL
);
"""

EPISODE = """
CREATE TABLE IF NOT EXISTS episode (
    episode_id                  BIGINT  NOT NULL,
    person_id                   BIGINT  NOT NULL,
    episode_concept_id          INTEGER NOT NULL,
    episode_start_date          DATE    NOT NULL,
    episode_start_datetime      TIMESTAMP,
    episode_end_date            DATE,
    episode_end_datetime        TIMESTAMP,
    episode_parent_id           BIGINT,
    episode_number              INTEGER,
    episode_object_concept_id   INTEGER NOT NULL,
    episode_type_concept_id     INTEGER NOT NULL,
    episode_source_value        VARCHAR,
    episode_source_concept_id   INTEGER
);
"""

EPISODE_EVENT = """
CREATE TABLE IF NOT EXISTS episode_event (
    episode_id                BIGINT  NOT NULL,
    event_id                  BIGINT  NOT NULL,
    episode_event_field_concept_id INTEGER NOT NULL
);
"""

# ------------------------------------------------------------------- metadata
CDM_SOURCE = """
CREATE TABLE IF NOT EXISTS cdm_source (
    cdm_source_name                VARCHAR NOT NULL,
    cdm_source_abbreviation        VARCHAR NOT NULL,
    cdm_holder                     VARCHAR NOT NULL,
    source_description             VARCHAR,
    source_documentation_reference VARCHAR,
    cdm_etl_reference              VARCHAR,
    source_release_date            DATE    NOT NULL,
    cdm_release_date               DATE    NOT NULL,
    cdm_version                    VARCHAR,
    cdm_version_concept_id         INTEGER NOT NULL,
    vocabulary_version             VARCHAR NOT NULL
);
"""

METADATA = """
CREATE TABLE IF NOT EXISTS metadata (
    metadata_id            BIGINT  NOT NULL,
    metadata_concept_id    INTEGER NOT NULL,
    metadata_type_concept_id INTEGER NOT NULL,
    name                   VARCHAR NOT NULL,
    value_as_string        VARCHAR,
    value_as_concept_id    INTEGER,
    value_as_number        DOUBLE,
    metadata_date          DATE,
    metadata_datetime      TIMESTAMP
);
"""

SOURCE_TO_CONCEPT_MAP = """
CREATE TABLE IF NOT EXISTS source_to_concept_map (
    source_code             VARCHAR NOT NULL,
    source_concept_id       INTEGER NOT NULL,
    source_vocabulary_id    VARCHAR NOT NULL,
    source_code_description VARCHAR,
    target_concept_id       INTEGER NOT NULL,
    target_vocabulary_id    VARCHAR NOT NULL,
    valid_start_date        DATE    NOT NULL,
    valid_end_date          DATE    NOT NULL,
    invalid_reason          VARCHAR
);
"""

COHORT = """
CREATE TABLE IF NOT EXISTS cohort (
    cohort_definition_id BIGINT NOT NULL,
    subject_id           BIGINT NOT NULL,
    cohort_start_date    DATE   NOT NULL,
    cohort_end_date      DATE   NOT NULL
);
"""

COHORT_DEFINITION = """
CREATE TABLE IF NOT EXISTS cohort_definition (
    cohort_definition_id          INTEGER NOT NULL,
    cohort_definition_name        VARCHAR NOT NULL,
    cohort_definition_description VARCHAR,
    definition_type_concept_id    INTEGER NOT NULL,
    cohort_definition_syntax      VARCHAR,
    subject_concept_id            INTEGER NOT NULL,
    cohort_initiation_date        DATE
);
"""

# Vocabulary tables the loader may not have supplied (it loads only what the
# ETL reads). DQD checks these too.
DRUG_STRENGTH = """
CREATE TABLE IF NOT EXISTS drug_strength (
    drug_concept_id             INTEGER NOT NULL,
    ingredient_concept_id       INTEGER NOT NULL,
    amount_value                DOUBLE,
    amount_unit_concept_id      INTEGER,
    numerator_value             DOUBLE,
    numerator_unit_concept_id   INTEGER,
    denominator_value           DOUBLE,
    denominator_unit_concept_id INTEGER,
    box_size                    INTEGER,
    valid_start_date            DATE NOT NULL,
    valid_end_date              DATE NOT NULL,
    invalid_reason              VARCHAR
);
"""

ALL = [
    DEATH, PROCEDURE_OCCURRENCE, DEVICE_EXPOSURE, VISIT_DETAIL,
    NOTE, NOTE_NLP, SPECIMEN, FACT_RELATIONSHIP,
    LOCATION, CARE_SITE, PROVIDER,
    PAYER_PLAN_PERIOD, COST,
    DOSE_ERA, EPISODE, EPISODE_EVENT,
    CDM_SOURCE, METADATA, SOURCE_TO_CONCEPT_MAP,
    COHORT, COHORT_DEFINITION, DRUG_STRENGTH,
]
