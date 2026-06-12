from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import db_to_amp, ensure_channels, read_audio
from .effects import EffectSpec, apply_effects, effects_from_mapping
from .master import CompressorSettings, EQBand, apply_eq, compress, highpass


@dataclass(frozen=True)
class Stem:
    path: Path
    gain_db: float = 0.0
    pan: float = 0.0
    start: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    mute: bool = False
    solo: bool = False
    highpass_hz: float = 0.0
    eq: list[EQBand] | None = None
    effects: list[EffectSpec] | None = None
    compressor: CompressorSettings | None = None


@dataclass(frozen=True)
class MixSettings:
    sample_rate: int = 48_000
    output_channels: int = 2
    headroom_db: float = -6.0


def mix_stems(stems: list[Stem], settings: MixSettings) -> np.ndarray:
    active_stems = _active_stems(stems)
    if not active_stems:
        return np.zeros((0, settings.output_channels), dtype=np.float64)

    rendered: list[tuple[int, np.ndarray]] = []
    total_samples = 0

    for stem in active_stems:
        buffer = read_audio(stem.path, settings.sample_rate)
        data = _render_stem(buffer.data, stem, settings)
        offset = max(0, int(round(stem.start * settings.sample_rate)))
        rendered.append((offset, data))
        total_samples = max(total_samples, offset + data.shape[0])

    mix = np.zeros((total_samples, settings.output_channels), dtype=np.float64)
    for offset, data in rendered:
        mix[offset : offset + data.shape[0]] += data

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    target_peak = db_to_amp(settings.headroom_db)
    if peak > target_peak:
        mix *= target_peak / peak

    return mix


def stem_from_mapping(values: dict) -> Stem:
    if "path" not in values:
        raise ValueError("Each stem must include a path.")

    return Stem(
        path=Path(values["path"]),
        gain_db=float(values.get("gain_db", 0.0)),
        pan=float(values.get("pan", 0.0)),
        start=float(values.get("start", 0.0)),
        fade_in=float(values.get("fade_in", 0.0)),
        fade_out=float(values.get("fade_out", 0.0)),
        mute=bool(values.get("mute", False)),
        solo=bool(values.get("solo", False)),
        highpass_hz=float(values.get("highpass_hz", 0.0)),
        eq=[_eq_band_from_mapping(band) for band in values.get("eq", []) or []],
        effects=effects_from_mapping(values.get("effects")),
        compressor=_compressor_from_mapping(values.get("compressor")),
    )


def _active_stems(stems: list[Stem]) -> list[Stem]:
    soloed = [stem for stem in stems if stem.solo and not stem.mute]
    if soloed:
        return soloed
    return [stem for stem in stems if not stem.mute]


def _render_stem(data: np.ndarray, stem: Stem, settings: MixSettings) -> np.ndarray:
    rendered = ensure_channels(data, settings.output_channels).astype(np.float64, copy=True)

    if stem.highpass_hz > 0:
        rendered = highpass(rendered, settings.sample_rate, stem.highpass_hz)

    if stem.eq:
        rendered = apply_eq(rendered, settings.sample_rate, stem.eq)

    if stem.effects:
        rendered = apply_effects(rendered, settings.sample_rate, stem.effects)

    if stem.compressor:
        rendered = compress(rendered, settings.sample_rate, stem.compressor)

    if settings.output_channels == 2:
        rendered = _apply_pan(rendered, stem.pan)

    rendered *= db_to_amp(stem.gain_db)
    rendered = _apply_fades(rendered, stem.fade_in, stem.fade_out, settings.sample_rate)
    return rendered


def _apply_pan(data: np.ndarray, pan: float) -> np.ndarray:
    pan = float(np.clip(pan, -1.0, 1.0))
    result = data.copy()

    if data.shape[1] == 1:
        angle = (pan + 1.0) * np.pi / 4.0
        return np.column_stack((data[:, 0] * np.cos(angle), data[:, 0] * np.sin(angle)))

    if pan < 0.0:
        result[:, 1] *= 1.0 + pan
    elif pan > 0.0:
        result[:, 0] *= 1.0 - pan
    return result


def _apply_fades(data: np.ndarray, fade_in: float, fade_out: float, sample_rate: int) -> np.ndarray:
    result = data.copy()

    fade_in_samples = min(result.shape[0], max(0, int(round(fade_in * sample_rate))))
    if fade_in_samples:
        result[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples, endpoint=True)[:, np.newaxis]

    fade_out_samples = min(result.shape[0], max(0, int(round(fade_out * sample_rate))))
    if fade_out_samples:
        result[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples, endpoint=True)[:, np.newaxis]

    return result


def _eq_band_from_mapping(values: dict) -> EQBand:
    return EQBand(
        kind=str(values.get("type", values.get("kind", "bell"))),
        frequency_hz=float(values["frequency_hz"]),
        gain_db=float(values.get("gain_db", 0.0)),
        q=float(values.get("q", 0.707)),
    )


def _compressor_from_mapping(values: dict | None) -> CompressorSettings | None:
    if not values:
        return None
    return CompressorSettings(
        threshold_db=float(values.get("threshold_db", -18.0)),
        ratio=float(values.get("ratio", 2.0)),
        attack_ms=float(values.get("attack_ms", 20.0)),
        release_ms=float(values.get("release_ms", 160.0)),
        makeup_gain_db=float(values.get("makeup_gain_db", 0.0)),
    )
