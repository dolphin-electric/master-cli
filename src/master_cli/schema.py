from __future__ import annotations

from typing import Any

from .presets import preset_names
from .reference import BANDS


def mix_config_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "master-cli mix config",
        "type": "object",
        "required": ["stems"],
        "additionalProperties": False,
        "properties": {
            "sample_rate": {"type": "integer", "default": 48000, "minimum": 8000},
            "output_channels": {"type": "integer", "default": 2, "enum": [1, 2]},
            "headroom_db": {"type": "number", "default": -6.0},
            "master": master_settings_schema(),
            "stems": {
                "type": "array",
                "minItems": 1,
                "items": stem_schema(),
            },
        },
    }


def master_settings_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "preset": {"type": "string", "enum": preset_names()},
            "target_lufs": {"type": "number", "default": -14.0},
            "ceiling_db": {"type": "number", "default": -1.0},
            "highpass_hz": {"type": "number", "default": 30.0, "minimum": 0.0},
            "stereo_width": {"type": "number", "default": 1.0, "minimum": 0.0},
            "eq": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["frequency_hz"],
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string", "enum": ["bell", "peaking", "low_shelf", "high_shelf"]},
                        "kind": {"type": "string", "enum": ["bell", "peaking", "low_shelf", "high_shelf"]},
                        "frequency_hz": {"type": "number", "exclusiveMinimum": 0.0},
                        "gain_db": {"type": "number", "default": 0.0},
                        "q": {"type": "number", "default": 0.707, "exclusiveMinimum": 0.0},
                    },
                },
            },
            "effects": effects_schema(),
            "compressor": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "threshold_db": {"type": "number", "default": -18.0},
                    "ratio": {"type": "number", "default": 2.0, "minimum": 1.0},
                    "attack_ms": {"type": "number", "default": 20.0, "minimum": 0.0},
                    "release_ms": {"type": "number", "default": 160.0, "minimum": 0.0},
                    "makeup_gain_db": {"type": "number", "default": 0.0},
                },
            },
            "limiter": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ceiling_db": {"type": "number", "default": -1.0},
                    "lookahead_ms": {"type": "number", "default": 5.0, "minimum": 0.0},
                    "release_ms": {"type": "number", "default": 80.0, "minimum": 0.0},
                    "oversample_factor": {"type": "integer", "default": 4, "minimum": 1},
                },
            },
        },
    }


def stem_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["path"],
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string"},
            "gain_db": {"type": "number", "default": 0.0},
            "pan": {"type": "number", "default": 0.0, "minimum": -1.0, "maximum": 1.0},
            "start": {"type": "number", "default": 0.0, "minimum": 0.0},
            "fade_in": {"type": "number", "default": 0.0, "minimum": 0.0},
            "fade_out": {"type": "number", "default": 0.0, "minimum": 0.0},
            "mute": {"type": "boolean", "default": False},
            "solo": {"type": "boolean", "default": False},
            "highpass_hz": {"type": "number", "default": 0.0, "minimum": 0.0},
            "eq": master_settings_schema()["properties"]["eq"],
            "effects": effects_schema(),
            "compressor": master_settings_schema()["properties"]["compressor"],
        },
    }


def effects_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["type"],
            "additionalProperties": True,
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Pedalboard built-in class name, e.g. pedalboard.Reverb, Delay, Chorus, Distortion, Phaser, PitchShift, Bitcrush, or external plugin marker such as vst3.",
                },
                "path": {"type": "string", "description": "Path for external VST3/AU plugins."},
                "parameters": {
                    "type": "object",
                    "description": "Parameter map for external plugins loaded with pedalboard.load_plugin.",
                },
            },
        },
    }


def json_output_contract() -> dict[str, Any]:
    return {
        "status": "ok | dry_run | error",
        "command": "mix | master | render | batch | analyze | compare | audit | presets | schema | job",
        "paths": "Input/output/report paths as strings where relevant.",
        "settings": "Resolved mix/master settings after preset and override expansion.",
        "stats": "AudioStats objects include integrated_lufs, peak_db, and peak.",
        "errors": {
            "status": "error",
            "code": "Stable machine-readable error code.",
            "message": "Human-readable error message.",
            "field": "Optional request field associated with the error.",
        },
        "reference_analysis": {
            "source": "Profile for generated/source audio.",
            "reference": "Profile for reference audio.",
            "deltas": "source minus reference values.",
            "tonal_balance_bands": [name for name, _, _ in BANDS],
        },
    }


def job_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "master-cli job request",
        "type": "object",
        "required": ["command"],
        "additionalProperties": True,
        "properties": {
            "command": {
                "type": "string",
                "enum": ["mix", "master", "render", "batch", "analyze", "compare", "audit", "presets", "schema"],
            },
            "path": {"type": "string"},
            "config": {"type": "string"},
            "input": {"type": "string"},
            "output": {"type": "string"},
            "input_dir": {"type": "string"},
            "output_dir": {"type": "string"},
            "reference": {"type": "string"},
            "preset": {"type": "string", "enum": preset_names()},
            "target_lufs": {"type": "number"},
            "lufs_tolerance": {"type": "number"},
            "ceiling_db": {"type": "number"},
            "stats_tolerance": {"type": "number"},
            "require_reports": {"type": "boolean"},
            "highpass_hz": {"type": "number"},
            "stereo_width": {"type": "number"},
            "lookahead_ms": {"type": "number"},
            "release_ms": {"type": "number"},
            "oversample_factor": {"type": "integer", "minimum": 1},
            "subtype": {"type": "string"},
            "extension": {"type": "string"},
            "report": {"oneOf": [{"type": "boolean"}, {"type": "string"}, {"type": "null"}]},
            "dry_run": {"type": "boolean"},
        },
    }


def schemas_payload() -> dict[str, Any]:
    return {
        "job": job_schema(),
        "mix_config": mix_config_schema(),
        "master_settings": master_settings_schema(),
        "stem": stem_schema(),
        "effects": effects_schema(),
        "json_output_contract": json_output_contract(),
    }
