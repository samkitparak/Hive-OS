# Factory Machine Readiness

HIVE OS 0.28 turns machine connection work into an evidence-led, resumable
commissioning mission. It does not assume that a model name proves an installed
PC, protocol, log folder, register map, or license.

## Readiness sequence

Each of the 15 machines advances through six independent gates:

1. **Machine passport** - nameplate/controller identity, physical location, and
   telemetry decision are confirmed by a named person.
2. **Site endpoint** - a real static host and network zone are recorded when the
   selected strategy needs a network.
3. **Transport reachability** - fingerprint-pinned SSH, a verified industrial
   profile, or a HIVE read-only TCP probe supplies site evidence.
4. **Data contract** - a machine agent or industrial mapping is separately
   analyzed and approved; operator-evidence machines need an accepted study.
5. **Live reporting** - a representative run produces a signal no older than
   three minutes.
6. **Cycle calibration** - accepted linked cycles promote a validated active
   model through the existing learning gate.

A passing TCP check is not a data contract. A confirmed passport is not proof
that a signal is correct. A research profile is never site evidence.

## Commissioning missions

Open **Commission > Machine links**, select a machine, and start its mission.
HIVE builds the runbook from the selected telemetry strategy and separates work
that can be completed before travel from evidence that requires the factory.

- Maestro missions verify the deployment identity and offline agent payload,
  then route through fingerprint trust, folder discovery, installation,
  machine-specific log approval, heartbeat proof, and calibration.
- Industrial missions verify that a draft read-only profile exists, then route
  through endpoint capture, transport tests, immutable contract approval, a
  representative poll, and calibration.
- Operator-evidence missions skip network gates and route through timed field
  observations, review, live-run evidence, and calibration.

Missions are append-audited, versioned, and resumable. Operators can start,
pause, resume, or cancel them, but cannot tick evidence gates complete. HIVE
reconciles every step from the underlying passport, transport, contract, signal,
and cycle-model records. A telemetry-strategy change is reported as mission
drift and requires a new mission instead of silently changing the old plan.

## Operator workflow

Open **Commission > Machine links**.

1. Download the hashed field pack before travel. It contains a prefilled atomic
   inventory CSV, probe plan, commissioning plan in CSV and JSON, an
   official-source register, and one checklist per machine.
2. At the machine, copy facts from the nameplate, HMI About screen, managed
   switch, and actual Windows folders. Save inventory while work is incomplete.
3. Import the completed CSV in preview mode. HIVE rejects unknown columns,
   duplicate machines, stale passport versions, and any invalid row before an
   atomic apply.
4. Confirm each passport only while physically matched to the asset. Confirmed
   network strategies require a real endpoint.
5. Preview the proposed transport check, then explicitly run it. HIVE limits
   targets to private, loopback, or link-local addresses and opens TCP only.
6. Continue through **Machine logs**, **Industrial I/O**, or **Field evidence**
   to approve the real data contract and collect calibration evidence.

Passport updates use optimistic versions and append a SHA-256-fingerprinted
audit event. They never create machine events, cycle observations, cycle models,
or industrial contract versions.

## Researched starting paths

| Machines | Starting path | What must be proven on site |
|---|---|---|
| Gabbiani PT80, Morbidelli CX100/N100 | Maestro PC candidate | Installed Windows/controller generation, readable production logs, exact paths, SSH fingerprint |
| Stefani KD, DMC60, DMC90, Superfici | Low-confidence Maestro PC candidate | Whether the installed generation has a Windows PC and usable local logs; otherwise use energy/operator evidence |
| Action E, Nova SI400, Varie Osama | Operator evidence | Any real counter, barcode, dry contact, vendor option, or external sensing path |
| Sergiani GS120 | Operator evidence first; OPC-UA discovery fallback | Siemens panel/PLC order numbers and licensed, read-only OPC-UA or Modbus contract |
| Elgi 1/2, Aarco 1/2 | Separate energy meter | Purchased meter identity, wiring, IP/unit ID, exact manufacturer register map, observed thresholds |

Action E and Nova SI400 are deliberately not seeded as Maestro agents. Published
material describes manual operation and does not establish a networked Maestro
PC. Add them only if the installed asset proves otherwise.

## Offline release verification

Before copying a Windows release to USB, verify its outer sidecar, manifest,
every file, nested agent payload, wheelhouses, installer references, target, and
application version on macOS, Linux, or Windows:

```bash
PYTHONPATH=src python src/offline_release.py \
  release/HIVE-OS-0.32.0-offline.zip --version 0.32.0
```

Static verification does not prove Windows service startup, firewall behavior,
upgrade, or rollback. Rehearse those on a disposable Windows 11 x64 PC or VM
before travel. The factory install is one click after extraction, but each real
machine still needs identity, network, transport, contract, and live-signal
commissioning.

## Research boundary

The workflow follows the asset-inventory, segmentation, remote-access control,
and test-before-production principles in [NIST SP 800-82 Rev. 3](https://csrc.nist.gov/pubs/sp/800/82/r3/final)
and the joint [CISA OT asset inventory guidance](https://www.cisa.gov/sites/default/files/2025-08/joint-guide-foundations-for-OT-cybersecurity-asset-inventory-guidance_508c.pdf).
These references guide the process; they do not establish that any particular
HAEEV machine supports an interface.

The built-in profiles summarize official public material and expose the links
inside Machine Links. Key sources include [SCM IoT Solution](https://www.scmgroup.com/en_US/scmwood/news-events/news/maestro-connect-becomes-iot-solution.n235793.html),
[Gabbiani P/PT](https://www.scmgroup.com/products/docs/sezionatura-gabbiani/gabbiani-p-pt/gabbiani_p-pt_rev03_nov23_EN.pdf),
[Morbidelli N100](https://www.scmgroup.com/products/docs/CDL/morbidelli%20n/Catalogo%20morbidelli%20n100_EN.pdf),
[Stefani KD](https://www.scmgroup.com/en_GB/scmwood/products/edge-banders-squaring-edge-banders.c863/single-sided-automatic-edge-banders.865/stefani-kd.42159),
[Superfici Compact XL](https://www.scmgroup.com/en/scmwood/products/finishing-systems.c920/spraying-machines.929/compact-xl.148218),
[Action E](https://www.scmgroup.com/fr_FR/scmwood/products/assemblage.c42150/cadreuses-pour-meubles.862/action-fl---action-p.61512),
[Nova SI400](https://www.scmgroup.com/en_US/scmwood/products/joinery-machines.c884/sliding-table-saws.896/nova-si-400.586),
[Sergiani GS120](https://shop.scmgroup.com/scmwood-na/us/en/Catalogs/Catalog/PRESSES/Presses/Presses---hot-presses/sergiani-gs-120/p/SERGIANIGS120_COMP2),
[ELGi Neuron 4](https://www.elgi.com/us/press-coverage/elgi-expands-eg-sp-super-premium-series-air-compressors-to-unlock-energy-savings-in-industrial-applications/),
and [Cabinet Vision requirements](https://hexagon.com/products/product-groups/computer-aided-manufacturing-cad-cam-software/cabinet-vision/system-requirements).
