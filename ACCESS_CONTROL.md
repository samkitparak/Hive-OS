# HIVE OS Access Control

HIVE uses local identities because the central PC must continue operating when
the internet is unavailable. Authentication is required by default. No default
username or password exists.

## First Administrator

The Windows installer creates `C:\HIVE-OS\data\hive-bootstrap.token`, restricts
it to SYSTEM and local Administrators, and prints it once. On the central PC:

1. Open `http://localhost:8000`.
2. Enter the token, a named administrator account, and a password.
3. Create a second administrator for recovery.

The token is deleted as soon as an active account exists. A copied token cannot
create another administrator later. If every administrator credential is lost,
restore a verified database backup or perform a controlled local recovery with
the system owner; do not add a shared backdoor account.

## Roles

| Role | Intended authority |
|---|---|
| Administrator | Accounts, commissioning, and all HIVE workflows |
| Supervisor | Operations, planning, quality, alerts, and improvement review |
| Planner | Planning, procurement, and optimization |
| Maintenance | Maintenance records and alerts |
| Quality | Quality/rework records and alerts |
| Operator | Production execution, downtime, scans, and alerts |
| Viewer | Read-only dashboard access |

Use one account per person and the smallest suitable role. An administrator
cannot deactivate or demote their own account. Role or active-state changes
revoke the affected user's browser sessions.

## Passwords and Sessions

- Passwords are 15-128 characters with no arbitrary composition rule.
- Common passwords and passwords containing account details are rejected.
- Passwords use Argon2id with 19,456 KiB memory, two iterations, and one lane.
- Five failed logins lock the account for 15 minutes; errors do not reveal
  whether a username exists.
- Browser sessions expire after 12 hours and are not persistent.
- Only the SHA-256 session-token hash is stored. The cookie is HttpOnly and
  SameSite Strict; the browser keeps the CSRF token in memory.
- Password reset revokes all target sessions. A self-service password change
  revokes every other session.

## Machine Credentials

Create a separate key under **Access control > HTTP keys** for each agent or tightly
related machine group. The plaintext key is shown once and only its SHA-256 hash
is stored. Machine keys have only `integration` permission: they can submit
approved ingestion payloads but cannot persist commissioning history, approve or
reconfigure mappings, read dashboards, or administer HIVE.
Revoke a key immediately if it appears in logs, screenshots, or chat.

## Transport and Audit

Credentials are accepted over HTTPS or central-PC loopback only. Remote plain
HTTP requests are rejected even with a valid cookie or bearer token. Keep the
FastAPI process on `127.0.0.1` and place an approved HTTPS reverse proxy or OT
gateway in front of it for LAN clients.

Every authenticated mutation creates an immutable access event containing the
principal, route, result, client address, and timestamp. For domain payloads,
HIVE replaces client-provided `actor`, `completed_by`, `inspector`, and
`operator` fields with the authenticated display name when those fields exist.

MQTT transport is independent of HTTP access control. The bundled anonymous
LAN listener is for controlled commissioning only; broker identities, ACLs, and
TLS must be commissioned before the network includes untrusted devices.

Remote machine setup uses a separate SSH trust boundary. Generating the central
deployment identity, approving or revoking a machine host key, and executing a
live agent install require an administrator. Other SSH diagnostics require the
commissioning permission. HIVE accepts no SSH passwords and stores no SSH
private key in SQLite. See `REMOTE_COMMISSIONING.md`.

## Standards Basis

- NIST SP 800-63B-4, Digital Identity Guidelines: https://doi.org/10.6028/NIST.SP.800-63b-4
- NIST SP 800-82 Rev. 3, Operational Technology Security: https://doi.org/10.6028/NIST.SP.800-82r3
- OWASP Password Storage Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
