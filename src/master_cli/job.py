from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import AgentError
from .presets import preset_names


SUPPORTED_COMMANDS = {"mix", "master", "render", "batch", "analyze", "compare", "audit", "presets", "schema"}


def load_job(path: str | Path) -> dict[str, Any]:
    job_path = Path(path)
    try:
        with job_path.open("r", encoding="utf-8") as handle:
            values = json.load(handle)
    except FileNotFoundError as exc:
        raise AgentError("job_not_found", f"Job file does not exist: {job_path}", "job") from exc
    except json.JSONDecodeError as exc:
        raise AgentError("invalid_json", f"Job file is not valid JSON: {exc.msg}", "job") from exc

    if not isinstance(values, dict):
        raise AgentError("invalid_job", "Job file must contain a JSON object.", "job")
    return values


def job_to_argv(job: dict[str, Any]) -> list[str]:
    command = _required_string(job, "command")
    if command not in SUPPORTED_COMMANDS:
        raise AgentError(
            "unsupported_command",
            f"Unsupported job command: {command}. Supported commands: {', '.join(sorted(SUPPORTED_COMMANDS))}",
            "command",
        )

    if command == "mix":
        return _mix_argv(job)
    if command == "master":
        return _master_argv(job)
    if command == "render":
        return _render_argv(job)
    if command == "batch":
        return _batch_argv(job)
    if command == "analyze":
        return ["analyze", _required_string(job, "input"), "--json"]
    if command == "compare":
        return ["compare", _required_string(job, "input"), _required_string(job, "reference"), "--json"]
    if command == "audit":
        return _audit_argv(job)
    if command == "presets":
        return ["presets", "--json"]
    if command == "schema":
        return ["schema"]

    raise AgentError("unsupported_command", f"Unsupported job command: {command}", "command")


def _mix_argv(job: dict[str, Any]) -> list[str]:
    argv = ["mix", _required_string(job, "config"), _required_string(job, "output")]
    _append_common_output_options(argv, job, batch=False)
    return argv


def _master_argv(job: dict[str, Any]) -> list[str]:
    argv = ["master", _required_string(job, "input"), _required_string(job, "output")]
    _append_master_options(argv, job)
    _append_common_output_options(argv, job, batch=False)
    return argv


def _render_argv(job: dict[str, Any]) -> list[str]:
    argv = ["render", _required_string(job, "config"), _required_string(job, "output")]
    _append_optional_string(argv, "--preset", job, "preset")
    _append_optional_string(argv, "--reference", job, "reference")
    _append_common_output_options(argv, job, batch=False)
    return argv


def _batch_argv(job: dict[str, Any]) -> list[str]:
    argv = ["batch", _required_string(job, "input_dir"), _required_string(job, "output_dir")]
    _append_master_options(argv, job)
    _append_optional_string(argv, "--extension", job, "extension")
    _append_common_output_options(argv, job, batch=True)
    return argv


def _audit_argv(job: dict[str, Any]) -> list[str]:
    argv = ["audit", _required_string(job, "path")]
    _append_optional_number(argv, "--target-lufs", job, "target_lufs")
    _append_optional_number(argv, "--lufs-tolerance", job, "lufs_tolerance")
    _append_optional_number(argv, "--ceiling-db", job, "ceiling_db")
    _append_optional_number(argv, "--stats-tolerance", job, "stats_tolerance")
    if job.get("require_reports") is False:
        argv.append("--no-require-reports")
    elif job.get("require_reports") not in {None, True}:
        raise AgentError("invalid_field", "require_reports must be a boolean.", "require_reports")
    argv.append("--json")
    return argv


def _append_master_options(argv: list[str], job: dict[str, Any]) -> None:
    _append_optional_string(argv, "--preset", job, "preset")
    _append_optional_string(argv, "--reference", job, "reference")
    _append_optional_number(argv, "--target-lufs", job, "target_lufs")
    _append_optional_number(argv, "--ceiling-db", job, "ceiling_db")
    _append_optional_number(argv, "--highpass-hz", job, "highpass_hz")
    _append_optional_number(argv, "--stereo-width", job, "stereo_width")
    _append_optional_number(argv, "--lookahead-ms", job, "lookahead_ms")
    _append_optional_number(argv, "--release-ms", job, "release_ms")
    _append_optional_integer(argv, "--oversample-factor", job, "oversample_factor")


def _append_common_output_options(argv: list[str], job: dict[str, Any], *, batch: bool) -> None:
    _append_optional_string(argv, "--subtype", job, "subtype")
    report = job.get("report")
    if isinstance(report, str) and not batch:
        argv.extend(["--report", report])
    elif report is True:
        argv.append("--report")
    elif report not in {None, False}:
        raise AgentError("invalid_field", "report must be true, false, null, or a string path for non-batch commands.", "report")

    if job.get("dry_run") is True:
        argv.append("--dry-run")
    elif job.get("dry_run") not in {None, False}:
        raise AgentError("invalid_field", "dry_run must be a boolean.", "dry_run")

    argv.append("--json")


def _append_optional_string(argv: list[str], flag: str, job: dict[str, Any], field: str) -> None:
    value = job.get(field)
    if value is None:
        return
    if not isinstance(value, str):
        raise AgentError("invalid_field", f"{field} must be a string.", field)
    if field == "preset" and value not in preset_names():
        raise AgentError("invalid_preset", f"Unknown preset: {value}.", field)
    argv.extend([flag, value])


def _append_optional_number(argv: list[str], flag: str, job: dict[str, Any], field: str) -> None:
    value = job.get(field)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentError("invalid_field", f"{field} must be a number.", field)
    argv.extend([flag, str(value)])


def _append_optional_integer(argv: list[str], flag: str, job: dict[str, Any], field: str) -> None:
    value = job.get(field)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentError("invalid_field", f"{field} must be an integer.", field)
    argv.extend([flag, str(value)])


def _required_string(job: dict[str, Any], field: str) -> str:
    value = job.get(field)
    if value is None:
        raise AgentError("missing_field", f"Missing required field: {field}", field)
    if not isinstance(value, str) or not value:
        raise AgentError("invalid_field", f"{field} must be a non-empty string.", field)
    return value
