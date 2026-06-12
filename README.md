# master-cli

`master-cli` is a small Python stack for offline mixing and mastering audio files.
It can:

- mix one or more stems with gain, pan, start offsets, fades, mute, and solo
- resample inputs to one project sample rate
- export WAV/FLAC/AIFF through `soundfile`
- export MP3/AAC/M4A when `ffmpeg` is installed
- process stems with high-pass filtering, EQ, and compression before summing
- optionally process stems and masters with Spotify Pedalboard effects
- run a mastering chain with DC removal, high-pass filtering, EQ, stereo width, compression, LUFS normalization, and oversampled lookahead limiting
- write JSON reports with LUFS, peak, settings, and stem metadata
- use built-in presets and compare output against a reference track

This is meant for repeatable batch processing and rough masters. Critical commercial releases should still be checked in a dedicated DAW with trusted metering.

## Agent Usage

The CLI is designed to be called by LLM agents and automation. Prefer `--json` for operational commands so stdout is exactly one JSON object.

```bash
master-cli render examples/mix.yaml build/master.wav --json
master-cli render examples/mix.yaml build/master.wav --dry-run --json
master-cli master mix.wav master.wav --preset streaming --reference ref.wav --report --json
master-cli analyze master.wav --json
master-cli compare master.wav ref.wav --json
master-cli audit output --target-lufs -14 --ceiling-db -1 --json
master-cli presets --json
master-cli schema
```

Structured job requests are the preferred interface when another LLM is calling the tool:

```bash
master-cli job request.json
```

Example `request.json`:

```json
{
  "command": "master",
  "input": "mix.wav",
  "output": "master.wav",
  "preset": "streaming",
  "reference": "ref.wav",
  "report": true,
  "dry_run": false
}
```

The `job` command always delegates to the underlying command with JSON output enabled.

JSON outputs include:

- `status`: `ok` or `dry_run`
- `command`: command name
- resolved settings after preset expansion
- input/output paths
- audio stats where available
- report path when a report is written
- reference comparison payload when `--reference` is used

Use `master-cli schema` to retrieve JSON schemas for mix configs, master settings, stems, and the output contract.

JSON errors use this stable shape and are written to stderr:

```json
{
  "status": "error",
  "code": "missing_field",
  "message": "Missing required field: input",
  "field": "input"
}
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Install optional Pedalboard effects support:

```bash
python -m pip install -e ".[pedalboard]"
```

## Quick Start

Create a mix from a YAML file:

```bash
master-cli mix examples/mix.yaml build/mix.wav
```

Master an existing mix:

```bash
master-cli master build/mix.wav build/master.wav --target-lufs -14 --ceiling-db -1
```

Use a preset:

```bash
master-cli master build/mix.wav build/master.wav --preset streaming
```

List presets:

```bash
master-cli presets
```

Machine-readable presets:

```bash
master-cli presets --json
```

Mix and master in one pass:

```bash
master-cli render examples/mix.yaml build/master.wav
```

Write a render report:

```bash
master-cli render examples/mix.yaml build/master.wav --report
```

Compare against a reference while rendering:

```bash
master-cli render examples/mix.yaml build/master.wav --reference refs/target.wav --report
```

Preview a render without writing audio:

```bash
master-cli render examples/mix.yaml build/master.wav --dry-run
```

Batch-master a folder:

```bash
master-cli batch mixes masters --target-lufs -14 --ceiling-db -1 --report
```

Export MP3 if `ffmpeg` is installed:

```bash
master-cli master build/mix.wav build/master.mp3
```

Analyze loudness and peak level:

```bash
master-cli analyze build/master.wav
```

Audit a folder of rendered WAVs and matching reports:

```bash
master-cli audit output --target-lufs -14 --ceiling-db -1 --json
```

Compare any file to a reference:

```bash
master-cli compare build/master.wav refs/target.wav
```

## Mix Config

```yaml
sample_rate: 48000
output_channels: 2
master:
  preset: streaming
  target_lufs: -14.0
  ceiling_db: -1.0
  highpass_hz: 30.0
  eq:
    - type: low_shelf
      frequency_hz: 120
      gain_db: 0.8
      q: 0.707
    - type: bell
      frequency_hz: 3200
      gain_db: 1.2
      q: 1.1
  compressor:
    threshold_db: -18.0
    ratio: 2.0
    attack_ms: 20.0
    release_ms: 160.0
  effects:
    - type: pedalboard.Reverb
      room_size: 0.08
      wet_level: 0.05
      dry_level: 0.95
  stereo_width: 1.05
  limiter:
    ceiling_db: -1.0
    lookahead_ms: 5.0
    release_ms: 80.0
    oversample_factor: 4
stems:
  - path: audio/drums.wav
    gain_db: -3
    pan: 0
    fade_in: 0.02
    fade_out: 0.10
    highpass_hz: 35
    compressor:
      threshold_db: -16
      ratio: 2.5
      attack_ms: 10
      release_ms: 120
  - path: audio/vocal.wav
    gain_db: 1.5
    pan: 0
    start: 0.0
    highpass_hz: 80
    eq:
      - type: bell
        frequency_hz: 3500
        gain_db: 1.5
        q: 1.0
    effects:
      - type: pedalboard.Delay
        delay_seconds: 0.18
        mix: 0.08
```

Stem options:

- `path`: input audio path
- `gain_db`: gain before summing
- `pan`: `-1.0` left to `1.0` right
- `start`: seconds before the stem begins in the mix
- `fade_in`, `fade_out`: seconds
- `mute`: exclude this stem
- `solo`: if any stem has solo enabled, only solo stems are mixed
- `highpass_hz`: optional stem high-pass before gain/pan
- `eq`: optional stem EQ bands before gain/pan
- `effects`: optional Pedalboard effect chain before gain/pan
- `compressor`: optional stem compressor before gain/pan

Master EQ band types:

- `bell` or `peaking`
- `low_shelf`
- `high_shelf`

Pedalboard effects:

- Add `effects` to a stem or the `master` block
- `type` can be a Pedalboard built-in such as `pedalboard.Reverb`, `Delay`, `Chorus`, `Distortion`, `Phaser`, `PitchShift`, or `Bitcrush`
- For external plugins, use `type: vst3` or `type: au`, plus `path` and optional `parameters`
- Pedalboard is optional because it is GPLv3; install it only when that license is acceptable for your use case

Built-in presets:

- `streaming`: balanced default for streaming platforms
- `club`: louder, wider, and slightly brighter
- `podcast`: voice-forward compression and filtering
- `demo-loud`: louder rough-master setting
- `vinyl-prep`: quieter ceiling, narrowed width, conservative EQ

Preset behavior:

- In YAML, set `master.preset`
- On CLI, pass `--preset streaming`, `--preset club`, etc.
- Explicit CLI flags such as `--target-lufs` or `--ceiling-db` override preset values
- Explicit YAML values override `master.preset` values

Reference analysis compares:

- integrated LUFS
- peak level
- RMS and crest factor
- stereo width
- broad tonal balance bands from sub through air

CLI report behavior:

- `--report` writes `<output>.report.json`
- `--report path/to/report.json` writes to an explicit path
- `batch --report` writes one report per processed output
- reference comparisons are included in reports when `--reference` is used

## Notes

- Audio is processed internally as floating point.
- Mono inputs are panned into stereo when `output_channels` is `2`.
- The limiter is transparent lookahead gain reduction, not analog-style clipping or saturation.
- `limiter.oversample_factor` checks oversampled peaks after limiting and applies final gain correction when needed.
