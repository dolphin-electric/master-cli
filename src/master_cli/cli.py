from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import AuditSettings, audit_directory
from .audio import export_audio, read_audio
from .config import load_mix_config
from .errors import AgentError
from .job import job_to_argv, load_job
from .master import MasterSettings, analyze_audio, master_audio
from .mix import mix_stems
from .presets import preset_names, settings_from_mapping_with_preset, settings_from_preset
from .reference import ReferenceComparison, compare_to_reference
from .report import default_report_path, serialize, write_report
from .schema import schemas_payload


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, json_errors: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._json_errors = json_errors

    def error(self, message: str) -> None:
        if self._json_errors:
            payload = {
                "status": "error",
                "code": "invalid_arguments",
                "message": message,
                "field": None,
            }
            print(json.dumps(serialize(payload), sort_keys=True), file=sys.stderr)
            raise SystemExit(2)
        super().error(message)


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser(json_errors=_argv_wants_json(raw_argv))
    args = parser.parse_args(raw_argv)
    try:
        args.handler(args)
    except AgentError as exc:
        _emit_error(exc, json_mode=_wants_json(args))
    except Exception as exc:
        if _wants_json(args):
            _emit_error(AgentError("runtime_error", str(exc)), json_mode=True)
        raise


def _build_parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="master-cli", description="Mix and master audio files.", json_errors=json_errors)
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=lambda *args, **kwargs: JsonArgumentParser(*args, json_errors=json_errors, **kwargs),
    )

    mix_parser = subparsers.add_parser("mix", help="Mix stems from a YAML config.")
    mix_parser.add_argument("config", type=Path)
    mix_parser.add_argument("output", type=Path)
    mix_parser.add_argument("--subtype", default="PCM_24", help="SoundFile output subtype, e.g. PCM_24 or FLOAT.")
    mix_parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing audio.")
    mix_parser.add_argument("--report", nargs="?", const="auto", help="Write a JSON render report.")
    mix_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    mix_parser.set_defaults(handler=_mix_command)

    master_parser = subparsers.add_parser("master", help="Master an existing audio file.")
    master_parser.add_argument("input", type=Path)
    master_parser.add_argument("output", type=Path)
    master_parser.add_argument("--preset", choices=preset_names(), help="Use a built-in mastering preset.")
    master_parser.add_argument("--reference", type=Path, help="Compare the mastered output against a reference track.")
    master_parser.add_argument("--target-lufs", type=float)
    master_parser.add_argument("--ceiling-db", type=float)
    master_parser.add_argument("--highpass-hz", type=float)
    master_parser.add_argument("--stereo-width", type=float)
    master_parser.add_argument("--lookahead-ms", type=float)
    master_parser.add_argument("--release-ms", type=float)
    master_parser.add_argument("--oversample-factor", type=int)
    master_parser.add_argument("--subtype", default="PCM_24")
    master_parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing audio.")
    master_parser.add_argument("--report", nargs="?", const="auto", help="Write a JSON render report.")
    master_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    master_parser.set_defaults(handler=_master_command)

    render_parser = subparsers.add_parser("render", help="Mix and master stems from a YAML config.")
    render_parser.add_argument("config", type=Path)
    render_parser.add_argument("output", type=Path)
    render_parser.add_argument("--preset", choices=preset_names(), help="Override the config master chain with a preset.")
    render_parser.add_argument("--reference", type=Path, help="Compare the rendered output against a reference track.")
    render_parser.add_argument("--subtype", default="PCM_24")
    render_parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing audio.")
    render_parser.add_argument("--report", nargs="?", const="auto", help="Write a JSON render report.")
    render_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    render_parser.set_defaults(handler=_render_command)

    batch_parser = subparsers.add_parser("batch", help="Master every audio file in a folder.")
    batch_parser.add_argument("input_dir", type=Path)
    batch_parser.add_argument("output_dir", type=Path)
    batch_parser.add_argument("--preset", choices=preset_names(), help="Use a built-in mastering preset.")
    batch_parser.add_argument("--reference", type=Path, help="Compare each mastered output against a reference track.")
    batch_parser.add_argument("--target-lufs", type=float)
    batch_parser.add_argument("--ceiling-db", type=float)
    batch_parser.add_argument("--highpass-hz", type=float)
    batch_parser.add_argument("--stereo-width", type=float)
    batch_parser.add_argument("--lookahead-ms", type=float)
    batch_parser.add_argument("--release-ms", type=float)
    batch_parser.add_argument("--oversample-factor", type=int)
    batch_parser.add_argument("--extension", default=".wav", help="Output extension, e.g. .wav, .flac, or .mp3.")
    batch_parser.add_argument("--subtype", default="PCM_24")
    batch_parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing audio.")
    batch_parser.add_argument("--report", action="store_true", help="Write one JSON report per output file.")
    batch_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    batch_parser.set_defaults(handler=_batch_command)

    analyze_parser = subparsers.add_parser("analyze", help="Print LUFS and peak stats for an audio file.")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    analyze_parser.set_defaults(handler=_analyze_command)

    compare_parser = subparsers.add_parser("compare", help="Compare an audio file against a reference track.")
    compare_parser.add_argument("input", type=Path)
    compare_parser.add_argument("reference", type=Path)
    compare_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    compare_parser.set_defaults(handler=_compare_command)

    audit_parser = subparsers.add_parser("audit", help="Audit rendered WAV files and matching JSON reports.")
    audit_parser.add_argument("path", type=Path, help="WAV file or directory to audit.")
    audit_parser.add_argument("--target-lufs", type=float, help="Expected integrated LUFS target.")
    audit_parser.add_argument("--lufs-tolerance", type=float, default=0.5, help="Allowed LUFS deviation from target.")
    audit_parser.add_argument("--ceiling-db", type=float, default=-1.0, help="Maximum allowed peak in dBFS.")
    audit_parser.add_argument("--stats-tolerance", type=float, default=0.05, help="Allowed report-vs-audio stats delta.")
    audit_parser.add_argument("--no-require-reports", action="store_true", help="Do not flag missing report JSON files.")
    audit_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    audit_parser.set_defaults(handler=_audit_command)

    presets_parser = subparsers.add_parser("presets", help="List built-in mastering presets.")
    presets_parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    presets_parser.set_defaults(handler=_presets_command)

    schema_parser = subparsers.add_parser("schema", help="Print JSON schemas and output contracts for agent callers.")
    schema_parser.set_defaults(handler=_schema_command)

    job_parser = subparsers.add_parser("job", help="Run a structured JSON job request.")
    job_parser.add_argument("job", type=Path)
    job_parser.set_defaults(handler=_job_command)

    return parser


def _mix_command(args: argparse.Namespace) -> None:
    stems, mix_settings, _ = load_mix_config(args.config)
    plan = _mix_payload("dry_run" if args.dry_run else "ok", args.config, args.output, stems, mix_settings)
    if not args.json:
        _print_mix_plan(args.config, args.output, len(stems), mix_settings.sample_rate)
    if args.dry_run:
        _emit_json(plan, args)
        return

    mix = mix_stems(stems, mix_settings)
    export_audio(args.output, mix, mix_settings.sample_rate, subtype=args.subtype)
    output_stats = analyze_audio(mix, mix_settings.sample_rate)
    report_path = None
    if args.report:
        report_path = _report_path(args.report, args.output)
        write_report(
            report_path,
            command="mix",
            output=args.output,
            sample_rate=mix_settings.sample_rate,
            input_stats=None,
            output_stats=output_stats,
            mix_settings=mix_settings,
            stems=stems,
        )
    payload = {
        **plan,
        "output_stats": output_stats,
        "report": str(report_path) if report_path else None,
    }
    _emit_json(payload, args)
    if not args.json:
        print(f"Wrote mix: {args.output}")


def _master_command(args: argparse.Namespace) -> None:
    source = read_audio(args.input)
    settings = _settings_from_args(args)
    if not args.json:
        _print_master_plan(args.input, args.output, settings)
    input_stats = analyze_audio(source.data, source.sample_rate)
    if args.dry_run:
        payload = _master_payload(
            "dry_run",
            args.input,
            args.output,
            source.sample_rate,
            settings,
            input_stats=input_stats,
        )
        _emit_json(payload, args)
        if not args.json:
            _print_stats_from_value(input_stats)
        return

    mastered = master_audio(source.data, source.sample_rate, settings)
    export_audio(args.output, mastered, source.sample_rate, subtype=args.subtype)
    output_stats = analyze_audio(mastered, source.sample_rate)
    reference_analysis = _reference_comparison(mastered, source.sample_rate, args.reference)
    if not args.json:
        _print_stats_from_value(output_stats)
    if reference_analysis and not args.json:
        _print_reference_comparison(reference_analysis)
    report_path = None
    if args.report:
        report_path = _report_path(args.report, args.output)
        write_report(
            report_path,
            command="master",
            output=args.output,
            sample_rate=source.sample_rate,
            input_stats=input_stats,
            output_stats=output_stats,
            master_settings=settings,
            reference_analysis=reference_analysis,
        )
    payload = _master_payload(
        "ok",
        args.input,
        args.output,
        source.sample_rate,
        settings,
        input_stats=input_stats,
        output_stats=output_stats,
        reference_analysis=reference_analysis,
        report_path=report_path,
    )
    _emit_json(payload, args)
    if not args.json:
        print(f"Wrote master: {args.output}")


def _render_command(args: argparse.Namespace) -> None:
    stems, mix_settings, master_settings = load_mix_config(args.config)
    if args.preset:
        master_settings = settings_from_preset(args.preset)
    if not args.json:
        _print_mix_plan(args.config, args.output, len(stems), mix_settings.sample_rate)
    if args.dry_run:
        _emit_json(
            _render_payload("dry_run", args.config, args.output, stems, mix_settings, master_settings),
            args,
        )
        return

    mix = mix_stems(stems, mix_settings)
    input_stats = analyze_audio(mix, mix_settings.sample_rate)
    mastered = master_audio(mix, mix_settings.sample_rate, master_settings)
    export_audio(args.output, mastered, mix_settings.sample_rate, subtype=args.subtype)
    output_stats = analyze_audio(mastered, mix_settings.sample_rate)
    reference_analysis = _reference_comparison(mastered, mix_settings.sample_rate, args.reference)
    if not args.json:
        _print_stats_from_value(output_stats)
    if reference_analysis and not args.json:
        _print_reference_comparison(reference_analysis)
    report_path = None
    if args.report:
        report_path = _report_path(args.report, args.output)
        write_report(
            report_path,
            command="render",
            output=args.output,
            sample_rate=mix_settings.sample_rate,
            input_stats=input_stats,
            output_stats=output_stats,
            mix_settings=mix_settings,
            master_settings=master_settings,
            stems=stems,
            reference_analysis=reference_analysis,
        )
    payload = {
        **_render_payload("ok", args.config, args.output, stems, mix_settings, master_settings),
        "input_stats": input_stats,
        "output_stats": output_stats,
        "reference_analysis": reference_analysis,
        "report": str(report_path) if report_path else None,
    }
    _emit_json(payload, args)
    if not args.json:
        print(f"Wrote render: {args.output}")


def _batch_command(args: argparse.Namespace) -> None:
    inputs = _audio_files(args.input_dir)
    settings = _settings_from_args(args)
    if not args.json:
        print(f"Batch mastering {len(inputs)} file(s) from {args.input_dir} to {args.output_dir}")
    if args.dry_run:
        outputs = [
            {"input": str(source), "output": str(_batch_output_path(source, args.input_dir, args.output_dir, args.extension))}
            for source in inputs
        ]
        _emit_json(
            {
                "status": "dry_run",
                "command": "batch",
                "input_dir": str(args.input_dir),
                "output_dir": str(args.output_dir),
                "master_settings": settings,
                "files": outputs,
            },
            args,
        )
        for source in inputs:
            if not args.json:
                print(f"{source} -> {_batch_output_path(source, args.input_dir, args.output_dir, args.extension)}")
        return

    results = []
    for source_path in inputs:
        source = read_audio(source_path)
        output_path = _batch_output_path(source_path, args.input_dir, args.output_dir, args.extension)
        input_stats = analyze_audio(source.data, source.sample_rate)
        mastered = master_audio(source.data, source.sample_rate, settings)
        export_audio(output_path, mastered, source.sample_rate, subtype=args.subtype)
        output_stats = analyze_audio(mastered, source.sample_rate)
        reference_analysis = _reference_comparison(mastered, source.sample_rate, args.reference)
        report_path = None
        if args.report:
            report_path = default_report_path(output_path)
            write_report(
                report_path,
                command="batch",
                output=output_path,
                sample_rate=source.sample_rate,
                input_stats=input_stats,
                output_stats=output_stats,
                master_settings=settings,
                reference_analysis=reference_analysis,
            )
        results.append(
            {
                "input": str(source_path),
                "output": str(output_path),
                "sample_rate": source.sample_rate,
                "input_stats": input_stats,
                "output_stats": output_stats,
                "reference_analysis": reference_analysis,
                "report": str(report_path) if report_path else None,
            }
        )
        if not args.json:
            print(f"Wrote master: {output_path}")
        if reference_analysis and not args.json:
            _print_reference_comparison(reference_analysis)
    _emit_json(
        {
            "status": "ok",
            "command": "batch",
            "input_dir": str(args.input_dir),
            "output_dir": str(args.output_dir),
            "master_settings": settings,
            "files": results,
        },
        args,
    )


def _analyze_command(args: argparse.Namespace) -> None:
    source = read_audio(args.input)
    stats = analyze_audio(source.data, source.sample_rate)
    _emit_json(
        {"status": "ok", "command": "analyze", "input": str(args.input), "sample_rate": source.sample_rate, "stats": stats},
        args,
    )
    if not args.json:
        _print_stats_from_value(stats)


def _compare_command(args: argparse.Namespace) -> None:
    source = read_audio(args.input)
    reference = read_audio(args.reference)
    comparison = compare_to_reference(source.data, source.sample_rate, reference.data, reference.sample_rate)
    _emit_json(
        {
            "status": "ok",
            "command": "compare",
            "input": str(args.input),
            "reference": str(args.reference),
            "comparison": comparison,
        },
        args,
    )
    if not args.json:
        _print_reference_comparison(comparison)


def _audit_command(args: argparse.Namespace) -> None:
    payload = audit_directory(
        args.path,
        AuditSettings(
            target_lufs=args.target_lufs,
            lufs_tolerance=args.lufs_tolerance,
            ceiling_db=args.ceiling_db,
            stats_tolerance=args.stats_tolerance,
            require_reports=not args.no_require_reports,
        ),
    )
    _emit_json(payload, args)
    if args.json:
        return

    summary = payload["summary"]
    print(
        f"Audited {summary['files']} file(s): "
        f"{summary['passed']} passed, {summary['failed']} failed, {summary['issues']} issue(s)"
    )
    for item in payload["files"]:
        if not item["issues"]:
            continue
        print(item["path"])
        for issue in item["issues"]:
            print(f"  [{issue['code']}] {issue['message']}")


def _presets_command(args: argparse.Namespace) -> None:
    payload = {
        "status": "ok",
        "command": "presets",
        "presets": {name: settings_from_preset(name) for name in preset_names()},
    }
    _emit_json(payload, args)
    if args.json:
        return
    for name in preset_names():
        settings = settings_from_preset(name)
        limiter = settings.limiter
        ceiling = limiter.ceiling_db if limiter else settings.ceiling_db
        print(f"{name}: target {settings.target_lufs:g} LUFS, ceiling {ceiling:g} dBFS")


def _schema_command(args: argparse.Namespace) -> None:
    print(json.dumps(serialize(schemas_payload()), indent=2, sort_keys=True))


def _job_command(args: argparse.Namespace) -> None:
    job = load_job(args.job)
    main(job_to_argv(job))


def _print_stats(data, sample_rate: int) -> None:
    stats = analyze_audio(data, sample_rate)
    _print_stats_from_value(stats)


def _print_stats_from_value(stats) -> None:
    print(f"Integrated LUFS: {stats.integrated_lufs:.2f}")
    print(f"Peak: {stats.peak_db:.2f} dBFS")


def _settings_from_args(args: argparse.Namespace) -> MasterSettings:
    values: dict = {}
    if getattr(args, "preset", None):
        values["preset"] = args.preset
    for option in ("target_lufs", "ceiling_db", "highpass_hz", "stereo_width"):
        value = getattr(args, option, None)
        if value is not None:
            values[option] = value

    limiter: dict = {}
    ceiling_db = getattr(args, "ceiling_db", None)
    if ceiling_db is not None:
        limiter["ceiling_db"] = ceiling_db
    lookahead_ms = getattr(args, "lookahead_ms", None)
    if lookahead_ms is not None:
        limiter["lookahead_ms"] = lookahead_ms
    release_ms = getattr(args, "release_ms", None)
    if release_ms is not None:
        limiter["release_ms"] = release_ms
    oversample_factor = getattr(args, "oversample_factor", None)
    if oversample_factor is not None:
        limiter["oversample_factor"] = oversample_factor
    if limiter:
        values["limiter"] = limiter

    return settings_from_mapping_with_preset(values)


def _report_path(value: str, output: Path) -> Path:
    if value == "auto":
        return default_report_path(output)
    return Path(value)


def _audio_files(path: Path) -> list[Path]:
    if not path.exists():
        raise AgentError("input_not_found", f"Input directory does not exist: {path}", "input_dir")
    if not path.is_dir():
        raise AgentError("invalid_input_dir", f"Input path is not a directory: {path}", "input_dir")
    supported = {".wav", ".flac", ".aiff", ".aif", ".ogg"}
    return sorted(item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in supported)


def _batch_output_path(source: Path, input_dir: Path, output_dir: Path, extension: str) -> Path:
    suffix = extension if extension.startswith(".") else f".{extension}"
    relative = source.relative_to(input_dir)
    return (output_dir / relative).with_suffix(suffix)


def _print_mix_plan(config: Path, output: Path, stem_count: int, sample_rate: int) -> None:
    print(f"Config: {config}")
    print(f"Stems: {stem_count}")
    print(f"Sample rate: {sample_rate}")
    print(f"Output: {output}")


def _print_master_plan(input_path: Path, output: Path, settings: MasterSettings) -> None:
    limiter = settings.limiter
    print(f"Input: {input_path}")
    print(f"Output: {output}")
    print(f"Target LUFS: {settings.target_lufs}")
    print(f"Ceiling: {(limiter.ceiling_db if limiter else settings.ceiling_db)} dBFS")
    print(f"Lookahead: {(limiter.lookahead_ms if limiter else 5.0)} ms")


def _reference_comparison(data, sample_rate: int, reference_path: Path | None) -> ReferenceComparison | None:
    if reference_path is None:
        return None
    reference = read_audio(reference_path)
    return compare_to_reference(data, sample_rate, reference.data, reference.sample_rate)


def _print_reference_comparison(comparison: ReferenceComparison) -> None:
    deltas = comparison.deltas
    tonal = deltas["tonal_balance_db"]
    print("Reference delta:")
    print(f"  LUFS: {deltas['integrated_lufs']:+.2f}")
    print(f"  Peak: {deltas['peak_db']:+.2f} dB")
    print(f"  Crest factor: {deltas['crest_factor_db']:+.2f} dB")
    print(f"  Stereo width: {deltas['stereo_width']:+.3f}")
    if isinstance(tonal, dict):
        tone = ", ".join(f"{name} {value:+.2f} dB" for name, value in tonal.items())
        print(f"  Tonal balance: {tone}")


def _emit_json(payload: dict, args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        print(json.dumps(serialize(payload), sort_keys=True))


def _emit_error(error: AgentError, *, json_mode: bool) -> None:
    if json_mode:
        payload = {
            "status": "error",
            "code": error.code,
            "message": error.message,
            "field": error.field,
        }
        print(json.dumps(serialize(payload), sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
    raise error


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "command", None) == "job")


def _argv_wants_json(argv: list[str]) -> bool:
    return "--json" in argv or (bool(argv) and argv[0] == "job")


def _mix_payload(status: str, config: Path, output: Path, stems, mix_settings) -> dict:
    return {
        "status": status,
        "command": "mix",
        "config": str(config),
        "output": str(output),
        "mix_settings": mix_settings,
        "stems": stems,
    }


def _master_payload(
    status: str,
    input_path: Path,
    output: Path,
    sample_rate: int,
    settings: MasterSettings,
    *,
    input_stats=None,
    output_stats=None,
    reference_analysis=None,
    report_path: Path | None = None,
) -> dict:
    return {
        "status": status,
        "command": "master",
        "input": str(input_path),
        "output": str(output),
        "sample_rate": sample_rate,
        "master_settings": settings,
        "input_stats": input_stats,
        "output_stats": output_stats,
        "reference_analysis": reference_analysis,
        "report": str(report_path) if report_path else None,
    }


def _render_payload(status: str, config: Path, output: Path, stems, mix_settings, master_settings) -> dict:
    return {
        "status": status,
        "command": "render",
        "config": str(config),
        "output": str(output),
        "mix_settings": mix_settings,
        "master_settings": master_settings,
        "stems": stems,
    }
