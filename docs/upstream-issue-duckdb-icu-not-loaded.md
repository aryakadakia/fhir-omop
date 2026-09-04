`DatabaseConnector::connectDuckdb()` runs `INSTALL icu` but never `LOAD icu`. Since `LOAD` is per-connection and `INSTALL` persists, the ICU extension ends up installed but unloaded on every connection, and functions that depend on it remain unavailable.

###### --- connection from DatabaseConnector: installed, not loaded ---
``` r
> library(DatabaseConnector)
> cd <- createConnectionDetails(dbms = "duckdb", server = tempfile(fileext = ".duckdb"))
> con <- connect(cd)
Connecting using DuckDB driver
> querySql(con, "SELECT value FROM duckdb_settings() WHERE name = 'autoload_known_extensions'")
  value
1  true
> querySql(con, "SELECT extension_name, installed, loaded
+                FROM duckdb_extensions() WHERE extension_name = 'icu'")
  extension_name installed loaded
1            icu      TRUE  FALSE
> querySql(con, "SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x")
Error executing SQL:
Binder Error: No function matches the given name and argument types '(DATE, INTERVAL)'.
LINE 1: SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x
```
###### --- same connection after LOAD ---
``` r
> executeSql(con, "LOAD icu")
> querySql(con, "SELECT CAST((CURRENT_DATE + TO_DAYS(CAST(1 AS INTEGER))) AS DATE) AS x")
           x
1 2026-09-05
```
###### --- LOAD does not carry to a new connection, INSTALL does ---
``` r
> c2 <- dbConnect(duckdb::duckdb(), dbdir = f)   # same file, fresh connection
> dbGetQuery(c2, "SELECT installed, loaded FROM duckdb_extensions()
+                 WHERE extension_name = 'icu'")
  installed loaded
1      TRUE  FALSE
```

#### Relevant lines:
https://github.com/OHDSI/DatabaseConnector/blob/128c84fa4f2fa254667dd17dc30210f7c1420a63/R/Connect.R#L863-L880

The guard queries `installed` rather than `loaded`. `INSTALL` writes to the DuckDB home directory and persists across sessions, so after the first successful install the branch is skipped on every subsequent connection and `LOAD` is never reached. `LOAD icu` does not appear anywhere else in the package.

Note that `autoload_known_extensions` is `true` on the connection, so DuckDB's extension autoloading does not cover this case.

Encountered via `DataQualityDashboard::executeDqChecks()` against a DuckDB CDM, where `plausibleValueHigh` on `CDM_SOURCE.CDM_RELEASE_DATE` renders as `CURRENT_DATE + TO_DAYS(...)` and reports an error rather than a result.

#### Environment:
DatabaseConnector: 7.2.0
duckdb: 1.5.5
DataQualityDashboard: 2.8.9
R: 4.6.0, macOS
