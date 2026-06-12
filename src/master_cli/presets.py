from __future__ import annotations

from copy import deepcopy
from typing import Any

from .master import MasterSettings, master_settings_from_mapping


PRESETS: dict[str, dict[str, Any]] = {
    "streaming": {
        "target_lufs": -14.0,
        "highpass_hz": 30.0,
        "stereo_width": 1.0,
        "compressor": {
            "threshold_db": -18.0,
            "ratio": 2.0,
            "attack_ms": 20.0,
            "release_ms": 160.0,
        },
        "limiter": {
            "ceiling_db": -1.0,
            "lookahead_ms": 5.0,
            "release_ms": 80.0,
            "oversample_factor": 4,
        },
    },
    "club": {
        "target_lufs": -9.0,
        "highpass_hz": 28.0,
        "stereo_width": 1.04,
        "eq": [
            {"type": "low_shelf", "frequency_hz": 90.0, "gain_db": 0.8, "q": 0.707},
            {"type": "high_shelf", "frequency_hz": 9500.0, "gain_db": 0.6, "q": 0.707},
        ],
        "compressor": {
            "threshold_db": -16.0,
            "ratio": 2.5,
            "attack_ms": 15.0,
            "release_ms": 130.0,
        },
        "limiter": {
            "ceiling_db": -0.8,
            "lookahead_ms": 5.0,
            "release_ms": 70.0,
            "oversample_factor": 4,
        },
    },
    "podcast": {
        "target_lufs": -16.0,
        "highpass_hz": 70.0,
        "stereo_width": 0.9,
        "eq": [
            {"type": "bell", "frequency_hz": 180.0, "gain_db": -1.2, "q": 1.0},
            {"type": "bell", "frequency_hz": 3500.0, "gain_db": 1.5, "q": 1.0},
        ],
        "compressor": {
            "threshold_db": -22.0,
            "ratio": 3.0,
            "attack_ms": 10.0,
            "release_ms": 120.0,
            "makeup_gain_db": 0.5,
        },
        "limiter": {
            "ceiling_db": -1.5,
            "lookahead_ms": 5.0,
            "release_ms": 90.0,
            "oversample_factor": 4,
        },
    },
    "demo-loud": {
        "target_lufs": -10.0,
        "highpass_hz": 30.0,
        "stereo_width": 1.02,
        "compressor": {
            "threshold_db": -17.0,
            "ratio": 2.8,
            "attack_ms": 12.0,
            "release_ms": 110.0,
        },
        "limiter": {
            "ceiling_db": -1.0,
            "lookahead_ms": 4.0,
            "release_ms": 65.0,
            "oversample_factor": 4,
        },
    },
    "vinyl-prep": {
        "target_lufs": -16.0,
        "highpass_hz": 35.0,
        "stereo_width": 0.95,
        "eq": [
            {"type": "low_shelf", "frequency_hz": 70.0, "gain_db": -0.8, "q": 0.707},
            {"type": "high_shelf", "frequency_hz": 12000.0, "gain_db": -0.5, "q": 0.707},
        ],
        "compressor": {
            "threshold_db": -19.0,
            "ratio": 1.8,
            "attack_ms": 25.0,
            "release_ms": 180.0,
        },
        "limiter": {
            "ceiling_db": -3.0,
            "lookahead_ms": 5.0,
            "release_ms": 100.0,
            "oversample_factor": 4,
        },
    },
}


def preset_names() -> list[str]:
    return sorted(PRESETS)


def settings_from_preset(name: str) -> MasterSettings:
    return master_settings_from_mapping(_preset_mapping(name))


def settings_from_mapping_with_preset(values: dict | None) -> MasterSettings:
    values = values or {}
    preset = values.get("preset")
    merged = _preset_mapping(preset) if preset else {}
    _deep_update(merged, {key: value for key, value in values.items() if key != "preset"})
    return master_settings_from_mapping(merged)


def _preset_mapping(name: str) -> dict[str, Any]:
    if name not in PRESETS:
        available = ", ".join(preset_names())
        raise ValueError(f"Unknown preset '{name}'. Available presets: {available}")
    return deepcopy(PRESETS[name])


def _deep_update(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base
