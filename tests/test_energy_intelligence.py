"""Gap-aware utility optimization from commissioned telemetry."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import energy_intelligence
import industrial_gateway
from db import init_db


def _reader(power_w, energy_kwh, power_factor=0.7):
    def read(profile, signals):
        values = {signal["key"]: industrial_gateway.SIGNAL_DEFINITIONS[signal["key"]]["simulation"]
                  for signal in signals}
        values.update({"power_w": power_w, "energy_kwh": energy_kwh,
                       "power_factor": power_factor})
        return values
    return read


def test_energy_intelligence_labels_source_cost_idle_waste_and_low_pf():
    conn = init_db(":memory:", check_same_thread=False)
    industrial_gateway.sync_defaults(conn)
    profile = next(item for item in industrial_gateway.snapshot(conn)["profiles"]
                   if item["profile_key"] == "elgi_1_energy")
    configured = industrial_gateway.update_profile(conn, profile["profile_key"], {
        "expected_version": profile["version"],
        "endpoint": "10.10.0.51",
        "poll_interval_s": 300,
        "settings": {**profile["settings"], "tariff_per_kwh": 10},
    })
    probe = industrial_gateway.probe_profile(
        conn, profile["profile_key"], reader=_reader(1000, 100), actor="test"
    )
    industrial_gateway.approve_run(
        conn, profile["profile_key"], probe["run_id"],
        expected_version=configured["version"], actor="test", enable=True,
    )
    start = datetime.now(timezone.utc) - timedelta(minutes=15)
    readings = [(100, 100.0), (1000, 100.03), (1000, 100.11), (6000, 100.2)]
    for index, (power, energy) in enumerate(readings):
        industrial_gateway.poll_profile(
            conn, profile["profile_key"], reader=_reader(power, energy),
            source_ts=(start + timedelta(minutes=index * 5)).isoformat(),
        )

    result = energy_intelligence.build(conn, hours=1)
    metrics = result["profiles"][0]
    assert metrics["energy_source"] == "cumulative_meter"
    assert metrics["energy_kwh"] == 0.2
    assert metrics["estimated_cost"] == 2.0
    assert metrics["idle_energy_share"] > 0.8
    assert metrics["coverage"] == 1.0
    assert {alert["code"] for alert in metrics["alerts"]} == {
        "idle_energy", "low_power_factor",
    }
    assert result["summary"]["alerts"] == 2
    conn.close()
