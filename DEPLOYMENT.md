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
- Installs Python, Node.js, and Mosquitto if missing
- Creates the Python virtual environment and dashboard build
- Asks for the Cabinet Vision export folder
- Creates a startup task
- Configures a LAN MQTT listener for machine agents
- Serves the built dashboard and API from one FastAPI process on port `8000`
- Opens ports `8000` and `1883` on the factory LAN
- Creates a public-desktop HIVE OS shortcut
- Writes logs to `C:\HIVE-OS\logs`

After installation, run:

```powershell
.\deploy\windows\test-hive-install.ps1
```

This checks the dashboard, API, MQTT port, install folder, logs folder, and
startup task.

Firewall rules are limited to `LocalSubnet`; HIVE does not expose its API or
MQTT broker intentionally beyond the factory LAN.

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

## Capturing Maestro Evidence

On a machine PC, run:

```powershell
.\deploy\windows\capture-maestro-logs.ps1
```

This creates `hive-maestro-sample.txt` on the desktop with the latest 300 log
lines. Capture samples while starting a cycle, ending a cycle, causing an alarm,
and scanning a completed part.

## Removal

Run `deploy\windows\uninstall-hive.ps1`. It removes startup tasks and firewall
rules but deliberately preserves database, configuration, and logs.
