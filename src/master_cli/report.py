from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .master import AudioStats
from .mix import MixSettings, Stem


def write_report(
    path: str | Path,
    *,
    command: str,
    output: str | Path,
    sample_rate: int,
    input_stats: AudioStats | None,
    output_stats: AudioStats,
    mix_settings: MixSettings | None = None,
    master_settings: Any | None = None,
    stems: list[Stem] | None = None,
    reference_analysis: Any | None = None,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "command": command,
        "output": str(output),
        "sample_rate": sample_rate,
        "input_stats": serialize(input_stats),
        "output_stats": serialize(output_stats),
        "mix_settings": serialize(mix_settings),
        "master_settings": serialize(master_settings),
        "stems": serialize(stems),
        "reference_analysis": serialize(reference_analysis),
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def default_report_path(output: str | Path) -> Path:
    output_path = Path(output)
    return output_path.with_suffix(output_path.suffix + ".report.json")


def serialize(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return serialize(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    return value
