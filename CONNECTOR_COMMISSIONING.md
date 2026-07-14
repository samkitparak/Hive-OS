# HIVE OS Connector Commissioning

HIVE keeps vendor formats at one controlled boundary. A connector cannot import
production data until a real sample has been analyzed, its mapping has been
approved by a named person, and the connector has been enabled.

## Safety and Data Rules

- Cabinet Vision access is read-only. HIVE only issues `SELECT` against one
  allowlisted table or view; it does not accept SQL text from the dashboard.
- Put the ODBC connection string in a Windows machine environment variable.
  The HIVE database stores only that variable's name.
- Prefer Windows/Active Directory authentication and grant the HIVE service
  account `SELECT` only on the approved view.
- Commissioning files are not retained. HIVE stores a SHA-256 fingerprint,
  column names, aggregate validation results, issues, and the approved mapping.
- Mapping versions are immutable. New evidence creates a new approved version.
- Exact import batches are fingerprinted and cannot write twice.

## Cabinet Vision SQL

Public Cabinet Vision material confirms that the product uses SQL Server, but
does not publish a stable job/part table contract. Ask the Cabinet Vision
reseller or site DBA to expose a view such as `dbo.HiveJobParts` with one row per
part. Do not point HIVE at undocumented internal tables unless the exact version
and fields have been approved.

1. Install HIVE on the central/Cabinet Vision PC. The installer adds Microsoft
   ODBC Driver 18 and Python `pyodbc`.
2. Create a least-privilege login or service account with `SELECT` on the view.
3. Set a machine environment variable, for example:

   ```powershell
   [Environment]::SetEnvironmentVariable(
     "HIVE_CV_SQL_CONNECTION",
     "Driver={ODBC Driver 18 for SQL Server};Server=CV-PC\\INSTANCE;Database=CabinetVision;Trusted_Connection=Yes;Encrypt=Yes;TrustServerCertificate=Yes;ApplicationIntent=ReadOnly",
     "Machine"
   )
   ```

4. Restart the `HIVE OS Central` scheduled task so it receives the variable.
5. Open **Commission > Data connectors > Cabinet Vision SQL**.
6. Enter `HIVE_CV_SQL_CONNECTION`, the approved view, and a row limit. Save,
   then run **Test metadata**.
7. Export 10-100 representative rows as CSV or JSON and select the file.
8. Analyze, inspect every required/optional field mapping, and re-analyze after
   changes. Approve and enable only when all rows pass.
9. Run **Sync now** and compare HIVE job/part counts with Cabinet Vision.

Recommended view fields match HIVE's normalized contract:

```text
job_name, client_name, room_name, job_date, part_name, material, length_mm,
width_mm, thickness_mm, qty, cnc_file_back, cnc_file_front, has_cnc
```

## Ottimo Barcode

1. Obtain a representative Ottimo event export or API response containing
   scans from arrival, completion, QC pass/fail, packing, and dispatch.
2. Open **Commission > Data connectors > Ottimo barcode** and select the CSV or
   JSON file.
3. Map the barcode, event type, timestamp, station, operator, and external event
   ID where available.
4. Map each observed vendor event value to a HIVE event. Re-analyze until every
   record passes, then approve and enable.
5. Import the sample once and reconcile counts with Ottimo. HIVE does not retain
   the raw sample payload.
6. During live API work, feed the same approved row shape to the connector import
   endpoint; downstream identity, route, quality, packing, and dispatch logic
   remains unchanged.

## SCM Maestro

1. Capture a log while powering on, running at least five cycles, stopping, and
   producing an alarm where safe.
2. Open **Commission > Machine logs**, choose the machine and captured file, and
   analyze it.
3. Confirm recognition, cycle pairing, timestamp order, and program identity.
4. Approve the parser evidence explicitly. Replay history only after approval.
5. Repeat for each distinct Maestro version or machine log format before its
   live agent is enabled.

## Audit and Recovery

`GET /api/connectors/snapshot` exposes profiles, active mapping versions, and
recent runs. A failed sample records issues but no raw records. Disable a
connector immediately if source behavior changes, analyze a fresh sample, then
approve the new mapping version. Existing imports and mapping history remain
auditable.

Primary references:

- [Cabinet Vision system requirements](https://hexagon.com/products/product-groups/computer-aided-manufacturing-cad-cam-software/cabinet-vision/system-requirements)
- [Cabinet Vision xReporting](https://hexagon.com/products/cabinet-vision-xreporting)
- [SCM Maestro digital systems](https://www.scmgroup.com/en_IN/scmwood/products/maestro-digital-systems/software.c102273/automatic.102285)
- [Microsoft SQL Server security best practices](https://learn.microsoft.com/en-us/sql/relational-databases/security/sql-server-security-best-practices?view=sql-server-ver17)
- [Microsoft ODBC Driver 18](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server?view=sql-server-ver17)
