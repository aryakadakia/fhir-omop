# example/

One synthetic patient with a complete clinical trail — a visit at a named
hospital with a named clinician, a diagnosis, a lab result, a prescription and
a vaccine. Running the ETL over this directory exercises every stage of the
pipeline including the derived tables, which is what the quick start in the
top-level README does.

`../fixtures/` is a different thing: deliberately awkward resources used to
pin edge-case behaviour (a patient with no birth date, a gender code with no
OMOP concept, year-only precision). Those patients have no clinical events,
so running the ETL over them produces no observation periods — correctly, and
the `every_person_has_observation_period` check reports it.
