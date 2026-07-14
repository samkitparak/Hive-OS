# HIVE OS Industrial Telemetry Gateway

HIVE commissions Modbus TCP, OPC-UA, and MQTT telemetry as read-only signal
contracts. Simulation verifies decoding and downstream behavior, but it cannot
approve a profile. Production polling starts only after a real device/sample
probe passes and a named operator approves that exact endpoint and signal map.

## Runtime Contract

1. A profile binds one endpoint or MQTT topic to one machine or site source.
2. Draft signals declare the normalized meaning, source address/node/path,
   data type, unit, scaling, plausible range, and whether the signal is required.
3. A real probe reads only the declared signals and records a fingerprint and
   normalized results.
4. Approval creates an immutable contract version. Editing endpoint, signal,
   threshold, credential reference, or polling settings invalidates approval.
5. Polling writes raw normalized samples, latest values, and hourly rollups.
6. Power/running evidence is debounced before it creates `state_on`,
   `state_idle`, or `state_off` machine events for OEE.
7. Raw samples expire after the profile retention period; hourly evidence is
   retained for long-term energy and optimization analysis.

There are no Modbus write functions, OPC-UA writes, or controller methods in
the gateway API.

## Offsite Profiles

HIVE ships disabled candidates for Elgi 1/2, Aarco 1/2, the Sergiani GS 120,
and generic MQTT telemetry. The four utility candidates use the official
Eastron SDM630 v1.8 input-register map:

| Signal | Zero-based address | Function | Encoding |
|---|---:|---|---|
| Total system power | 52 | 04 input registers | IEEE-754 float32, two registers |
| Total import energy | 72 | 04 input registers | IEEE-754 float32, two registers |
| Average line current | 46 | 04 input registers | IEEE-754 float32, two registers |
| Average L-N voltage | 42 | 04 input registers | IEEE-754 float32, two registers |
| Total system power factor | 62 | 04 input registers | IEEE-754 float32, two registers |
| Frequency | 70 | 04 input registers | IEEE-754 float32, two registers |

These addresses are valid only if the installed meter is an SDM630 with the
documented register order. If a different meter is purchased, replace the draft
signals with its exact manufacturer register list before probing.

## Modbus TCP Commissioning

1. Record meter manufacturer, exact model, firmware, wiring mode, static IP,
   TCP port, and unit ID. Photograph the label and network settings.
2. Put the central HIVE PC and meters on the isolated factory OT network. Do not
   route port 502 to the internet.
3. Run `deploy/windows/test-industrial-network.ps1` from the central PC.
4. Open **Commission > Industrial I/O**, choose the meter, and enter endpoint,
   unit ID, polling interval, and the manufacturer signal map.
5. Run **Simulate**. This checks the software path only.
6. Run **Probe device** and compare voltage/current/power with the physical
   display while the machine is off, idle, and loaded.
7. Set idle/on thresholds between observed bands. Keep two-sample debounce at
   first; increase it if contactor or load transients create chatter.
8. Approve and enable, then use **Poll now** twice and confirm state transition,
   latest telemetry, diagnostics, and hourly rollup counts.

Modbus PDU addresses are zero-based. A manual that displays register `30053`
may call for PDU address `52`; never type the display reference blindly.

## Sergiani OPC-UA Commissioning

OPC-UA availability on the Sergiani/Siemens controller is a candidate, not a
confirmed fact. Confirm the CPU/HMI model, firmware, licensed server feature,
endpoint, and available security policies with Sergiani or the controls
integrator first.

1. Create a read-only OPC-UA role with access only to required variables.
2. Generate a unique HIVE application certificate and have the controller trust
   it. Use `SignAndEncrypt`; `Basic256Sha256` is the conservative initial choice
   unless the server offers a stronger supported policy.
3. Put credential material in one Windows machine environment variable as JSON:

   ```json
   {
     "username": "hive_observer",
     "password": "site-managed-secret",
     "security_string": "Basic256Sha256,SignAndEncrypt,C:\\HIVE-OS\\certs\\hive.der,C:\\HIVE-OS\\certs\\hive-key.pem,C:\\HIVE-OS\\certs\\sergiani-server.der"
   }
   ```

4. Store only that environment variable name in HIVE, then restart the HIVE
   scheduled task so it receives the variable.
5. Enter `opc.tcp://host:port`, select the matching policy, save, and run
   **Browse nodes**.
6. Map only observed nodes for running, alarm, recipe/program, cycle counter,
   temperature, and pressure. Probe, compare with the HMI, approve, and enable.
7. If OPC-UA is unavailable, commission documented read-only Modbus registers.
   If neither is available, use an energy meter for state and an operator scan
   for cycle completion; do not modify PLC logic during HIVE commissioning.

`SecurityPolicy None` is available only for isolated discovery without username
or password. HIVE rejects credentials over that policy.

## MQTT JSON Commissioning

1. Define a narrow topic filter and normalized JSON paths.
2. Paste one real message and topic into **Validate sample**.
3. HIVE stores the payload fingerprint and normalized values, not the raw sample.
4. Approve and enable. QoS 1 can redeliver, so HIVE fingerprints each
   profile/signal/timestamp/value/contract combination before writing.

## Diagnostics and Recovery

- `GET /api/industrial/snapshot` returns profile, contract, latest-value, state,
  and fleet power/energy status.
- `GET /api/industrial/profiles/{key}/telemetry` returns hourly evidence.
- `GET /api/energy/intelligence` returns coverage-labeled energy, loaded/idle/
  standby use, load factor, tariff cost, and power-factor opportunities.
- A required-signal failure marks the poll failed without inventing a value.
- Disable a failing profile, correct and save the draft, run a new real probe,
  and approve a new contract. Existing contracts and telemetry remain auditable.
- Raw telemetry retention defaults to 30 days; hourly rollups remain available
  for optimization and utility baselines.

Primary references:

- [Modbus Application Protocol V1.1b3](https://modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf)
- [Modbus specifications and security protocol](https://www.modbus.org/modbus-specifications)
- [Eastron SDM630 Modbus protocol v1.8](https://www.eastroneurope.com/images/uploads/products/protocol/SDM630_MODBUS_Protocol.pdf)
- [OPC-UA Part 2 security model](https://reference.opcfoundation.org/specs/OPC-10000-2/1)
- [Siemens S7-1500 communication manual](https://support.industry.siemens.com/cs/attachments/59192925/s71500_communication_function_manual_en-US_en-US.pdf)
- [MQTT 5.0 OASIS standard](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html)
- [PyModbus client documentation](https://pymodbus.readthedocs.io/en/v3.11.1/source/client.html)
- [asyncua client library](https://github.com/FreeOpcUa/opcua-asyncio)
