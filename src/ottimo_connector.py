"""
Ottimo placeholder connector.

Replace parse_placeholder_event() once the real Ottimo barcode log/API format is
known. Everything downstream should continue using the normalized barcode event.
"""

from datetime import datetime, timezone


EVENT_MAP = {
    "COMPLETE": "part_complete",
    "QC_OK": "qc_pass",
    "QC_FAIL": "qc_fail",
    "PACKED": "packed",
    "DISPATCH": "dispatched",
}


def parse_placeholder_event(payload: dict) -> dict:
    """
    Demo contract:
      {
        "barcode": "AA-GBR|Fixed Shelf",
        "event": "QC_OK",
        "station": "packing",
        "operator": "Amit",
        "ts": "..."
      }
    """
    barcode = payload.get("barcode") or ""
    parts = barcode.split("|", 1)
    return {
        "barcode": barcode,
        "job_name": payload.get("job_name") or (parts[0] if parts else None),
        "part_name": payload.get("part_name") or (parts[1] if len(parts) > 1 else None),
        "station": payload.get("station"),
        "event_type": EVENT_MAP.get(payload.get("event"), payload.get("event_type", "unknown")),
        "operator": payload.get("operator"),
        "source": "ottimo_placeholder",
        "raw_payload": payload,
        "ts": payload.get("ts") or datetime.now(timezone.utc).isoformat(),
        "notes": payload.get("notes"),
    }
