# HIVE OS Alarm Management

## Purpose

HIVE creates an alert only when a detected abnormal condition needs a timely
operator response. Informational state changes remain events. This distinction
follows the ISA-18 principle that an alarm requires operator action and the
IEC 62682 definition of alarm systems for abnormal process and equipment
conditions.

Alerts are advisory workflow records. They never write to a PLC, start or stop
a machine, or change an approved production schedule.

## Rationalized Rules

| Rule | Condition | Default response owner | Initial response |
|---|---|---|---|
| Machine alarm | A distinct machine/alarm-code occurrence in the last 24 hours | Maintenance lead | 15 min |
| Open downtime | Unclosed downtime lasting at least 15 minutes | Shift supervisor | 15 min |
| Maintenance attention | Urgent or overdue high-priority work | Maintenance lead | 240 min |
| Condition trigger | Latest active commissioned condition threshold | Maintenance lead | 30 min |
| Spare shortage | Required work-order spare below required quantity | Maintenance lead | 240 min |
| Execution exception | Unresolved station execution deviation | Shift supervisor | 30 min |
| Route exception | Unresolved actual-versus-route deviation | Production planner | 60 min |
| Quality recurrence | Three matching failures or rework records in eight hours | Quality lead | 120 min |
| Procurement delivery failure | Failed ERP/outbox document | Procurement lead | 240 min |
| Industrial profile failure | Enabled, verified profile with a polling error | Site engineer | 30 min |
| Diagnostic review | Open high-confidence root-cause case | Reliability lead | 240 min |
| Dispatch unacknowledged | Dispatched station job unacknowledged for 15 minutes | Shift supervisor | 15 min |

The fixed catalog and thresholds live in `src/alerting.py`. Change them only
through a reviewed management-of-change decision after observing real factory
rates and operator workload.

## Lifecycle and Deduplication

Each alert has a stable `alert_key` for its condition and an `evidence_token`
for the latest distinct occurrence. Repeated synchronization of unchanged
evidence updates `last_seen_at` but does not increment occurrences or send a
duplicate notification.

Statuses are:

- `open`: response is required.
- `acknowledged`: a named person owns the response.
- `snoozed`: response is intentionally deferred for 5 to 1,440 minutes with a reason.
- `resolved`: the condition was disposed or cleared.

New evidence reopens acknowledged or snoozed alerts. Stateful conditions that
are resolved while still active reopen on the next synchronization. Event-style
machine alarms and quality recurrences remain resolved until a new evidence
token arrives. Conditions absent from the next synchronization resolve as
`source-sync` with an immutable lifecycle event.

Every operator action requires a real actor name. Updates use an expected
version to prevent one browser from overwriting a newer decision.

## Escalation

Each rule assigns an initial response deadline. An unacknowledged open alert
advances to escalation level 1 when the deadline is missed and level 2 after a
second response interval. Each level produces one lifecycle event and at most
one delivery per destination. A severity increase reopens the response clock.

The site must assign real roles, backups, and shift-specific response times
before enabling automatic synchronization.

## Webhook Contract

HIVE currently exposes one generic webhook channel. A site gateway can translate
it to Teams, Slack, email, SMS, WhatsApp, an incident platform, or an ERP without
putting vendor credentials in HIVE.

Delivery uses a structured CloudEvents 1.0 JSON envelope with stable event IDs.
Requests include:

- `Content-Type: application/cloudevents+json`
- `X-HIVE-Delivery`: idempotency key
- `X-HIVE-Signature: sha256=<hex>` when `secret_env` is configured

Only the environment-variable name is stored. The secret remains in the Windows
machine environment. Payloads are capped at 64 KiB, redirects are rejected,
public or DNS-named endpoints require HTTPS, and plain HTTP is accepted only for
literal private/loopback addresses or `localhost`.

Failed requests retry at most five times with bounded exponential backoff.
Receivers must deduplicate on `X-HIVE-Delivery` or the CloudEvents `id`.

## Commissioning Sequence

1. Review and rationalize the 12 rule classes against actual roles and OEM alarms.
2. Save a disabled webhook contract with a named operator.
3. Run **Simulate**. This builds the exact envelope and headers without network I/O.
4. Put the HMAC secret in the central PC environment, if required.
5. Run **Send live test**. This is an explicit real network request.
6. Confirm the receiver validates the signature and idempotency key.
7. Enable the verified destination and save it.
8. Dispatch pending state once and reconcile the delivery history.
9. Enable automatic condition sync; observe one full shift for nuisance alarms.
10. Enable automatic dispatch only after the shift supervisor signs off.

Changing an endpoint or secret reference clears verification and disables the
destination. Automatic sync and dispatch are both off by default after install.

## Standards Basis

- [ISA alarm management life cycle](https://www.isa.org/intech-home/2018/march-april/features/alarm-management-life-cycle)
- [ISA-18 alarm management standards series](https://www.isa.org/standards-and-publications/isa-standards/isa-18-series-of-standards)
- [IEC 62682:2022 alarm systems for process industries](https://webstore.iec.ch/en/publication/65543)
- [NIST SP 800-61 Rev. 3 incident response guidance](https://csrc.nist.gov/pubs/sp/800/61/r3/final)
- [CloudEvents specification](https://github.com/cloudevents/spec)
- [OWASP SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
