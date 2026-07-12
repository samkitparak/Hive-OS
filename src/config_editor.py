"""Read and update HIVE OS site configuration."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml


EDITABLE_KEYS = {
    "mqtt",
    "cv_watch_folder",
    "energy_defaults",
    "energy_meters",
    "maestro_agents",
}


def load(cfg_path: Path) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    return {
        "mqtt": cfg.get("mqtt", {}),
        "cv_watch_folder": cfg.get("cv_watch_folder"),
        "energy_defaults": cfg.get("energy_defaults", {}),
        "energy_meters": cfg.get("energy_meters", []),
        "maestro_agents": cfg.get("maestro_agents", []),
    }


def _backup(cfg_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = cfg_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{cfg_path.stem}.{stamp}.yaml"
    shutil.copy2(cfg_path, backup_path)
    return backup_path


def _clean_string(value):
    if value == "":
        return None
    return value


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return _clean_string(value)


def save(cfg_path: Path, payload: dict) -> dict:
    current = yaml.safe_load(cfg_path.read_text()) or {}
    editable = {key: _clean(payload[key]) for key in EDITABLE_KEYS if key in payload}
    updated = {**current, **editable}
    backup_path = _backup(cfg_path)
    temp_path = cfg_path.with_suffix(f"{cfg_path.suffix}.tmp")
    temp_path.write_text(
        yaml.safe_dump(updated, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    temp_path.replace(cfg_path)
    result = load(cfg_path)
    result["backup_path"] = str(backup_path)
    result["saved_at"] = datetime.now(timezone.utc).isoformat()
    return result
