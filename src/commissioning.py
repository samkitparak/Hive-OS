"""Offline log evidence analysis and safe replay for factory commissioning."""

import re
import sqlite3
from collections import Counter
from typing import Optional

import event_pipeline
from maestro_agent import _build_payload, _extract_cnc_file, _parse_log_line


TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")


def analyze_log(machine_key: str, log_text: str, *, max_lines: int = 10000,
                include_events: bool = False) -> dict:
    lines = log_text.splitlines()[:max_lines]
    parsed = []
    unknown = []
    token_counts: Counter[str] = Counter()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        item = _parse_log_line(line)
        if item:
            item["line_number"] = number
            parsed.append(item)
        else:
            if len(unknown) < 12:
                unknown.append({"line_number": number, "text": line[:300]})
            token_counts.update(TOKEN.findall(line.upper()))

    event_counts = Counter(item["event_type"] for item in parsed)
    recognized = len(parsed)
    nonempty = sum(1 for line in lines if line.strip())
    timestamps = [item["ts"] for item in parsed]
    ordering_issues = sum(1 for left, right in zip(timestamps, timestamps[1:]) if right < left)
    cnc_matches = sum(1 for item in parsed if _extract_cnc_file(item))
    cycle_pairs = min(event_counts["cycle_start"], event_counts["cycle_end"])
    recognition_rate = recognized / nonempty if nonempty else 0.0

    checks = [
        {"key": "lines", "label": "Log contains evidence", "passed": nonempty >= 10,
         "detail": f"{nonempty} non-empty lines"},
        {"key": "recognition", "label": "Parser recognizes the format", "passed": recognition_rate >= 0.7,
         "detail": f"{round(recognition_rate * 100)}% recognized"},
        {"key": "cycles", "label": "Cycle boundaries are visible", "passed": cycle_pairs >= 3,
         "detail": f"{cycle_pairs} complete cycle pairs"},
        {"key": "ordering", "label": "Timestamps are ordered", "passed": ordering_issues == 0,
         "detail": f"{ordering_issues} ordering issues"},
        {"key": "programs", "label": "CNC program identity is visible", "passed": cnc_matches >= 1,
         "detail": f"{cnc_matches} events with .xcs/.ard files"},
    ]
    ready = all(check["passed"] for check in checks[:4])
    result = {
        "machine_key": machine_key,
        "ready_to_replay": ready,
        "total_lines": len(lines),
        "nonempty_lines": nonempty,
        "recognized_lines": recognized,
        "recognition_rate": round(recognition_rate, 3),
        "event_counts": dict(event_counts),
        "first_timestamp": timestamps[0] if timestamps else None,
        "last_timestamp": timestamps[-1] if timestamps else None,
        "ordering_issues": ordering_issues,
        "cnc_matches": cnc_matches,
        "checks": checks,
        "unknown_samples": unknown,
        "candidate_keywords": [
            {"token": token, "count": count}
            for token, count in token_counts.most_common(15)
        ],
    }
    if include_events:
        result["_events"] = parsed
    return result


def replay_log(conn: sqlite3.Connection, machine_key: str, log_text: str,
               *, persist: bool = False, site_timezone: str = "Asia/Kolkata") -> dict:
    analysis = analyze_log(machine_key, log_text, include_events=True)
    events = analysis.pop("_events")
    if not persist:
        return {**analysis, "persisted": False, "ingestion": {}}
    if not analysis["ready_to_replay"]:
        return {**analysis, "persisted": False,
                "ingestion": {"rejected": analysis["recognized_lines"]},
                "reason": "Log evidence has not passed the replay readiness checks"}

    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not machine:
        raise ValueError(f"Unknown machine '{machine_key}'")

    dispositions: Counter[str] = Counter()
    for item in events:
        payload = _build_payload(machine_key, item, _extract_cnc_file(item))
        payload["source"] = "commissioning_replay"
        result = event_pipeline.ingest_event(
            conn, payload, site_timezone=site_timezone,
            received_at=event_pipeline.canonical_timestamp(payload["ts"], site_timezone),
        )
        dispositions[result["status"]] += 1
    return {**analysis, "persisted": True, "ingestion": dict(dispositions)}
