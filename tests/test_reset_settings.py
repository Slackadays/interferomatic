"""Tests for Reset to Default: applies factory settings only when idle."""

from __future__ import annotations

import dearpygui.dearpygui as dpg
import pytest

import main
from src import config


@pytest.fixture
def app(monkeypatch):
    dpg.create_context()
    original = main.ifm
    main.ifm = main.ifmstate()
    saved = []
    monkeypatch.setattr(
        main, "save_config", lambda data, quiet=False: saved.append(dict(data))
    )
    yield main, saved
    main.ifm = original
    dpg.destroy_context()


def test_reset_is_listed_with_locked_settings():
    assert "reset_defaults_button" in main.SETTINGS_WIDGETS
    assert "scale_button" not in main.SETTINGS_WIDGETS
    assert "exit_button" not in main.SETTINGS_WIDGETS


def test_reset_noops_while_gathering(app):
    main_mod, saved = app
    main_mod.ifm.gathering = True
    main_mod.ifm.samplerate = 1_000
    main_mod.ifm.mode = main_mod.Mode.AVERAGE
    assert main_mod.reset_settings_to_defaults() is False
    assert main_mod.ifm.samplerate == 1_000
    assert main_mod.ifm.mode == main_mod.Mode.AVERAGE
    assert saved == []


def test_reset_applies_defaults_to_state_and_widgets(app):
    main_mod, saved = app
    main_mod.ifm.gathering = False
    main_mod.ifm.samplerate = 1_000
    main_mod.ifm.mode = main_mod.Mode.AVERAGE
    main_mod.ifm.channel2 = True
    main_mod.ifm.threshold = 0.1
    main_mod.ifm.input_range = 400
    main_mod.ifm.trigger_source = "External"
    main_mod.ifm.save_enabled = True
    main_mod.ifm.save_file = "/tmp/out.csv"

    with dpg.window(tag="test_win"):
        dpg.add_combo(
            tag="mode_combo",
            items=list(main_mod.MODE_LABELS.values()),
            default_value="Average interferograms",
        )
        dpg.add_combo(
            tag="sample_rate_dropdown",
            items=list(main_mod.SAMPLERATE_LABELS.values()),
            default_value="1 kS/s",
        )
        dpg.add_checkbox(tag="channel2_checkbox", default_value=True)
        dpg.add_slider_float(tag="threshold_slider", default_value=0.1, max_value=1)
        dpg.add_combo(
            tag="input_range_dropdown",
            items=main_mod.INPUT_RANGE_COMBO_ITEMS,
            default_value="±200mV",
        )
        dpg.add_combo(
            tag="trigger_source_dropdown",
            items=list(main_mod.TRIGGER_SOURCE_ITEMS),
            default_value="External",
        )
        dpg.add_checkbox(tag="save_enabled_checkbox", default_value=True)
        dpg.add_input_text(tag="save_file_input", default_value="/tmp/out.csv")
        dpg.add_button(tag="reset_defaults_button", label="Reset to Default")

    assert main_mod.reset_settings_to_defaults() is True

    defaults = config.default_ui_settings()
    assert main_mod.ifm.samplerate == defaults["samplerate"]
    assert main_mod.ifm.mode == main_mod.Mode.MONITOR
    assert main_mod.ifm.channel2 is False
    assert main_mod.ifm.threshold == defaults["threshold"]
    assert main_mod.ifm.input_range == defaults["input_range"]
    assert main_mod.ifm.trigger_source == defaults["trigger_source"]
    assert main_mod.ifm.save_enabled is False
    assert main_mod.ifm.save_file == ""

    assert dpg.get_value("mode_combo") == "Monitor signal"
    assert dpg.get_value("sample_rate_dropdown") == "200 MS/s"
    assert dpg.get_value("channel2_checkbox") is False
    assert dpg.get_value("threshold_slider") == pytest.approx(defaults["threshold"])
    assert dpg.get_value("input_range_dropdown") == "±1V"
    assert dpg.get_value("trigger_source_dropdown") == "Channel 1"
    assert dpg.get_value("save_enabled_checkbox") is False
    assert dpg.get_value("save_file_input") == ""

    assert saved and saved[0]["samplerate"] == defaults["samplerate"]
    assert saved[0]["mode"] == defaults["mode"]


def test_reset_button_is_disabled_with_other_settings(app):
    main_mod, _saved = app
    with dpg.window(tag="test_win"):
        dpg.add_button(tag="reset_defaults_button", label="Reset to Default")
        dpg.add_combo(tag="mode_combo", items=["Monitor signal"])
    main_mod.set_settings_enabled(False)
    assert not dpg.is_item_enabled("reset_defaults_button")
    assert not dpg.is_item_enabled("mode_combo")
    main_mod.set_settings_enabled(True)
    assert dpg.is_item_enabled("reset_defaults_button")
    assert dpg.is_item_enabled("mode_combo")
