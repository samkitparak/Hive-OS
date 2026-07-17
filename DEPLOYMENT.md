# HIVE OS Deployment

## What "one-click install" means

For the current prototype, download or copy the HIVE OS folder onto the target
Windows PC and run one PowerShell installer as Administrator.

The installer needs internet access once to install Python, Node.js, Mosquitto,
and package dependencies. After installation, HIVE OS starts automatically when
Windows boots and can operate on the factory LAN without internet.

An offline USB installer should be prepared after confirming the exact Windows
versions and CPU architectures used by the factory PCs.

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
- Configures a LAN MQTT listener for machine agents
- Serves the built dashboard and API on central-PC localhost port `8000`
- Opens only MQTT port `1883` to the factory LAN
- Creates a random, one-time administrator bootstrap token
- Creates a public-desktop HIVE OS shortcut
- Writes logs to `C:\HIVE-OS\logs`

The installer prints the one-time administrator token. Open the desktop HIVE OS
shortcut on the central PC, create the first administrator, and then store the
password in the site's approved password manager. HIVE deletes the token after
the first administrator exists. Create a second administrator for recovery.

After installation, run:

```powershell
.\deploy\windows\test-hive-install.ps1
```

This checks the dashboard, public health and access-control status, protected API
reachability, MQTT port,
ODBC driver, Modbus/OPC-UA client libraries, install folder, logs folder, and
startup task.

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
The MQTT listener is limited to `LocalSubnet`; anonymous MQTT is a temporary
commissioning boundary and must be replaced with broker credentials and TLS
before untrusted devices can join the factory network.

## Maestro Machine PCs

Copy the HIVE OS folder to each machine PC and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\install-machine-agent.ps1
```

It asks for:

- Machine key, such as `morbidelli_cx100`
- Central HIVE/CV PC IP address
- Maestro log folder

The agent is installed at `C:\HIVE-Agent`, starts with Windows, and emits a
heartbeat even while the machine is idle.

For repeatable installs, pass values directly. The installer auto-detects common
Maestro log locations when `LogFolder` is omitted, verifies MQTT reachability,
and submits the latest log sample to central HIVE for dry-run parser analysis:

```powershell
.\deploy\windows\install-machine-agent.ps1 `
  -MachineKey morbidelli_cx100 `
  -BrokerHost 192.168.1.20
```

The agent always writes the latest sample to
`C:\HIVE-Agent\logs\commissioning-sample.txt`. Automatic submission is optional
and requires an HTTPS HIVE URL plus a one-time-visible integration key created
under **Access > Machine keys**:

```powershell
.\deploy\windows\install-machine-agent.ps1 `
  -MachineKey morbidelli_cx100 `
  -BrokerHost 192.168.1.20 `
  -CentralApiBase https://hive.factory.example
```

The installer prompts for the integration key without echoing it or placing it
in PowerShell history.

## Diagnostics

Open the dashboard and click **Setup** first to fill in site-specific values:

- Cabinet Vision export folder
- MQTT broker host and port
- Maestro machine PC IPs
- Maestro log folders and CNC folders
- Energy meter Modbus IPs and thresholds

Every save writes a timestamped backup under `config/backups/` and replaces the
active YAML atomically.

The **Remote Agent Setup** area provides a safe deployment scaffold. It can
probe whether SSH port 22 is reachable and preview folder discovery, agent
installation, restart, and log-fetch actions. Remote command execution and
credential persistence remain disabled until an SSH or WinRM adapter is chosen
and enabled explicitly.

Then open **Diagnostics**. The view shows both live system health and deployment
package readiness:

- Database, MQTT bridge, and Cabinet Vision watcher status
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
