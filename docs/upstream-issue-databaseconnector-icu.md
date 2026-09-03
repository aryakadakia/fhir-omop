# Upstream issue draft — OHDSI/DatabaseConnector

Found while running the Data Quality Dashboard against a DuckDB-backed CDM.
File at <https://github.com/OHDSI/DatabaseConnector/issues>.

---

**Title:** `connectDuckdb()` installs the ICU extension but never loads it, so date arithmetic still fails

---

**Body:**

## Summary

`connectDuckdb()` checks whether DuckDB's ICU extension is `installed` and runs
`INSTALL icu` if not, but never runs `LOAD icu`. Since `LOAD` is per-connection
and `INSTALL` persists to the DuckDB home directory, the extension is installed
but unloaded on every connection after the first. Functions that depend on it —
including `DATE + INTERVAL` — remain unavailable, and the warning that hints at
the problem stops appearing once the extension is installed.

## Reproduction

```r
library(DBI)
con <- dbConnect(duckdb::duckdb())
dbGetQuery(con, "SELECT extension_name, installed, loaded
                 FROM duckdb_extensions() WHERE extension_name = 'icu'")
#>   extension_name installed loaded
#> 1            icu      TRUE  FALSE

dbGetQuery(con, "SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x")
#> Error: Binder Error: No function matches the given name and argument
#>        types '(DATE, INTERVAL)'.

dbExecute(con, "LOAD icu")
dbGetQuery(con, "SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x")
#>            x
#> 1 2026-09-04
```

The same SQL succeeds in the DuckDB CLI, which autoloads extensions.

## Cause

In `connectDuckdb()`:

```r
isInstalled <- querySql(connection = connection,
  sql = "SELECT installed FROM duckdb_extensions() WHERE extension_name = 'icu';")[1, 1]
if (!isInstalled) {
  warning("The ICU extension of DuckDB is not installed. Attempting to install it.")
  tryCatch(executeSql(connection, "INSTALL icu"), error = function(e) { ... })
}
```

Two things combine:

1. The guard is on `installed`, not `loaded`. `INSTALL` writes to the DuckDB home
   directory and persists across sessions, so after the first successful install
   this branch never runs again.
2. Even on the first run the branch only installs. `LOAD` is per-connection and
   is never called.

The net effect is that ICU is never loaded on any connection.

## Impact

Any generated SQL relying on ICU-provided date arithmetic fails to compile.
Concretely, `DataQualityDashboard::executeDqChecks()` against a DuckDB CDM
reports an error rather than a result for `plausibleValueHigh` on
`CDM_SOURCE.CDM_RELEASE_DATE`, whose template renders as
`CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))`.

It is a single check out of 2,533 in that particular run, so the practical
impact is small — but the failure surfaces as a SQL binder error inside a
generated query, which reads like a problem with the user's CDM rather than a
missing extension.

## Suggested fix

Guard on `loaded` rather than `installed`, and load unconditionally after
installing:

```r
ext <- querySql(connection = connection,
  sql = "SELECT installed, loaded FROM duckdb_extensions() WHERE extension_name = 'icu';")
if (!ext$INSTALLED[1]) {
  # ... existing INSTALL logic ...
}
tryCatch(executeSql(connection, "LOAD icu"), error = function(e) {
  warning("Could not load the ICU extension of DuckDB. ",
          "Some date and time functionality will not be available.")
})
```

## Environment

```
DatabaseConnector 7.2.0
DataQualityDashboard 2.8.9
duckdb (R) 1.5.5
R 4.6.0, macOS
```
