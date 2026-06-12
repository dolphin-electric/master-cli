from __future__ import annotations

from pathlib import Path

import yaml

from .master import MasterSettings
from .mix import MixSettings, Stem, stem_from_mapping
from .presets import settings_from_mapping_with_preset


class ConfigError(ValueError):
    pass


def load_mix_config(path: str | Path) -> tuple[list[Stem], MixSettings, MasterSettings]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}

    stems_config = values.get("stems")
    if not isinstance(stems_config, list) or not stems_config:
        raise ConfigError("Mix config must include a non-empty stems list.")

    stems = [stem_from_mapping(_resolve_stem_path(stem, config_path.parent)) for stem in stems_config]
    mix_settings = MixSettings(
        sample_rate=int(values.get("sample_rate", 48_000)),
        output_channels=int(values.get("output_channels", 2)),
        headroom_db=float(values.get("headroom_db", -6.0)),
    )
    master_settings = settings_from_mapping_with_preset(values.get("master"))
    return stems, mix_settings, master_settings


def _resolve_stem_path(values: dict, base_path: Path) -> dict:
    if "path" not in values:
        return values

    resolved = dict(values)
    path = Path(resolved["path"])
    if not path.is_absolute():
        resolved["path"] = base_path / path
    return resolved
