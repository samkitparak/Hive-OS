# Remote Machine Commissioning

HIVE can commission Maestro machine agents centrally over Windows OpenSSH after
one local bootstrap on each machine PC. The bootstrap is the only unavoidable
per-machine step when no remote-management channel already exists.

## Security boundary

- The central installer creates one Ed25519 deployment identity under
  `C:\HIVE-OS\data\ssh` and restricts it to Administrators and SYSTEM.
- Only the public key is copied to a machine PC. The private key never leaves
  the central PC and is never stored in SQLite.
- The machine bootstrap enables Windows OpenSSH Server, adds the public key to
  `C:\ProgramData\ssh\administrators_authorized_keys`, fixes its ACL, and
  limits the inbound firewall rule to `LocalSubnet`.
- HIVE scans host public keys but will not trust one until an administrator
  compares and approves its SHA-256 fingerprint.
- Every live command uses key-only `BatchMode`, disables password and
  keyboard-interactive fallback, pins the approved host key, ignores user SSH
  configuration, and targets only private, loopback, or link-local addresses.
- SSH commands are argument arrays with an encoded PowerShell payload. HIVE
  does not invoke a command shell or interpolate user values into shell text.
- Host keys, fingerprints, endpoints, results, and bounded output tails are
  audited. Passwords, SSH private keys, and MQTT client private keys are not.
- Live install is enabled only when the central machine-agent cache passes its
  nested manifest, size, path-safety, and SHA-256 checks. There is no automatic
  network fallback on the machine PC.

Microsoft documents the Windows OpenSSH Server capability, automatic service,
and firewall setup in [Get started with OpenSSH for Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).
Its [key-management guide](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
specifies `administrators_authorized_keys` and the required administrator/SYSTEM
ACL. HIVE uses those Windows-native paths and key-authentication rules.

OpenSSH warns that an unverified `ssh-keyscan` result is vulnerable to a
machine-in-the-middle attack. HIVE therefore treats a scan only as a candidate
and requires physical fingerprint comparison before trust. See the
[OpenBSD ssh-keyscan manual](https://man.openbsd.org/ssh-keyscan.1).

## Central installation

`install-central.ps1` installs the Windows OpenSSH Client when needed, creates
the deployment identity, and places this folder on the public desktop:

```text
HIVE Machine Bootstrap/
  enable-hive-ssh.ps1
  hive-deploy.pub
  README.txt
```

The folder contains no private credential and can be moved by approved USB to
each Maestro PC.

## Machine bootstrap

On each machine PC, open Administrator PowerShell in the bootstrap folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\enable-hive-ssh.ps1 -PublicKeyPath .\hive-deploy.pub
```

Leave the printed host-key fingerprints visible. In HIVE **Setup > Remote Agent
Setup**, choose that machine, enter its static IP and an existing local
Administrator username, then:

1. Check port 22.
2. Scan the host fingerprint.
3. Compare one SHA-256 fingerprint with the machine screen.
4. Confirm the match and trust the host.
5. Authenticate and verify `is_admin: true`.
6. Enable live SSH and detect folders.
7. Save the detected log/CNC paths to site setup.
8. Install the agent.
9. Fetch the log and confirm the MQTT heartbeat in Diagnostics.

## Live installation transaction

For a live install HIVE creates a fresh machine-specific MQTT enrollment bundle
in memory, writes it to a temporary central file, copies it over strict SCP,
and runs the bundled installer. The bundle contains the Python 3.12 x64 offline
installer, agent-only wheelhouse, requirements, code, certificates, and a nested
hash manifest. Python dependencies are installed with `--no-index --find-links`.
The remote ZIP and extraction directory are removed in a PowerShell `finally`
block. The local temporary file is deleted when the request finishes.

If transfer or installation fails after certificate issuance, HIVE revokes the
orphaned enrollment and records the failed run. A successful install verifies a
staging installation, checks mutual-TLS MQTT, atomically replaces
`C:\HIVE-Agent`, and starts the `HIVE Agent - <machine>` SYSTEM scheduled task.
If activation fails, the previous agent directory and task are restored.

## Recovery and host-key changes

- A changed host key always fails closed. Confirm the PC was rebuilt or its
  OpenSSH keys intentionally rotated, revoke the old trust, rescan, physically
  compare, and approve the new fingerprint.
- Revoke host trust when a PC is retired or its administrative ownership
  changes. This removes it from HIVE's strict `known_hosts` file.
- Set `HIVE_SSH_IDENTITY_FILE` only when the site deliberately manages the
  deployment identity outside `C:\HIVE-OS\data\ssh\id_ed25519`.
- Back up the deployment identity with HIVE's other protected central data.
