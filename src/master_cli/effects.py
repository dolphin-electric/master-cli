from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class EffectSpec:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)


def effects_from_mapping(values: list[dict] | None) -> list[EffectSpec]:
    effects = []
    for item in values or []:
        if "type" not in item:
            raise ValueError("Each effect must include a type.")
        parameters = {key: value for key, value in item.items() if key != "type"}
        effects.append(EffectSpec(type=str(item["type"]), parameters=parameters))
    return effects


def apply_effects(data: np.ndarray, sample_rate: int, effects: list[EffectSpec] | None) -> np.ndarray:
    if not effects:
        return data

    import_error = None
    try:
        import pedalboard as pb
    except ImportError as exc:
        import_error = exc

    if import_error:
        raise RuntimeError("Pedalboard effects require installing the optional dependency: pip install -e '.[pedalboard]'") from import_error

    board = pb.Pedalboard([_build_pedalboard_plugin(pb, effect) for effect in effects])
    audio = data.astype(np.float32, copy=False).T
    processed = board(audio, float(sample_rate)).T
    if processed.ndim == 1:
        processed = processed[:, np.newaxis]
    return processed.astype(np.float64, copy=False)


def _build_pedalboard_plugin(pb, effect: EffectSpec):
    effect_type = effect.type.removeprefix("pedalboard.")
    parameters = dict(effect.parameters)

    if effect_type.lower() in {"plugin", "external", "vst3", "au", "audio_unit"}:
        path = parameters.pop("path", None)
        if not path:
            raise ValueError("External Pedalboard effects require a path.")
        plugin = pb.load_plugin(path)
        for key, value in parameters.get("parameters", {}).items():
            setattr(plugin, key, value)
        return plugin

    try:
        plugin_class = getattr(pb, effect_type)
    except AttributeError as exc:
        raise ValueError(f"Unsupported Pedalboard effect type: {effect.type}") from exc

    return plugin_class(**parameters)
