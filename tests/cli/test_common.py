"""Tests for bikephysics.cli.common (architecture.md S9, task.md T1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bikephysics.cli.common import (
    GlobalOptions,
    apply_overrides,
    coerce_scalar,
    make_run_dir_for,
    parse_set_override,
    resolve_config_path,
)


def test_parse_set_override_splits_key_and_value() -> None:
    assert parse_set_override("rear.c_2a_rr=0.008") == ("rear.c_2a_rr", "0.008")


def test_parse_set_override_rejects_missing_equals() -> None:
    with pytest.raises(ValueError, match="key.path=value"):
        parse_set_override("rear.c_2a_rr")


def test_parse_set_override_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="non-empty key"):
        parse_set_override("=0.008")


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("False", False), ("3", 3), ("0.008", 0.008), ("road_bike", "road_bike")],
)
def test_coerce_scalar(raw: str, expected: object) -> None:
    assert coerce_scalar(raw) == expected


def test_apply_overrides_sets_nested_dotted_path() -> None:
    data = {"rear": {"c_2a_rr": 0.004}}
    result = apply_overrides(data, ["rear.c_2a_rr=0.008"])
    assert result["rear"]["c_2a_rr"] == 0.008
    # original left untouched (deep copy, not mutated in place)
    assert data["rear"]["c_2a_rr"] == 0.004


def test_apply_overrides_creates_missing_intermediate_dicts() -> None:
    result = apply_overrides({}, ["rider.P_max=300"])
    assert result == {"rider": {"P_max": 300}}


def test_resolve_config_path_returns_none_for_no_config() -> None:
    assert resolve_config_path(None, []) is None


def test_resolve_config_path_accepts_a_direct_path(tmp_path: Path) -> None:
    config_file = tmp_path / "my_bike.toml"
    config_file.write_text("", encoding="utf-8")
    assert resolve_config_path(str(config_file), []) == config_file


def test_resolve_config_path_finds_a_preset_by_name(tmp_path: Path) -> None:
    preset_dir = tmp_path / "bikes"
    preset_dir.mkdir()
    preset_file = preset_dir / "road_bike.toml"
    preset_file.write_text("", encoding="utf-8")
    assert resolve_config_path("road_bike", [preset_dir]) == preset_file


def test_resolve_config_path_raises_when_nothing_matches(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_config_path("no_such_bike", [tmp_path])


def test_make_run_dir_for_creates_directory_under_out(tmp_path: Path) -> None:
    options = GlobalOptions(config=None, out=tmp_path / "runs")
    run_dir = make_run_dir_for(options, "slug")
    assert run_dir.exists()
    assert run_dir.parent == tmp_path / "runs"
