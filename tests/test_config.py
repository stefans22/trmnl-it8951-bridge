from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ha_trmnl_ws_display.config import Settings, load_settings


def test_vcom_is_required() -> None:
    with pytest.raises(ValidationError, match="panel"):
        load_settings()


def test_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EPD_PANEL__VCOM", "-1.87")
    monkeypatch.setenv("EPD_DISPLAY__FULL_REFRESH_EVERY", "3")
    monkeypatch.setenv("EPD_PULL__HEADERS", '{"Authorization": "Bearer x"}')
    s = load_settings()
    assert s.panel.vcom == -1.87
    assert s.display.full_refresh_every == 3
    assert s.pull.headers["Authorization"].get_secret_value() == "Bearer x"
    assert s.display.rotation == 0  # default


def test_precedence_default_file_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("panel:\n  vcom: -1.5\ndisplay:\n  rotation: 90\n  full_refresh_every: 4\n")
    monkeypatch.setenv("EPD_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("EPD_DISPLAY__FULL_REFRESH_EVERY", "7")
    s = load_settings()
    assert s.panel.vcom == -1.5  # file
    assert s.display.rotation == 90  # file
    assert s.display.full_refresh_every == 7  # env beats file
    assert s.display.max_rects == 8  # default


@pytest.mark.parametrize(
    ("section", "values"),
    [
        ("panel", {"vcom": 1.5}),
        ("display", {"rotation": 45}),
        ("display", {"tile_size": 30}),
        ("display", {"full_refresh_area_ratio": 0}),
        ("pull", {"enabled": True}),
        ("display", {"unknown_key": 1}),
    ],
)
def test_invalid_values(section: str, values: dict[str, object]) -> None:
    sections: dict[str, dict[str, object]] = {"panel": {"vcom": -1.5}}
    sections[section] = {**sections.get(section, {}), **values}
    with pytest.raises(ValidationError):
        load_settings(**sections)


def test_unknown_key_in_file_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("panel:\n  vcom: -1.5\n  vcomm: -2\n")
    monkeypatch.setenv("EPD_CONFIG_FILE", str(cfg))
    with pytest.raises(ValidationError, match="vcomm"):
        load_settings()


def test_secrets_are_redacted() -> None:
    s = load_settings(
        panel={"vcom": -1.5},
        push={"token": "top-secret"},
        pull={"enabled": True, "url": "http://addon:10000/x", "headers": {"X-Key": "hidden"}},
    )
    dumped = str(s.redacted())
    assert "top-secret" not in dumped
    assert "hidden" not in dumped
    assert s.push.token is not None
    assert s.push.token.get_secret_value() == "top-secret"


def test_configuration_doc_lists_every_setting() -> None:
    """docs/configuration.md must document every field of the model (ADR 0007)."""
    doc = (Path(__file__).parents[1] / "docs" / "configuration.md").read_text()
    for section_name, section in Settings.model_fields.items():
        assert f"### `{section_name}`" in doc, f"section {section_name} missing"
        annotation = section.annotation
        assert annotation is not None
        for key in annotation.model_fields:
            assert re.search(rf"`{key}`", doc), f"{section_name}.{key} is not documented"
        # and nothing is documented that doesn't exist
        table = doc.split(f"### `{section_name}`", 1)[1].split("\n### ", 1)[0]
        for match in re.finditer(r"^\| `(\w+)`(?: / `(\w+)`)? \|", table, re.MULTILINE):
            for key in filter(None, match.groups()):
                assert key in annotation.model_fields, f"{section_name}.{key} is documented but not a setting"
