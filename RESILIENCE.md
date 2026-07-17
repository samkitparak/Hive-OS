# HIVE OS Offline Installation and Recovery

HIVE OS 0.21 can be installed and upgraded on a disconnected factory LAN. A
release is assembled on an internet-connected Windows x64 build PC before
travel, then carried on approved removable media. The target central PC does
not need Node.js, npm, PyPI, winget, or internet access.

## Build the offline release

Use Python 3.12 and Node.js on the build PC. Download the current official x64
installers for Python 3.12, Mosquitto, and Microsoft ODBC Driver 18. OpenSSH
Win64 can also be supplied for target PCs that do not already have the Windows
OpenSSH Client.

```powershell
.\deploy\windows\build-offline-bundle.ps1 `
  -Version 0.21.0 `
  -PythonInstaller C:\ReleaseInputs\python-3.12.exe `
  -MosquittoInstaller C:\ReleaseInputs\mosquitto.exe `
  -OdbcInstaller C:\ReleaseInputs\msodbcsql18.msi `
  -OpenSshArchive C:\ReleaseInputs\OpenSSH-Win64.zip
```

The builder runs dashboard lint/build, downloads Windows-compatible binary
wheels, packages the prebuilt dashboard, hashes every bundled file, and emits:

- `release\HIVE-OS-0.21.0-offline.zip`
- `release\HIVE-OS-0.21.0-offline.zip.sha256`

Keep both files. Compare the ZIP SHA-256 after copying it to USB and again on
the factory PC.

## Offline central installation

Extract the ZIP, open the extracted folder, and double-click
`Install-HIVE-OS.cmd`. Approve the Administrator prompt when Windows asks.

The installer verifies every file before running a vendor installer. It then
installs Python, Mosquitto, ODBC, and optional OpenSSH from the bundle, installs
Python packages with `--no-index --find-links`, copies the prebuilt dashboard,
provisions MQTT/SSH identities, and registers the supervised startup task.

This is one-click after the ZIP has been extracted. A vendor-installer failure,
missing wheel, architecture mismatch, or hash mismatch stops installation
instead of falling back to the internet.

## Verified backups

From the dashboard, an administrator can open **Diagnostics** and select
**Back up now**. From Administrator PowerShell:

```powershell
C:\HIVE-OS\deploy\windows\backup-hive.ps1
```

Backups default to `C:\HIVE-Backups`. Each ZIP contains:

- a consistent SQLite snapshot created through the online backup API;
- site configuration;
- the MQTT certificate authority and issued identities;
- the SSH deployment identity and trusted-host state;
- local `.env` configuration when one exists;
- an internal file manifest, an external SHA-256 sidecar, and a verification record.

Transient runtime PID files, logs, caches, and previous backups are excluded.
The archive is ACL-restricted to Local System and Administrators by the Windows
script. Copy recovery backups to the site's approved encrypted secondary media.

## Restore

Restore must run locally because the database and trust material cannot be
replaced safely by the process that currently has them open.

```powershell
C:\HIVE-OS\deploy\windows\restore-hive.ps1 `
  -BackupPath C:\HIVE-Backups\hive-backup-....zip
```

The script verifies both manifests and SQLite integrity, creates a fresh
pre-restore backup, extracts into a safe staging directory, stops the HIVE task
and all tracked child processes, replaces state, restarts HIVE, and polls the
health endpoint. If health validation fails, it automatically puts the original
database, configuration, and trust material back and restarts the old state.

## Offline upgrade and rollback

For an existing 0.21 or later installation, place the release ZIP and its
`.sha256` sidecar together, then run:

```powershell
C:\HIVE-OS\deploy\windows\upgrade-hive.ps1 `
  -BundlePath D:\HIVE-OS-0.21.0-offline.zip
```

To upgrade a 0.20 installation, extract the 0.21 release and run the upgrader
included at its root; it carries the new backup implementation itself:

```powershell
D:\HIVE-OS-0.21.0-offline\upgrade-hive.ps1 `
  -BundlePath D:\HIVE-OS-0.21.0-offline
```

The upgrader verifies the release, creates and verifies a pre-upgrade backup,
stages a separate application directory, installs dependencies from the local
wheelhouse, migrates a copied database, swaps directories only after staging
succeeds, and performs a live health check. A failed check automatically swaps
the previous release back. The failed candidate is retained as
`C:\HIVE-OS.failed` for diagnosis.

## Technical basis

SQLite documents that its [Online Backup API](https://www.sqlite.org/backup.html)
creates a consistent snapshot while other clients continue to operate. The
backup is subsequently checked with
[`PRAGMA integrity_check`](https://www.sqlite.org/pragma.html#pragma_integrity_check).
Python package installation follows pip's documented
[`pip download`](https://pip.pypa.io/en/stable/cli/pip_download/) and
[`--no-index --find-links`](https://pip.pypa.io/en/stable/cli/pip_install/)
workflow. Windows lifecycle control uses Microsoft's documented
[`Stop-ScheduledTask`](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/stop-scheduledtask)
and `Start-ScheduledTask` commands.
