from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import soundfile as sf

from .audio import read_audio
from .errors import AgentError
from .master import analyze_audio
from .report import default_report_path, serialize


@dataclass(frozen=True)
class AuditSettings:
    target_lufs: float | None = None
    lufs_tolerance: float = 0.5
    ceiling_db: float | None = -1.0
    stats_tolerance: float = 0.05
    require_reports: bool = True


def audit_directory(path: str | Path, settings: AuditSettings) -> dict[str, Any]:
    root = Path(path)
    if not root.exists():
        raise AgentError("input_not_found", f"Audit path does not exist: {root}", "path")
    if not root.is_dir() and not (root.is_file() and root.suffix.lower() == ".wav"):
        raise AgentError("invalid_audit_path", f"Audit path must be a WAV file or directory: {root}", "path")

    files = [_audit_file(wav, settings) for wav in _wav_files(root)]
    issue_count = sum(len(item["issues"]) for item in files)
    return {
        "status": "ok" if issue_count == 0 else "warning",
        "command": "audit",
        "path": str(root),
        "settings": serialize(settings),
        "summary": {
            "files": len(files),
            "issues": issue_count,
            "passed": sum(1 for item in files if not item["issues"]),
            "failed": sum(1 for item in files if item["issues"]),
        },
        "files": files,
    }


def _audit_file(path: Path, settings: AuditSettings) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    info_payload: dict[str, Any] | None = None
    stats_payload: dict[str, Any] | None = None

    try:
        info = sf.info(str(path))
        info_payload = {
            "sample_rate": info.samplerate,
            "channels": info.channels,
            "frames": info.frames,
            "duration_seconds": info.frames / info.samplerate if info.samplerate else 0.0,
            "format": info.format,
            "subtype": info.subtype,
        }
        if info.frames <= 0:
            issues.append(_issue("empty_audio", "Audio file has no frames."))
        if info.channels <= 0:
            issues.append(_issue("invalid_channels", "Audio file has no channels."))
    except Exception as exc:
        return {
            "path": str(path),
            "report": None,
            "info": None,
            "stats": None,
            "issues": [_issue("invalid_audio", f"Unable to read WAV metadata: {exc}")],
        }

    try:
        audio = read_audio(path)
        stats = analyze_audio(audio.data, audio.sample_rate)
        stats_payload = serialize(stats)
        if settings.ceiling_db is not None and stats.peak_db > settings.ceiling_db:
            issues.append(
                _issue(
                    "peak_above_ceiling",
                    f"Peak {stats.peak_db:.2f} dBFS is above ceiling {settings.ceiling_db:.2f} dBFS.",
                    peak_db=stats.peak_db,
                    ceiling_db=settings.ceiling_db,
                )
            )
        if settings.target_lufs is not None:
            delta = stats.integrated_lufs - settings.target_lufs
            if abs(delta) > settings.lufs_tolerance:
                issues.append(
                    _issue(
                        "lufs_out_of_range",
                        (
                            f"Integrated LUFS {stats.integrated_lufs:.2f} is outside "
                            f"{settings.target_lufs:.2f} +/- {settings.lufs_tolerance:.2f}."
                        ),
                        integrated_lufs=stats.integrated_lufs,
                        target_lufs=settings.target_lufs,
                        delta=delta,
                    )
                )
    except Exception as exc:
        issues.append(_issue("analysis_failed", f"Unable to analyze audio: {exc}"))

    report_path = default_report_path(path)
    report_payload = _load_report(report_path, issues, settings)
    if report_payload is None and settings.require_reports:
        issues.append(_issue("missing_report", "Expected matching report JSON.", report=str(report_path)))
    elif report_payload is not None and stats_payload is not None:
        _compare_report_stats(report_payload, stats_payload, issues, settings)
        reported_output = report_payload.get("output")
        if reported_output and Path(reported_output).name != path.name:
            issues.append(
                _issue(
                    "report_output_mismatch",
                    "Report output filename does not match audited WAV.",
                    report_output=reported_output,
                )
            )

    return {
        "path": str(path),
        "report": str(report_path) if report_path.exists() else None,
        "info": info_payload,
        "stats": stats_payload,
        "issues": issues,
    }


def _load_report(path: Path, issues: list[dict[str, Any]], settings: AuditSettings) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(_issue("invalid_report_json", f"Unable to parse report JSON: {exc}", report=str(path)))
        return None

    if not isinstance(payload, dict):
        issues.append(_issue("invalid_report_shape", "Report JSON must be an object.", report=str(path)))
        return None

    output_stats = payload.get("output_stats")
    if not isinstance(output_stats, dict):
        issues.append(_issue("missing_report_output_stats", "Report is missing output_stats.", report=str(path)))
        return None

    if settings.ceiling_db is not None:
        peak_db = output_stats.get("peak_db")
        if isinstance(peak_db, int | float) and peak_db > settings.ceiling_db:
            issues.append(
                _issue(
                    "report_peak_above_ceiling",
                    f"Reported peak {peak_db:.2f} dBFS is above ceiling {settings.ceiling_db:.2f} dBFS.",
                    peak_db=peak_db,
                    ceiling_db=settings.ceiling_db,
                )
            )

    return payload


def _compare_report_stats(
    report: dict[str, Any],
    actual_stats: dict[str, Any],
    issues: list[dict[str, Any]],
    settings: AuditSettings,
) -> None:
    reported = report.get("output_stats") or {}
    for key in ("integrated_lufs", "peak_db", "peak"):
        reported_value = reported.get(key)
        actual_value = actual_stats.get(key)
        if not isinstance(reported_value, int | float) or not isinstance(actual_value, int | float):
            continue
        delta = float(actual_value) - float(reported_value)
        if abs(delta) > settings.stats_tolerance:
            issues.append(
                _issue(
                    "report_stats_mismatch",
                    f"Reported {key} differs from actual by {delta:.3f}.",
                    field=key,
                    reported=reported_value,
                    actual=actual_value,
                    delta=delta,
                )
            )


def _wav_files(path: Path) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".wav":
        return [path]
    return sorted(item for item in path.rglob("*.wav") if item.is_file())


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}
