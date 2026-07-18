# Remote Machine Commissioning

HIVE can commission a Maestro machine agent centrally over Windows OpenSSH after
one local bootstrap on a passport-confirmed Windows machine PC. Do not bootstrap
SSH from a model-name assumption. Action E, Nova SI400, Varie Osama, utilities,
and Sergiani default to non-SSH evidence until their installed controllers prove
otherwise. Start in **Commission > Machine links** and confirm the passport first.

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
5. Enable live SSH and select **Commission agent**.
6. If HIVE finds more than one valid Maestro log folder, select the real folder
   and resume the same run.
7. If the agent is waiting for its first MQTT signal, select **Verify heartbeat**
   after the scheduled task has had a few seconds to connect.

The commissioning transaction authenticates the administrator context,
discovers folders, saves the selected host and paths with a configuration
backup, installs the agent, verifies its task/configuration/log, and checks for
a fresh central heartbeat. Every stage is persisted. A browser refresh or an
ambiguous folder does not lose the run, and a healthy agent already using the
selected log folder is not reinstalled. The individual folder, install,
restart, and log actions remain under **Advanced actions** for repair work.

Commissioning requires a confirmed machine passport whose telemetry strategy
is `maestro_agent`. A successful SSH command is not treated as a successful
installation: the run remains `awaiting_signal` until HIVE receives fresh
machine evidence. Microsoft documents the scheduled-task state queried by this
verification in [Get-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/get-scheduledtask).

The physical host-fingerprint comparison remains deliberately manual. This is
the one step a central one-click workflow cannot safely infer.

## Transaction states

| State | Meaning | Next action |
|---|---|---|
| `running` | A bounded commissioning stage is executing | Wait for the request |
| `needs_input` | More than one or no standard log folder was proven | Select a discovered folder or verify a custom path |
| `awaiting_signal` | Remote install is healthy but no fresh central signal exists | Verify heartbeat after the agent connects |
| `succeeded` | Remote state and fresh central evidence both passed | Continue the factory-readiness mission |
| `failed` | A stage failed closed and retained its evidence | Correct the reported cause and start a new run |

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

The orchestration endpoints are:

- `POST /remote-setup/commission-agent` for a side-effect-free preview.
- `POST /remote-setup/commission-agent/live` for an administrator-only live run
  or `needs_input` resume.
- `POST /remote-setup/commission-agent/{run_id}/verify` to recheck a run waiting
  for its first central signal.

## Recovery and host-key changes

- A changed host key always fails closed. Confirm the PC was rebuilt or its
  OpenSSH keys intentionally rotated, revoke the old trust, rescan, physically
  compare, and approve the new fingerprint.
- Revoke host trust when a PC is retired or its administrative ownership
  changes. This removes it from HIVE's strict `known_hosts` file.
- Set `HIVE_SSH_IDENTITY_FILE` only when the site deliberately manages the
  deployment identity outside `C:\HIVE-OS\data\ssh\id_ed25519`.
- Back up the deployment identity with HIVE's other protected central data.
