from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from master_cli.cli import main
from master_cli.effects import apply_effects, effects_from_mapping
from master_cli.master import (
    CompressorSettings,
    EQBand,
    LimiterSettings,
    MasterSettings,
    analyze_audio,
    apply_eq,
    limit_peak,
    limit_true_peak,
    lookahead_limit,
    master_audio,
    true_peak,
)
from master_cli.mix import MixSettings, Stem, mix_stems
from master_cli.presets import settings_from_mapping_with_preset, settings_from_preset
from master_cli.reference import compare_to_reference, profile_audio
from master_cli.report import default_report_path


def test_mix_stems_applies_gain_and_headroom(tmp_path):
    sample_rate = 48_000
    tone = np.ones((sample_rate // 10, 1), dtype=np.float64) * 0.5
    source = tmp_path / "tone.wav"
    sf.write(source, tone, sample_rate)

    mix = mix_stems([Stem(path=source, gain_db=6.0)], MixSettings(sample_rate=sample_rate, headroom_db=-6.0))

    assert mix.shape == (sample_rate // 10, 2)
    assert np.max(np.abs(mix)) <= 10 ** (-6.0 / 20.0) + 1e-9


def test_mix_stems_honors_start_offset(tmp_path):
    sample_rate = 1_000
    tone = np.ones((100, 1), dtype=np.float64) * 0.25
    source = tmp_path / "tone.wav"
    sf.write(source, tone, sample_rate)

    mix = mix_stems([Stem(path=source, start=0.1)], MixSettings(sample_rate=sample_rate))

    assert np.allclose(mix[:100], 0.0)
    assert mix.shape[0] == 200


def test_mix_stems_applies_stem_processing(tmp_path):
    sample_rate = 48_000
    time = np.arange(sample_rate // 2, dtype=np.float64) / sample_rate
    tone = 0.05 * np.sin(2 * np.pi * 1000 * time)
    source = tmp_path / "tone.wav"
    sf.write(source, tone, sample_rate)

    plain = mix_stems([Stem(path=source)], MixSettings(sample_rate=sample_rate, headroom_db=0.0))
    processed = mix_stems(
        [
            Stem(
                path=source,
                highpass_hz=60.0,
                eq=[EQBand(kind="bell", frequency_hz=1000.0, gain_db=6.0, q=1.0)],
                compressor=CompressorSettings(threshold_db=-30.0, ratio=2.0, attack_ms=5.0, release_ms=80.0),
            )
        ],
        MixSettings(sample_rate=sample_rate, headroom_db=0.0),
    )

    assert processed.shape == plain.shape
    assert not np.allclose(processed, plain)


def test_limit_peak_keeps_audio_under_ceiling():
    audio = np.array([[0.0], [2.0], [-2.0]], dtype=np.float64)

    limited = limit_peak(audio, -1.0)

    assert np.max(np.abs(limited)) <= 10 ** (-1.0 / 20.0) + 1e-12


def test_lookahead_limiter_keeps_transient_under_ceiling():
    sample_rate = 48_000
    audio = np.zeros((sample_rate // 10, 2), dtype=np.float64)
    audio[1000] = [2.0, -2.0]

    limited = lookahead_limit(audio, sample_rate, ceiling_db=-3.0, lookahead_ms=5.0)

    assert np.max(np.abs(limited)) <= 10 ** (-3.0 / 20.0) + 1e-12


def test_true_peak_limiter_uses_oversampled_peak():
    sample_rate = 48_000
    time = np.arange(64, dtype=np.float64) / sample_rate
    audio = 0.95 * np.sin(2 * np.pi * 18_000 * time)[:, np.newaxis]

    limited = limit_true_peak(audio, ceiling_db=-6.0, oversample_factor=4)

    assert true_peak(limited, oversample_factor=4) <= 10 ** (-6.0 / 20.0) + 1e-9


def test_eq_band_changes_audio_without_shape_change():
    sample_rate = 48_000
    time = np.arange(sample_rate // 2, dtype=np.float64) / sample_rate
    audio = 0.05 * np.sin(2 * np.pi * 1000 * time)[:, np.newaxis]

    equalized = apply_eq(audio, sample_rate, [EQBand(kind="bell", frequency_hz=1000, gain_db=6.0, q=1.0)])

    assert equalized.shape == audio.shape
    assert not np.allclose(equalized, audio)


def test_pedalboard_effects_process_audio_when_installed():
    pytest.importorskip("pedalboard")
    sample_rate = 48_000
    time = np.arange(sample_rate // 10, dtype=np.float64) / sample_rate
    audio = 0.05 * np.sin(2 * np.pi * 440 * time)[:, np.newaxis]

    processed = apply_effects(audio, sample_rate, effects_from_mapping([{"type": "pedalboard.Gain", "gain_db": 6.0}]))

    assert processed.shape == audio.shape
    assert np.max(np.abs(processed)) > np.max(np.abs(audio))


def test_master_audio_returns_finite_stereo_audio():
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    audio = 0.1 * np.column_stack(
        [
            np.sin(2 * np.pi * 220 * time),
            np.sin(2 * np.pi * 224 * time),
        ]
    )

    mastered = master_audio(
        audio,
        sample_rate,
        MasterSettings(target_lufs=-18.0, ceiling_db=-2.0, limiter=LimiterSettings(ceiling_db=-2.0)),
    )
    stats = analyze_audio(mastered, sample_rate)

    assert mastered.shape == audio.shape
    assert np.isfinite(mastered).all()
    assert stats.peak_db <= -2.0 + 1e-6


def test_master_settings_ceiling_db_controls_default_limiter():
    sample_rate = 48_000
    audio = np.ones((sample_rate, 2), dtype=np.float64) * 0.5

    mastered = master_audio(audio, sample_rate, MasterSettings(target_lufs=-1.0, ceiling_db=-6.0))

    assert np.max(np.abs(mastered)) <= 10 ** (-6.0 / 20.0) + 1e-12


def test_preset_settings_can_be_overridden():
    settings = settings_from_mapping_with_preset({"preset": "club", "target_lufs": -12.0})

    assert settings.target_lufs == -12.0
    assert settings.limiter is not None
    assert settings.limiter.ceiling_db == settings_from_preset("club").limiter.ceiling_db


def test_reference_comparison_reports_expected_deltas():
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    source = 0.05 * np.sin(2 * np.pi * 440 * time)[:, np.newaxis]
    reference = 0.10 * np.sin(2 * np.pi * 440 * time)[:, np.newaxis]

    source_profile = profile_audio(source, sample_rate)
    comparison = compare_to_reference(source, sample_rate, reference, sample_rate)

    assert np.isfinite(source_profile.crest_factor_db)
    assert comparison.deltas["rms_db"] < 0.0
    assert "mid" in comparison.deltas["tonal_balance_db"]


def test_render_writes_report(tmp_path):
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    source = tmp_path / "tone.wav"
    sf.write(source, 0.05 * np.sin(2 * np.pi * 220 * time), sample_rate)
    config = tmp_path / "mix.yaml"
    config.write_text(
        """
sample_rate: 48000
output_channels: 2
master:
  target_lufs: -18
  limiter:
    ceiling_db: -2
stems:
  - path: tone.wav
""",
        encoding="utf-8",
    )
    output = tmp_path / "master.wav"

    main(["render", str(config), str(output), "--report"])

    report = default_report_path(output)
    assert output.exists()
    assert report.exists()
    assert '"command": "render"' in report.read_text(encoding="utf-8")


def test_master_report_includes_reference_analysis(tmp_path):
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    source = tmp_path / "mix.wav"
    reference = tmp_path / "reference.wav"
    sf.write(source, 0.05 * np.sin(2 * np.pi * 220 * time), sample_rate)
    sf.write(reference, 0.08 * np.sin(2 * np.pi * 330 * time), sample_rate)
    output = tmp_path / "master.wav"

    main(["master", str(source), str(output), "--preset", "streaming", "--reference", str(reference), "--report"])

    payload = json.loads(default_report_path(output).read_text(encoding="utf-8"))
    assert output.exists()
    assert payload["master_settings"]["target_lufs"] == -14.0
    assert payload["reference_analysis"]["deltas"]["integrated_lufs"] is not None


def test_render_dry_run_does_not_write_audio(tmp_path):
    config = tmp_path / "mix.yaml"
    config.write_text(
        """
sample_rate: 48000
stems:
  - path: missing.wav
""",
        encoding="utf-8",
    )
    output = tmp_path / "master.wav"

    main(["render", str(config), str(output), "--dry-run"])

    assert not output.exists()


def test_render_dry_run_json_outputs_machine_plan(tmp_path, capsys):
    config = tmp_path / "mix.yaml"
    config.write_text(
        """
sample_rate: 48000
master:
  preset: streaming
stems:
  - path: missing.wav
""",
        encoding="utf-8",
    )
    output = tmp_path / "master.wav"

    main(["render", str(config), str(output), "--dry-run", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["command"] == "render"
    assert payload["master_settings"]["target_lufs"] == -14.0
    assert payload["output"] == str(output)


def test_presets_json_outputs_resolved_settings(capsys):
    main(["presets", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert "streaming" in payload["presets"]
    assert payload["presets"]["streaming"]["target_lufs"] == -14.0


def test_schema_command_outputs_mix_config_schema(capsys):
    main(["schema"])

    payload = json.loads(capsys.readouterr().out)
    assert "job" in payload
    assert "effects" in payload
    assert payload["mix_config"]["required"] == ["stems"]
    assert "compressor" in payload["stem"]["properties"]
    assert "effects" in payload["stem"]["properties"]
    assert "oversample_factor" in payload["master_settings"]["properties"]["limiter"]["properties"]
    assert "json_output_contract" in payload


def test_audit_reports_clean_wav_with_matching_report(tmp_path, capsys):
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    output = tmp_path / "master.wav"
    audio = 0.05 * np.sin(2 * np.pi * 220 * time)
    sf.write(output, audio, sample_rate)
    stats = analyze_audio(audio[:, np.newaxis], sample_rate)
    default_report_path(output).write_text(
        json.dumps(
            {
                "command": "master",
                "output": str(output),
                "sample_rate": sample_rate,
                "output_stats": {
                    "integrated_lufs": stats.integrated_lufs,
                    "peak_db": stats.peak_db,
                    "peak": stats.peak,
                },
            }
        ),
        encoding="utf-8",
    )

    main(["audit", str(tmp_path), "--ceiling-db", "-1", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["summary"]["files"] == 1
    assert payload["summary"]["issues"] == 0
    assert payload["files"][0]["info"]["sample_rate"] == sample_rate


def test_audit_flags_missing_report_and_peak(tmp_path, capsys):
    sample_rate = 48_000
    audio = np.ones((sample_rate, 2), dtype=np.float64) * 0.9
    output = tmp_path / "hot.wav"
    sf.write(output, audio, sample_rate)

    main(["audit", str(tmp_path), "--ceiling-db", "-3", "--json"])

    payload = json.loads(capsys.readouterr().out)
    codes = {issue["code"] for issue in payload["files"][0]["issues"]}
    assert payload["status"] == "warning"
    assert "missing_report" in codes
    assert "peak_above_ceiling" in codes


def test_job_command_runs_master_request_as_json(tmp_path, capsys):
    sample_rate = 48_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    source = tmp_path / "mix.wav"
    sf.write(source, 0.05 * np.sin(2 * np.pi * 220 * time), sample_rate)
    output = tmp_path / "master.wav"
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "command": "master",
                "input": str(source),
                "output": str(output),
                "preset": "streaming",
                "dry_run": True,
            }
        ),
        encoding="utf-8",
    )

    main(["job", str(job)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["command"] == "master"
    assert payload["master_settings"]["target_lufs"] == -14.0
    assert not output.exists()


def test_job_command_runs_audit_request_as_json(tmp_path, capsys):
    sample_rate = 48_000
    audio = np.zeros((sample_rate, 2), dtype=np.float64)
    source = tmp_path / "silent.wav"
    sf.write(source, audio, sample_rate)
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "command": "audit",
                "path": str(tmp_path),
                "require_reports": False,
            }
        ),
        encoding="utf-8",
    )

    main(["job", str(job)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "audit"
    assert payload["summary"]["files"] == 1


def test_job_command_emits_json_error_for_missing_field(tmp_path, capsys):
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"command": "master", "output": "master.wav"}), encoding="utf-8")

    try:
        main(["job", str(job)])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit for invalid job")

    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert payload["code"] == "missing_field"
    assert payload["field"] == "input"


def test_job_command_rejects_boolean_numeric_fields_as_json_error(tmp_path, capsys):
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps({"command": "master", "input": "mix.wav", "output": "master.wav", "target_lufs": True}),
        encoding="utf-8",
    )

    try:
        main(["job", str(job)])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit for invalid job")

    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_field"
    assert payload["field"] == "target_lufs"


def test_json_mode_emits_error_contract_for_parse_errors(tmp_path, capsys):
    output = tmp_path / "master.wav"

    try:
        main(["master", "missing.wav", str(output), "--target-lufs", "nope", "--json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected SystemExit for invalid arguments")

    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_arguments"
    assert "--target-lufs" in payload["message"]


def test_json_mode_emits_error_contract_for_runtime_errors(tmp_path, capsys):
    output = tmp_path / "master.wav"

    try:
        main(["master", str(tmp_path / "missing.wav"), str(output), "--json"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit for missing input")

    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert payload["code"] == "runtime_error"


def test_batch_missing_input_dir_emits_json_error(tmp_path, capsys):
    try:
        main(["batch", str(tmp_path / "missing"), str(tmp_path / "out"), "--json"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit for missing batch input")

    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert payload["code"] == "input_not_found"
    assert payload["field"] == "input_dir"


def test_audit_missing_path_emits_json_error(tmp_path, capsys):
    try:
        main(["audit", str(tmp_path / "missing"), "--json"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit for missing audit path")

    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "error"
    assert payload["code"] == "input_not_found"
    assert payload["field"] == "path"
