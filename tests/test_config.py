"""Unit tests for persisted UI settings and factory defaults."""

from __future__ import annotations

import json

from src import config


def test_default_ui_settings_factory_values():
    ui = config.default_ui_settings()
    assert ui["interferograms"] == config.DEFAULT_INTERFEROGRAMS
    assert ui["threshold"] == config.DEFAULT_THRESHOLD
    assert ui["save_file"] == config.DEFAULT_SAVE_FILE
    assert ui["save_enabled"] is config.DEFAULT_SAVE_ENABLED
    assert ui["save_format"] == config.DEFAULT_SAVE_FORMAT
    assert ui["save_when"] == config.DEFAULT_SAVE_WHEN
    assert ui["mode"] == config.DEFAULT_MODE
    assert ui["samplerate"] == config.DEFAULT_SAMPLERATE
    assert ui["input_range"] == config.DEFAULT_INPUT_RANGE
    assert ui["bulk_limit"] == config.DEFAULT_BULK_LIMIT
    assert ui["bulk_unit"] == config.DEFAULT_BULK_UNIT
    assert ui["channel1"] is config.DEFAULT_CHANNEL1
    assert ui["channel2"] is config.DEFAULT_CHANNEL2
    assert ui["channel3"] is config.DEFAULT_CHANNEL3
    assert ui["channel4"] is config.DEFAULT_CHANNEL4
    assert ui["pre_trigger_samples"] == config.DEFAULT_PRE_TRIGGER_SAMPLES
    assert ui["post_trigger_samples"] == config.DEFAULT_POST_TRIGGER_SAMPLES
    assert ui["max_capture_rate_hz"] == config.DEFAULT_MAX_CAPTURE_RATE_HZ
    assert ui["d_frep_hz"] == config.DEFAULT_DFREP_HZ
    assert ui["frio_mhz"] == config.DEFAULT_FRIO_MHZ
    assert ui["m1"] == float(config.DEFAULT_M1)
    assert ui["m2"] == float(config.DEFAULT_M2)
    assert ui["m3"] == float(config.DEFAULT_M3)
    assert ui["spectrum_axis"] == config.DEFAULT_SPECTRUM_AXIS
    assert ui["apodization"] == config.DEFAULT_APODIZATION
    assert ui["trigger_source"] == config.DEFAULT_TRIGGER_SOURCE
    assert ui["trigger_edge"] == config.DEFAULT_TRIGGER_EDGE
    assert ui["trigger_threshold"] == config.DEFAULT_TRIGGER_THRESHOLD
    assert ui["ext_trigger_coupling"] == config.DEFAULT_EXT_TRIGGER_COUPLING
    assert ui["ext_trigger_input_range"] == config.DEFAULT_EXT_TRIGGER_INPUT_RANGE
    assert ui["ext_trigger_impedance"] == config.DEFAULT_EXT_TRIGGER_IMPEDANCE


def test_load_ui_settings_matches_defaults_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.json")
    assert config.load_ui_settings() == config.default_ui_settings()


def test_load_ui_settings_matches_defaults_when_config_empty(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    assert config.load_ui_settings() == config.default_ui_settings()


def test_load_ui_settings_ignores_unrelated_keys(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"font_scale": 0.75, "not_a_setting": 123}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    loaded = config.load_ui_settings()
    assert loaded == config.default_ui_settings()
    assert "font_scale" not in loaded
    assert "not_a_setting" not in loaded


def test_save_config_writes_defaults_without_dropping_font_scale(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"font_scale": 0.75, "samplerate": 1000}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    config.save_config(config.default_ui_settings(), quiet=True)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["font_scale"] == 0.75
    assert on_disk["samplerate"] == config.DEFAULT_SAMPLERATE
    assert on_disk["mode"] == config.DEFAULT_MODE
