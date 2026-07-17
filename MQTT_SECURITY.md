# MQTT Machine Trust

HIVE production deployments use mutual TLS on TCP `8883`. The central installer
creates a site-local certificate authority, a broker certificate containing the
central PC address, and a separate central bridge identity. Mosquitto requires a
valid client certificate and uses its common name as the MQTT username.

Mosquitto documents `require_certificate`, `use_identity_as_username`, TLS
certificate files, CRLs, and ACL files in its
[configuration reference](https://mosquitto.org/man/mosquitto-conf-5.html).
Its [TLS guide](https://mosquitto.org/man/mosquitto-tls-7.html) also recommends
distinct subjects for CA, server, and client certificates. HIVE follows both
requirements.

## Topic Boundary

Each machine certificate common name is exactly its HIVE machine key. The broker
ACL uses the authenticated username placeholder to permit writes only to:

```text
hive/machines/<certificate-common-name>/events
```

The central bridge identity can read all machine event topics. A machine cannot
publish as another machine and cannot subscribe to factory events.

## Enrollment

1. Install HIVE on the central PC and create the first administrator.
2. Open **Access control > Device certificates**.
3. Select a machine and issue its enrollment ZIP.
4. Move that ZIP to the matching machine PC, extract it, and run
   `install-machine-agent.ps1` as Administrator.

The ZIP contains one client private key. HIVE records the signed certificate,
serial number, fingerprint, issuer, and expiry in SQLite, but never stores the
private client key in SQLite. The response is marked `no-store`.

The agent builds a hostname-verifying TLS context and presents its client
certificate before connecting. Paho documents that `tls_set_context()` must be
configured before connection and that reconnect delays can use exponential
backoff in its [Python client reference](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html).

## Revocation And Rotation

Issue a replacement ZIP before expiry and install it on the machine PC. Multiple
valid certificates may overlap during rotation. Revoke the old certificate only
after the replacement reports successfully.

Revocation adds the certificate serial to the site CRL. Run
`deploy\windows\restart-hive-mqtt.ps1` as Administrator on the central PC so
Mosquitto reloads the CRL. Diagnostics and Access control show a restart warning
until that happens. Compromise of one machine key does not require rotating
other machines.

Back up `C:\HIVE-OS\data\mqtt-pki` with the central HIVE backup. Access to that
directory is restricted to Windows Administrators and SYSTEM. Loss of the CA
private key requires reprovisioning the broker and re-enrolling every machine.
