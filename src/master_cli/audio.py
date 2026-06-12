from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


@dataclass(frozen=True)
class AudioBuffer:
    data: np.ndarray
    sample_rate: int


def read_audio(path: str | Path, target_sample_rate: int | None = None) -> AudioBuffer:
    """Read an audio file as float64 with shape `(samples, channels)`."""
    data, sample_rate = sf.read(str(path), always_2d=True, dtype="float64")

    if target_sample_rate and target_sample_rate != sample_rate:
        data = resample_audio(data, sample_rate, target_sample_rate)
        sample_rate = target_sample_rate

    return AudioBuffer(data=data, sample_rate=sample_rate)


def write_audio(path: str | Path, data: np.ndarray, sample_rate: int, subtype: str = "PCM_24") -> None:
    """Write audio, creating parent directories and clipping to a valid float range."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destination), np.clip(data, -1.0, 1.0), sample_rate, subtype=subtype)


def export_audio(path: str | Path, data: np.ndarray, sample_rate: int, subtype: str = "PCM_24") -> None:
    """Export audio with SoundFile, or transcode compressed formats with ffmpeg."""
    destination = Path(path)
    if destination.suffix.lower() not in {".mp3", ".aac", ".m4a"}:
        write_audio(destination, data, sample_rate, subtype=subtype)
        return

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for MP3/AAC/M4A export.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        temporary_wav = Path(handle.name)

    try:
        write_audio(temporary_wav, data, sample_rate, subtype="PCM_24")
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(temporary_wav),
            str(destination),
        ]
        subprocess.run(command, check=True)
    finally:
        temporary_wav.unlink(missing_ok=True)


def resample_audio(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    common = gcd(source_rate, target_rate)
    up = target_rate // common
    down = source_rate // common
    return resample_poly(data, up, down, axis=0)


def ensure_channels(data: np.ndarray, channels: int) -> np.ndarray:
    if data.ndim == 1:
        data = data[:, np.newaxis]

    if data.shape[1] == channels:
        return data

    if channels == 1:
        return np.mean(data, axis=1, keepdims=True)

    if data.shape[1] == 1 and channels == 2:
        return np.repeat(data, 2, axis=1)

    if data.shape[1] > channels:
        return data[:, :channels]

    padding = np.zeros((data.shape[0], channels - data.shape[1]), dtype=data.dtype)
    return np.concatenate([data, padding], axis=1)


def db_to_amp(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def amp_to_db(value: float, floor_db: float = -120.0) -> float:
    value = max(float(value), 10.0 ** (floor_db / 20.0))
    return float(20.0 * np.log10(value))
