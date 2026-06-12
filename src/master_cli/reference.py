from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import amp_to_db, ensure_channels
from .master import AudioStats, analyze_audio


BANDS: tuple[tuple[str, float, float], ...] = (
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 250.0),
    ("low_mid", 250.0, 500.0),
    ("mid", 500.0, 2000.0),
    ("high_mid", 2000.0, 6000.0),
    ("air", 6000.0, 20000.0),
)


@dataclass(frozen=True)
class ReferenceProfile:
    stats: AudioStats
    rms_db: float
    crest_factor_db: float
    stereo_width: float
    tonal_balance_db: dict[str, float]


@dataclass(frozen=True)
class ReferenceComparison:
    source: ReferenceProfile
    reference: ReferenceProfile
    deltas: dict[str, float | dict[str, float]]


def profile_audio(data: np.ndarray, sample_rate: int) -> ReferenceProfile:
    stats = analyze_audio(data, sample_rate)
    rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
    rms_db = amp_to_db(rms)
    crest_factor_db = stats.peak_db - rms_db
    return ReferenceProfile(
        stats=stats,
        rms_db=rms_db,
        crest_factor_db=crest_factor_db,
        stereo_width=_stereo_width_score(data),
        tonal_balance_db=_tonal_balance(data, sample_rate),
    )


def compare_to_reference(
    source_data: np.ndarray,
    source_sample_rate: int,
    reference_data: np.ndarray,
    reference_sample_rate: int,
) -> ReferenceComparison:
    source = profile_audio(source_data, source_sample_rate)
    reference = profile_audio(reference_data, reference_sample_rate)
    source_tone = source.tonal_balance_db
    reference_tone = reference.tonal_balance_db
    tonal_delta = {
        name: source_tone[name] - reference_tone[name]
        for name, _, _ in BANDS
        if name in source_tone and name in reference_tone
    }
    deltas: dict[str, float | dict[str, float]] = {
        "integrated_lufs": source.stats.integrated_lufs - reference.stats.integrated_lufs,
        "peak_db": source.stats.peak_db - reference.stats.peak_db,
        "rms_db": source.rms_db - reference.rms_db,
        "crest_factor_db": source.crest_factor_db - reference.crest_factor_db,
        "stereo_width": source.stereo_width - reference.stereo_width,
        "tonal_balance_db": tonal_delta,
    }
    return ReferenceComparison(source=source, reference=reference, deltas=deltas)


def _stereo_width_score(data: np.ndarray) -> float:
    stereo = ensure_channels(data, 2)
    if stereo.size == 0:
        return 0.0
    mid = (stereo[:, 0] + stereo[:, 1]) * 0.5
    side = (stereo[:, 0] - stereo[:, 1]) * 0.5
    mid_rms = np.sqrt(np.mean(np.square(mid)))
    side_rms = np.sqrt(np.mean(np.square(side)))
    if mid_rms <= 1e-12:
        return 0.0
    return float(side_rms / mid_rms)


def _tonal_balance(data: np.ndarray, sample_rate: int) -> dict[str, float]:
    mono = np.mean(ensure_channels(data, 1), axis=1)
    if mono.size == 0:
        return {name: float("-inf") for name, _, _ in BANDS}

    window = np.hanning(mono.size)
    spectrum = np.fft.rfft(mono * window)
    power = np.square(np.abs(spectrum))
    frequencies = np.fft.rfftfreq(mono.size, d=1.0 / sample_rate)
    total = float(np.sum(power)) + 1e-18

    balance: dict[str, float] = {}
    for name, low_hz, high_hz in BANDS:
        mask = (frequencies >= low_hz) & (frequencies < high_hz)
        band_power = float(np.sum(power[mask])) + 1e-18
        balance[name] = float(10.0 * np.log10(band_power / total))
    return balance
