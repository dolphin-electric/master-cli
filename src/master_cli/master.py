from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pyloudnorm as pyln
from scipy.signal import butter, resample_poly, sosfilt, sosfiltfilt

from .audio import amp_to_db, db_to_amp
from .effects import EffectSpec, apply_effects, effects_from_mapping


@dataclass(frozen=True)
class CompressorSettings:
    threshold_db: float = -18.0
    ratio: float = 2.0
    attack_ms: float = 20.0
    release_ms: float = 160.0
    makeup_gain_db: float = 0.0


@dataclass(frozen=True)
class EQBand:
    kind: str
    frequency_hz: float
    gain_db: float
    q: float = 0.707


@dataclass(frozen=True)
class LimiterSettings:
    ceiling_db: float = -1.0
    lookahead_ms: float = 5.0
    release_ms: float = 80.0
    oversample_factor: int = 4


@dataclass(frozen=True)
class MasterSettings:
    target_lufs: float = -14.0
    ceiling_db: float = -1.0
    highpass_hz: float = 30.0
    stereo_width: float = 1.0
    eq: list[EQBand] = field(default_factory=list)
    effects: list[EffectSpec] = field(default_factory=list)
    compressor: CompressorSettings = field(default_factory=CompressorSettings)
    limiter: LimiterSettings | None = None


@dataclass(frozen=True)
class AudioStats:
    integrated_lufs: float
    peak_db: float
    peak: float


def master_audio(data: np.ndarray, sample_rate: int, settings: MasterSettings) -> np.ndarray:
    if data.size == 0:
        return data

    processed = data.astype(np.float64, copy=True)
    processed -= np.mean(processed, axis=0, keepdims=True)

    if settings.highpass_hz > 0:
        processed = highpass(processed, sample_rate, settings.highpass_hz)

    if settings.eq:
        processed = apply_eq(processed, sample_rate, settings.eq)

    if settings.effects:
        processed = apply_effects(processed, sample_rate, settings.effects)

    if processed.shape[1] == 2 and settings.stereo_width != 1.0:
        processed = stereo_width(processed, settings.stereo_width)

    processed = compress(processed, sample_rate, settings.compressor)
    processed = normalize_loudness(processed, sample_rate, settings.target_lufs)
    limiter = settings.limiter or LimiterSettings(ceiling_db=settings.ceiling_db)
    processed = lookahead_limit(
        processed,
        sample_rate,
        ceiling_db=limiter.ceiling_db,
        lookahead_ms=limiter.lookahead_ms,
        release_ms=limiter.release_ms,
        oversample_factor=limiter.oversample_factor,
    )
    return processed


def analyze_audio(data: np.ndarray, sample_rate: int) -> AudioStats:
    if data.size == 0:
        return AudioStats(integrated_lufs=float("-inf"), peak_db=float("-inf"), peak=0.0)

    meter = pyln.Meter(sample_rate)
    loudness = float(meter.integrated_loudness(data))
    peak = float(np.max(np.abs(data)))
    return AudioStats(integrated_lufs=loudness, peak_db=amp_to_db(peak), peak=peak)


def true_peak(data: np.ndarray, oversample_factor: int = 4) -> float:
    if data.size == 0:
        return 0.0
    factor = max(1, int(oversample_factor))
    if factor == 1:
        return float(np.max(np.abs(data)))
    oversampled = resample_poly(data, factor, 1, axis=0)
    return float(np.max(np.abs(oversampled)))


def highpass(data: np.ndarray, sample_rate: int, frequency_hz: float, order: int = 2) -> np.ndarray:
    nyquist = sample_rate / 2.0
    cutoff = min(max(frequency_hz / nyquist, 0.0001), 0.99)
    sos = butter(order, cutoff, btype="highpass", output="sos")
    return _filter_sos(sos, data)


def apply_eq(data: np.ndarray, sample_rate: int, bands: list[EQBand]) -> np.ndarray:
    processed = data
    for band in bands:
        if band.gain_db == 0.0:
            continue
        sos = _eq_sos(band, sample_rate)
        processed = _filter_sos(sos, processed)
    return processed


def stereo_width(data: np.ndarray, width: float) -> np.ndarray:
    width = max(0.0, float(width))
    mid = (data[:, 0] + data[:, 1]) * 0.5
    side = (data[:, 0] - data[:, 1]) * 0.5 * width
    return np.column_stack((mid + side, mid - side))


def compress(data: np.ndarray, sample_rate: int, settings: CompressorSettings) -> np.ndarray:
    if settings.ratio <= 1.0:
        return data * db_to_amp(settings.makeup_gain_db)

    detector = np.max(np.abs(data), axis=1)
    envelope = _smooth_envelope(detector, sample_rate, settings.attack_ms, settings.release_ms)

    envelope_db = np.array([amp_to_db(value) for value in envelope])
    over_db = np.maximum(0.0, envelope_db - settings.threshold_db)
    gain_reduction_db = over_db * (1.0 - (1.0 / settings.ratio))
    gain_db = settings.makeup_gain_db - gain_reduction_db
    gain = np.power(10.0, gain_db / 20.0)
    return data * gain[:, np.newaxis]


def normalize_loudness(data: np.ndarray, sample_rate: int, target_lufs: float) -> np.ndarray:
    meter = pyln.Meter(sample_rate)
    loudness = meter.integrated_loudness(data)
    if not np.isfinite(loudness):
        return data
    return pyln.normalize.loudness(data, loudness, target_lufs)


def limit_peak(data: np.ndarray, ceiling_db: float) -> np.ndarray:
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    ceiling = db_to_amp(ceiling_db)
    if peak > ceiling > 0.0:
        return data * (ceiling / peak)
    return data


def lookahead_limit(
    data: np.ndarray,
    sample_rate: int,
    ceiling_db: float,
    lookahead_ms: float = 5.0,
    release_ms: float = 80.0,
    oversample_factor: int = 4,
) -> np.ndarray:
    if data.size == 0:
        return data

    ceiling = db_to_amp(ceiling_db)
    if ceiling <= 0.0:
        return data

    detector = np.max(np.abs(data), axis=1)
    lookahead_samples = max(1, int(round((lookahead_ms / 1000.0) * sample_rate)))
    future_peak = _sliding_future_max(detector, lookahead_samples)
    desired_gain = np.minimum(1.0, ceiling / np.maximum(future_peak, 1e-12))

    release = _coefficient(release_ms, sample_rate)
    gain = np.empty_like(desired_gain)
    previous = 1.0
    for index, desired in enumerate(desired_gain):
        if desired < previous:
            current = desired
        else:
            current = release * previous + (1.0 - release) * desired
        gain[index] = current
        previous = current

    limited = data * gain[:, np.newaxis]
    return limit_true_peak(limited, ceiling_db, oversample_factor=oversample_factor)


def limit_true_peak(data: np.ndarray, ceiling_db: float, oversample_factor: int = 4) -> np.ndarray:
    limited = limit_peak(data, ceiling_db)
    peak = true_peak(limited, oversample_factor=oversample_factor)
    ceiling = db_to_amp(ceiling_db)
    if peak > ceiling > 0.0:
        limited = limited * (ceiling / peak)
    return limit_peak(limited, ceiling_db)


def master_settings_from_mapping(values: dict | None) -> MasterSettings:
    values = values or {}
    compressor_values = values.get("compressor", {}) or {}
    limiter_values = values.get("limiter", {}) or {}

    return MasterSettings(
        target_lufs=float(values.get("target_lufs", -14.0)),
        ceiling_db=float(values.get("ceiling_db", -1.0)),
        highpass_hz=float(values.get("highpass_hz", 30.0)),
        stereo_width=float(values.get("stereo_width", 1.0)),
        eq=[_eq_band_from_mapping(band) for band in values.get("eq", []) or []],
        effects=effects_from_mapping(values.get("effects")),
        compressor=CompressorSettings(
            threshold_db=float(compressor_values.get("threshold_db", -18.0)),
            ratio=float(compressor_values.get("ratio", 2.0)),
            attack_ms=float(compressor_values.get("attack_ms", 20.0)),
            release_ms=float(compressor_values.get("release_ms", 160.0)),
            makeup_gain_db=float(compressor_values.get("makeup_gain_db", 0.0)),
        ),
        limiter=LimiterSettings(
            ceiling_db=float(limiter_values.get("ceiling_db", values.get("ceiling_db", -1.0))),
            lookahead_ms=float(limiter_values.get("lookahead_ms", 5.0)),
            release_ms=float(limiter_values.get("release_ms", 80.0)),
            oversample_factor=int(limiter_values.get("oversample_factor", 4)),
        ),
    )


def _eq_band_from_mapping(values: dict) -> EQBand:
    return EQBand(
        kind=str(values.get("type", values.get("kind", "bell"))),
        frequency_hz=float(values["frequency_hz"]),
        gain_db=float(values.get("gain_db", 0.0)),
        q=float(values.get("q", 0.707)),
    )


def _filter_sos(sos: np.ndarray, data: np.ndarray) -> np.ndarray:
    padlen = 3 * (2 * len(sos) + 1)
    if data.shape[0] <= padlen:
        return sosfilt(sos, data, axis=0)
    return sosfiltfilt(sos, data, axis=0)


def _eq_sos(band: EQBand, sample_rate: int) -> np.ndarray:
    kind = band.kind.lower().replace("-", "_")
    frequency = min(max(float(band.frequency_hz), 1.0), sample_rate / 2.0 - 1.0)
    q = max(float(band.q), 0.05)
    gain_db = float(band.gain_db)
    a = 10.0 ** (gain_db / 40.0)
    omega = 2.0 * np.pi * frequency / sample_rate
    sine = np.sin(omega)
    cosine = np.cos(omega)

    if kind in {"bell", "peak", "peaking"}:
        alpha = sine / (2.0 * q)
        b0 = 1.0 + alpha * a
        b1 = -2.0 * cosine
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cosine
        a2 = 1.0 - alpha / a
    elif kind in {"low_shelf", "lowshelf"}:
        alpha = sine / 2.0 * np.sqrt(2.0)
        sqrt_a = np.sqrt(a)
        b0 = a * ((a + 1.0) - (a - 1.0) * cosine + 2.0 * sqrt_a * alpha)
        b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cosine)
        b2 = a * ((a + 1.0) - (a - 1.0) * cosine - 2.0 * sqrt_a * alpha)
        a0 = (a + 1.0) + (a - 1.0) * cosine + 2.0 * sqrt_a * alpha
        a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cosine)
        a2 = (a + 1.0) + (a - 1.0) * cosine - 2.0 * sqrt_a * alpha
    elif kind in {"high_shelf", "highshelf"}:
        alpha = sine / 2.0 * np.sqrt(2.0)
        sqrt_a = np.sqrt(a)
        b0 = a * ((a + 1.0) + (a - 1.0) * cosine + 2.0 * sqrt_a * alpha)
        b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cosine)
        b2 = a * ((a + 1.0) + (a - 1.0) * cosine - 2.0 * sqrt_a * alpha)
        a0 = (a + 1.0) - (a - 1.0) * cosine + 2.0 * sqrt_a * alpha
        a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cosine)
        a2 = (a + 1.0) - (a - 1.0) * cosine - 2.0 * sqrt_a * alpha
    else:
        raise ValueError(f"Unsupported EQ band type: {band.kind}")

    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]], dtype=np.float64)


def _sliding_future_max(values: np.ndarray, window: int) -> np.ndarray:
    from collections import deque

    result = np.empty_like(values)
    indices: deque[int] = deque()
    last_index = len(values) - 1

    for index in range(last_index, -1, -1):
        while indices and indices[0] > index + window - 1:
            indices.popleft()
        while indices and values[indices[-1]] <= values[index]:
            indices.pop()
        indices.append(index)
        result[index] = values[indices[0]]

    return result


def _smooth_envelope(data: np.ndarray, sample_rate: int, attack_ms: float, release_ms: float) -> np.ndarray:
    envelope = np.zeros_like(data)
    attack = _coefficient(attack_ms, sample_rate)
    release = _coefficient(release_ms, sample_rate)

    for index, sample in enumerate(data):
        previous = envelope[index - 1] if index else 0.0
        coefficient = attack if sample > previous else release
        envelope[index] = coefficient * previous + (1.0 - coefficient) * sample

    return envelope


def _coefficient(time_ms: float, sample_rate: int) -> float:
    samples = max(1.0, (time_ms / 1000.0) * sample_rate)
    return float(np.exp(-1.0 / samples))
