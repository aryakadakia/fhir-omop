`DatabaseConnector::connectDuckdb()` runs `INSTALL icu` but never `LOAD icu`. Since `LOAD` is per-connection and `INSTALL` persists, the ICU extension ends up installed but unloaded on every connection, and functions that depend on it remain unavailable.

###### --- fresh connection: installed, not loaded ---
``` r
> library(DBI)
> con <- dbConnect(duckdb::duckdb())
> dbGetQuery(con, "SELECT extension_name, installed, loaded
+                  FROM duckdb_extensions() WHERE extension_name = 'icu'")
  extension_name installed loaded
1            icu      TRUE  FALSE
> dbGetQuery(con, "SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x")
Error in `duckdb_result()`:
! rapi_prepare: Failed to prepare query
Binder Error: No function matches the given name and argument types '(DATE, INTERVAL)'.
```
###### --- same connection after LOAD ---
``` r
> dbExecute(con, "LOAD icu")
> dbGetQuery(con, "SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x")
           x
1 2026-09-04
```

#### Relevant lines:
https://github.com/OHDSI/DatabaseConnector/blob/128c84fa4f2fa254667dd17dc30210f7c1420a63/R/Connect.R#L863-L880

The guard queries `installed` rather than `loaded`. `INSTALL` writes to the DuckDB home directory and persists across sessions, so after the first successful install the branch is skipped on every subsequent connection and `LOAD` is never reached. `grep -r "LOAD icu"` finds no other call site in the package.

Encountered via `DataQualityDashboard::executeDqChecks()` against a DuckDB CDM, where `plausibleValueHigh` on `CDM_SOURCE.CDM_RELEASE_DATE` renders as `CURRENT_DATE + TO_DAYS(...)` and reports an error rather than a result.

#### Environment:
DatabaseConnector: 7.2.0
duckdb: 1.5.5
DataQualityDashboard: 2.8.9
R: 4.6.0, macOS
