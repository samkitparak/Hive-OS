# HIVE OS Deployment

## What "one-click install" means

For an internet-connected workshop PC, copy the HIVE OS folder and run the
PowerShell installer as Administrator. For the factory, build the verified
offline x64 release before travel, extract it on the target central PC, and run
`Install-HIVE-OS.cmd`. The offline target does not need Node.js, npm, PyPI,
winget, or internet access. See `RESILIENCE.md` for the release-input, hash,
backup, restore, upgrade, and rollback workflow.

## Central / Cabinet Vision PC

Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\install-central.ps1
```

The installer:

- Copies HIVE OS to `C:\HIVE-OS`
- Installs Python, Node.js, Mosquitto, and Microsoft ODBC Driver 18 if missing
- Creates the Python virtual environment and dashboard build
- Asks for the Cabinet Vision export folder
- Creates a startup task
- Runs the broker and backend under one tracked startup-task supervisor
- Creates a local MQTT certificate authority, broker identity, and central identity
- Configures a mutual-TLS MQTT listener for machine agents
- Serves the built dashboard and API on central-PC localhost port `8000`
- Opens only mutual-TLS MQTT port `8883` to the factory LAN
- Creates a random, one-time administrator bootstrap token
- Creates a public-desktop HIVE OS shortcut
- Writes logs to `C:\HIVE-OS\logs`
- Generates a protected Ed25519 machine-deployment identity
- Places a public-key-only **HIVE Machine Bootstrap** folder on the desktop

The offline installer performs the same provisioning from verified bundled
vendor installers, Python wheels, and a prebuilt dashboard. It also retains a
verified offline machine-agent runtime used by enrollment downloads and SSH
commissioning.

The installer prints the one-time administrator token. Open the desktop HIVE OS
shortcut on the central PC, create the first administrator, and then store the
password in the site's approved password manager. HIVE deletes the token after
the first administrator exists. Create a second administrator for recovery.

After installation, run:

```powershell
.\deploy\windows\test-hive-install.ps1
```

This checks the dashboard, public health and access-control status, protected API
reachability (including production forecast and schedule recovery), secure MQTT port,
ODBC driver, Modbus/OPC-UA client libraries, install folder, logs folder, and
startup task.

The forecast endpoint may correctly report commissioning instead of ready. Its
readiness depends on real routes, cycle models, resources, and contractual due
times after factory evidence begins arriving.

The recovery endpoint may correctly report `waiting_for_schedule`. Recovery
monitoring starts only after a production-ready scenario has been approved.

From the central PC on the factory OT network, run the read-only endpoint
preflight after entering real device endpoints:

```powershell
.\deploy\windows\test-industrial-network.ps1
```

It checks TCP reachability only. Protocol reads, signal validation, and approval
remain in **Commission > Industrial I/O**.

The dashboard/API is intentionally bound to localhost because credentials must
not cross plain HTTP. Before using HIVE from another PC or tablet, commission an
HTTPS reverse proxy or approved OT gateway and keep FastAPI itself on localhost.
The MQTT listener is limited to `LocalSubnet`, requires a client certificate,
and maps each certificate identity to one machine topic. The private certificate
authority key is restricted to Administrators and SYSTEM on the central PC.

## Maestro Machine PCs

When SSH is not already commissioned, copy the installer-created **HIVE Machine
Bootstrap** folder to the machine PC once and run locally as Administrator:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\enable-hive-ssh.ps1 -PublicKeyPath .\hive-deploy.pub
```

Compare the printed machine fingerprint with **Setup > Remote Agent Setup**,
approve it, authenticate, detect the real folders, and select **Install agent**.
HIVE then issues the MQTT enrollment, transfers it over strict SCP, runs the
installer, removes temporary package files, and records the result centrally.
The package includes Python 3.12 x64 and all agent wheels; the machine PC does
not need internet, winget, or PyPI.

The manual enrollment-ZIP workflow below remains available as an offline repair
path.

In HIVE, open **Access control > Device certificates**, choose a machine, and
download its enrollment ZIP. Extract that ZIP on the machine PC and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-machine-agent.ps1
```

An enrollment issued by an installed 0.22 offline release contains the machine
identity, central broker address, trusted CA, agent code, installer, Python
runtime, dependency wheels, and a hash manifest. The installer verifies every
runtime file before changing `C:\HIVE-Agent`, auto-detects the Maestro log folder,
checks mutual-TLS MQTT in a staging directory, and rolls back an existing agent
if activation fails. The private key is restricted to Administrators and SYSTEM.

The agent is installed at `C:\HIVE-Agent`, starts with Windows, and emits a
heartbeat even while the machine is idle.

For a repo-based repair install, select a current self-contained enrollment ZIP
in the file picker or pass it directly. The installer verifies secure MQTT reachability
and submits the latest log sample for dry-run parser analysis when optional HTTPS
API settings are supplied:

```powershell
.\deploy\windows\install-machine-agent.ps1 `
  -EnrollmentBundle C:\Install\hive-enrollment-morbidelli_cx100.zip
```

Older or development enrollment ZIPs do not contain the offline runtime and fail
closed by default. On an intentionally internet-connected repair PC only, allow
the legacy prerequisite path explicitly:

```powershell
.\deploy\windows\install-machine-agent.ps1 `
  -EnrollmentBundle C:\Install\legacy-enrollment.zip `
  -AllowOnlinePrerequisites
```

The agent always writes the latest sample to
`C:\HIVE-Agent\logs\commissioning-sample.txt`. Automatic submission is optional
and requires an HTTPS HIVE URL plus a one-time-visible HTTP integration key
created under **Access control > HTTP keys**. It is separate from the MQTT
device certificate:

```powershell
.\deploy\windows\install-machine-agent.ps1 `
  -EnrollmentBundle C:\Install\hive-enrollment-morbidelli_cx100.zip `
  -CentralApiBase https://hive.factory.example
```

The installer prompts for the integration key without echoing it or placing it
in PowerShell history.

When an administrator revokes a device certificate, run
`deploy\windows\restart-hive-mqtt.ps1` as Administrator on the central PC. This
restarts only the HIVE-owned broker process and clears the dashboard warning
after the new revocation list is active.

## Diagnostics

Open the dashboard and click **Setup** first to fill in site-specific values:

- Cabinet Vision export folder
- MQTT broker host, port, and central certificate paths
- Maestro machine PC IPs
- Maestro log folders and CNC folders
- Energy meter Modbus IPs and thresholds

Every save writes a timestamped backup under `config/backups/` and replaces the
active YAML atomically.

The **Remote Agent Setup** area defaults to preview. Live execution becomes
available only after the central key exists and an administrator has physically
verified and approved that machine's SSH fingerprint. Authentication, folder
discovery, installation, restart, and log retrieval are then key-only and
audited. Credentials are never accepted or persisted by the setup API.

See `REMOTE_COMMISSIONING.md` for trust, failure cleanup, host-key rotation, and
the exact site sequence.

Then open **Diagnostics**. The view shows both live system health and deployment
package readiness:

- Database, MQTT bridge, and Cabinet Vision watcher status
- Mutual-TLS initialization, active device certificates, and expiry warnings
- Approved and enabled factory connector counts
- Approved, polling, and failing industrial I/O profile counts
- Component shortages, verified remnants, and warehouse source issues
- Configured versus unconfigured machines
- Online, stale, and offline agents
- Last report age
- Configured host and log path
- Central installer, machine-agent installer, uninstaller, post-install checker,
  and Maestro capture script status
- Exact PowerShell commands to run on the central PC and machine PCs

Status thresholds:

- `online`: reported within 3 minutes
- `stale`: last report was 3-15 minutes ago
- `offline`: no report for more than 15 minutes
- `waiting`: configured but has never reported
- `not configured`: no usable connection configuration

For Cabinet Vision SQL, Ottimo, and Maestro evidence approval, follow
`CONNECTOR_COMMISSIONING.md`. SQL credentials are machine environment variables;
they are never written to HIVE configuration or SQLite.

For meters, PLCs, OPC-UA security, MQTT telemetry, signal maps, simulation, and
real probe approval, follow `INDUSTRIAL_TELEMETRY.md`.

For sheet counts, edge rolls, hardware BOMs, remnants, reservations, and
purchase suggestions, follow `WAREHOUSE_INTELLIGENCE.md`.

For alert rationalization, response roles, webhook verification, HMAC secrets,
and escalation drills, follow `ALARM_MANAGEMENT.md`. Put any webhook signing
secret in a Windows machine environment variable, restart the HIVE scheduled
task, and store only that variable's name in the Alert Center. Automatic alert
sync and external dispatch remain disabled after installation.

## Capturing Maestro Evidence

On a machine PC, run:

```powershell
.\deploy\windows\capture-maestro-logs.ps1
```

This creates `hive-maestro-sample.txt` on the desktop with the latest 300 log
lines. Capture samples while starting a cycle, ending a cycle, causing an alarm,
and scanning a completed part.

It can also submit the evidence directly to HIVE without importing it:

```powershell
.\deploy\windows\capture-maestro-logs.ps1 `
  -MachineKey morbidelli_cx100 `
  -CentralApiBase https://hive.factory.example
```

The capture command also prompts for the key without echoing it.

Machine credentials can analyze but cannot import history. After the checks
pass, review the evidence in **Commission** and import it with a named human
account. Replaying the same sample is safe because the ingestion gate suppresses
duplicates.

See `ACCESS_CONTROL.md` for roles, password/session behavior, service-key scope,
transport rules, and recovery practice.

## Removal

Run `deploy\windows\uninstall-hive.ps1`. It removes startup tasks and firewall
rules but deliberately preserves database, configuration, and logs.
