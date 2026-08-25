import dearpygui.dearpygui as dpg
import numpy as np
import playsound3
import sys
import threading
import time
from pathlib import Path
from enum import Enum

from src.config import (
    save_config,
    load_ui_settings,
    default_ui_settings,
    AUTOSAVE_INTERVAL_S,
    BULK_UNITS,
    DEFAULT_PRE_TRIGGER_SAMPLES,
    DEFAULT_POST_TRIGGER_SAMPLES,
    DEFAULT_TRIGGER_SOURCE,
    DEFAULT_TRIGGER_EDGE,
    DEFAULT_TRIGGER_THRESHOLD,
    DEFAULT_EXT_TRIGGER_COUPLING,
    DEFAULT_EXT_TRIGGER_INPUT_RANGE,
    DEFAULT_EXT_TRIGGER_IMPEDANCE,
    DEFAULT_MAX_CAPTURE_RATE_HZ,
    DEFAULT_DFREP_HZ,
    DEFAULT_FRIO_MHZ,
    DEFAULT_M1,
    DEFAULT_M2,
    DEFAULT_M3,
    DEFAULT_APODIZATION,
    DEFAULT_SAVE_ENABLED,
    DEFAULT_SAVE_FORMAT,
    DEFAULT_SAVE_WHEN,
    DEFAULT_SPECTRUM_AXIS,
    MIN_PRE_TRIGGER_SAMPLES,
    MIN_POST_TRIGGER_SAMPLES,
    MAX_LIVE_SAMPLES,
    MIN_MAX_CAPTURE_RATE_HZ,
    MAX_MAX_CAPTURE_RATE_HZ,
    SAVE_FORMAT_BINARY,
    SAVE_FORMAT_CSV_WN,
    SAVE_FORMAT_ITEMS,
    SAVE_WHEN_AUTOSAVE,
    SAVE_WHEN_ITEMS,
    VALID_TRIGGER_SOURCES,
    VALID_TRIGGER_EDGES,
    VALID_EXT_TRIGGER_COUPLINGS,
    VALID_EXT_TRIGGER_IMPEDANCES,
)
from src.scaling import resolve_font_scale, change_font_scale
from src.live_view import (
    create_live_view_engine,
    normalize_live_window,
    max_capture_rate_to_interval_s,
    LIVE_DEPTH_INCREMENT,
    LIVE_TRIGGER_TIMEOUT,
    AVERAGE_TRIGGER_TIMEOUT,
)
from src.averaging import (
    AverageResult,
    slice_xy_for_view,
    format_eta_seconds,
)
from src.spectrum import (
    compute_rf_spectrum,
    default_m3_from_m1_m2,
    dual_comb_period_samples,
    map_rf_to_optical,
    slice_spectrum_for_view,
    spectrum_view_limits,
    write_spectrum_file,
    APODIZATION_ITEMS,
    SPECTRUM_AXIS_ITEMS,
    SPECTRUM_AXIS_WAVELENGTH,
    SPECTRUM_AXIS_WAVENUMBER,
    WAVENUMBER_UNIT,
    is_wavenumber_axis,
    normalize_apodization,
    normalize_spectrum_axis,
)

GAGE_API_DIR = Path(__file__).resolve().parent / "gage_api"
if str(GAGE_API_DIR) not in sys.path:
    sys.path.insert(0, str(GAGE_API_DIR))

# Placeholder empty series so the plot axes exist before the first capture.
PLACEHOLDER_X = [0.0, 1.0]
PLACEHOLDER_Y = [0.0, 0.0]
# Max points drawn for a line. Full-resolution traces stay in memory;
# this cap only applies when the current X window still has more samples
# (zoomed out). Zooming in plots every remaining sample.
SPECTRUM_PLOT_MAX_POINTS = 262144
TIME_PLOT_MAX_POINTS = 65536

# Distinct colors for Live View channel traces (RGBA 0–255).
CHANNEL_COLORS = {
    1: (80, 180, 255, 255),
    2: (80, 220, 120, 255),
    3: (255, 180, 60, 255),
    4: (240, 100, 200, 255),
}

ALL_CHANNELS = (1, 2, 3, 4)


def show_error_window(message):
    """Show a modal error; safe to call from the main render loop."""
    if dpg.does_item_exist("error_window"):
        dpg.delete_item("error_window")
    with dpg.window(label="Internal Error", modal=True, tag="error_window",
                    width=900, height=300, pos=(100, 100)):
        dpg.add_text(f"An error occurred: {message}", wrap=850)
        dpg.add_button(label="Close", callback=lambda: dpg.delete_item("error_window"))
        print("Internal error window displayed:", message)
    try:
        playsound3.playsound("src/half-life-2-episode-2-base-alarm.mp3", block=False)
    except Exception:
        pass


class Mode(Enum):
    MONITOR = 1
    COLLECT = 2
    AVERAGE = 3


MODE_LABELS = {
    Mode.MONITOR: "Monitor signal",
    Mode.COLLECT: "Collect bulk data",
    Mode.AVERAGE: "Average interferograms",
}
LABEL_TO_MODE = {label: mode for mode, label in MODE_LABELS.items()}

# Display labels for the sample-rate combo, keyed by rate in samples/second.
SAMPLERATE_LABELS = {
    1_000: "1 kS/s",
    2_000: "2 kS/s",
    5_000: "5 kS/s",
    10_000: "10 kS/s",
    20_000: "20 kS/s",
    50_000: "50 kS/s",
    100_000: "100 kS/s",
    200_000: "200 kS/s",
    500_000: "500 kS/s",
    1_000_000: "1 MS/s",
    2_000_000: "2 MS/s",
    5_000_000: "5 MS/s",
    10_000_000: "10 MS/s",
    25_000_000: "25 MS/s",
    50_000_000: "50 MS/s",
    100_000_000: "100 MS/s",
    200_000_000: "200 MS/s",
}
LABEL_TO_SAMPLERATE = {label: rate for rate, label in SAMPLERATE_LABELS.items()}
DEFAULT_SAMPLERATE_LABEL = SAMPLERATE_LABELS[200_000_000]

# Gage InputRange is millivolts peak-to-peak (bipolar label shows half of that).
# Keys match the values written to channel['InputRange'] / Acquire.ini Range=.
INPUT_RANGE_LABELS = {
    200: "±100mV",
    400: "±200mV",
    1000: "±500mV",
    2000: "±1V",
    4000: "±2V",
    10000: "±5V",
}
LABEL_TO_INPUT_RANGE = {label: mv for mv, label in INPUT_RANGE_LABELS.items()}
DEFAULT_INPUT_RANGE_LABEL = INPUT_RANGE_LABELS[2000]
# Preserve the order from the UI dropdown.
INPUT_RANGE_COMBO_ITEMS = [
    "±100mV",
    "±200mV",
    "±500mV",
    "±1V",
    "±2V",
    "±5V",
]

# External-trigger ExtRange is millivolts peak-to-peak (same units as channel range).
TRIGGER_INPUT_RANGE_LABELS = {
    2000: "±1V",
    6600: "±3.3V",
    10000: "±5V",
}
LABEL_TO_TRIGGER_INPUT_RANGE = {
    label: mv for mv, label in TRIGGER_INPUT_RANGE_LABELS.items()
}
DEFAULT_TRIGGER_INPUT_RANGE_LABEL = TRIGGER_INPUT_RANGE_LABELS[2000]
TRIGGER_INPUT_RANGE_COMBO_ITEMS = ["±1V", "±3.3V", "±5V"]

TRIGGER_SOURCE_ITEMS = VALID_TRIGGER_SOURCES
TRIGGER_EDGE_ITEMS = VALID_TRIGGER_EDGES
TRIGGER_COUPLING_ITEMS = VALID_EXT_TRIGGER_COUPLINGS
TRIGGER_IMPEDANCE_ITEMS = VALID_EXT_TRIGGER_IMPEDANCES


def parse_samplerate_label(label):
    """Convert a sample-rate combo label (e.g. '200 MS/s') to an int S/s."""
    if not isinstance(label, str):
        raise ValueError(f"Invalid sample rate label: {label!r}")
    if label in LABEL_TO_SAMPLERATE:
        return LABEL_TO_SAMPLERATE[label]
    # Fallback for unexpected formatting.
    value_str = label.replace(" ", "").replace("kS/s", "000").replace("MS/s", "000000")
    return int(value_str)


def samplerate_to_label(rate):
    """Convert an int sample rate (S/s) to a combo label, or the default."""
    return SAMPLERATE_LABELS.get(rate, DEFAULT_SAMPLERATE_LABEL)


def parse_input_range_label(label):
    """Convert an input-range combo label (e.g. '±1V') to mV peak-to-peak."""
    if not isinstance(label, str):
        raise ValueError(f"Invalid input range label: {label!r}")
    if label in LABEL_TO_INPUT_RANGE:
        return LABEL_TO_INPUT_RANGE[label]
    raise ValueError(f"Unknown input range label: {label!r}")


def input_range_to_label(range_mv):
    """Convert Gage InputRange (mV peak-to-peak) to a combo label."""
    return INPUT_RANGE_LABELS.get(range_mv, DEFAULT_INPUT_RANGE_LABEL)


def parse_trigger_input_range_label(label):
    """Convert an ext-trigger range label (e.g. '±3.3V') to mV peak-to-peak."""
    if not isinstance(label, str):
        raise ValueError(f"Invalid trigger input range label: {label!r}")
    if label in LABEL_TO_TRIGGER_INPUT_RANGE:
        return LABEL_TO_TRIGGER_INPUT_RANGE[label]
    raise ValueError(f"Unknown trigger input range label: {label!r}")


def trigger_input_range_to_label(range_mv):
    """Convert ext-trigger ExtRange (mV peak-to-peak) to a combo label."""
    return TRIGGER_INPUT_RANGE_LABELS.get(range_mv, DEFAULT_TRIGGER_INPUT_RANGE_LABEL)


class ifmstate:
    gathering = False
    has_gage = False
    save_file = ""
    save_enabled = DEFAULT_SAVE_ENABLED
    save_format = DEFAULT_SAVE_FORMAT
    save_when = DEFAULT_SAVE_WHEN
    last_autosave_t = 0.0
    mode = Mode.MONITOR
    samplerate = 200000000
    input_range = 2000  # mV peak-to-peak (±1 V)
    pre_trigger_samples = DEFAULT_PRE_TRIGGER_SAMPLES
    post_trigger_samples = DEFAULT_POST_TRIGGER_SAMPLES
    max_capture_rate_hz = DEFAULT_MAX_CAPTURE_RATE_HZ
    trigger_source = DEFAULT_TRIGGER_SOURCE
    trigger_edge = DEFAULT_TRIGGER_EDGE
    trigger_threshold = DEFAULT_TRIGGER_THRESHOLD  # percent of full scale
    ext_trigger_coupling = DEFAULT_EXT_TRIGGER_COUPLING
    ext_trigger_input_range = DEFAULT_EXT_TRIGGER_INPUT_RANGE  # mV pk-pk
    ext_trigger_impedance = DEFAULT_EXT_TRIGGER_IMPEDANCE
    channel1 = True
    channel2 = False
    channel3 = False
    channel4 = False
    live_engine = None
    live_error = None  # last live-view error string (for one-shot UI report)
    live_last_tick = 0.0  # monotonic time of last successful capture attempt
    live_last_frame = 0.0  # monotonic time of last non-empty capture frame
    # Live View axis reset: force full-window limits for N frames, then unlock
    # so pan/zoom work. Immediate unlock cancels the reset (never rendered).
    axis_force_frames = 0
    axis_unlock_pending = False
    axis_limits = None  # (x0, x1, y0, y1) last reset target
    # Spectrum (FFT) calibration — dual-comb optical axis.
    d_frep_hz = DEFAULT_DFREP_HZ
    frio_mhz = DEFAULT_FRIO_MHZ
    m1 = float(DEFAULT_M1)
    m2 = float(DEFAULT_M2)
    m3 = float(DEFAULT_M3)
    spectrum_axis = DEFAULT_SPECTRUM_AXIS
    apodization = DEFAULT_APODIZATION
    # Last time-domain y used for spectrum (full-resolution, first channel).
    last_spectrum_y = None
    # Cached full-resolution RF spectrum (rFFT magnitude) and its mapping.
    last_rf_n = 0
    last_rf_rate = 0.0
    last_rf_mag = None
    last_d_frep = 0.0
    last_spectrum_x = None
    last_spectrum_mag = None
    last_spectrum_x_frf0 = None
    # Recenter spectrum view after start / calibration change (not every frame).
    spectrum_needs_recenter = True
    spectrum_force_frames = 0
    spectrum_x_limits = None  # (x_lo, x_hi) while forcing a centered view
    # Last X window we sliced the full spectrum into for the line series.
    spectrum_lod_limits = None
    spectrum_lod_dirty = True
    # Full-resolution time traces for zoom-aware Live View (ch -> (x, y)).
    last_time_data = None
    time_lod_limits = None
    time_lod_dirty = True
    # Average-interferograms mode state.
    threshold = 0.5  # min peak cross-correlation to accept a trace
    interferograms_target = 100000
    average_result: AverageResult | None = None
    ignore_start_until = 0.0

# If the Gage child is silent this long (no frames AND no heartbeat), recycle
# it. Heartbeats fire while waiting for a trigger or sitting in analog
# calibration (relay clicking), so ordinary pauses must not re-Commit.
CAPTURE_STALL_TIMEOUT_S = 30.0

ifm = ifmstate()

def capture_min_interval_s() -> float:
    """Minimum seconds between capture pulls from *max_capture_rate_hz*."""
    return max_capture_rate_to_interval_s(ifm.max_capture_rate_hz)


# Detect the real PyGage extension without importing it into this process.
# Loading PyGage here installs Linux signal handlers that race with Dear PyGui's
# OpenGL threads and corrupt continuous capture (SIGSEGV / stuck READY).
from src.live_view import gage_extension_available

if gage_extension_available():
    ifm.has_gage = True
else:
    print("Running in Gage-less mode. PyGage module not found.")


# Acquisition settings locked while gathering (including Reset to Default).
# Start/Stop, Fullscreen, Change Scale, Exit, and the live plot stay usable.
SETTINGS_WIDGETS = (
    "mode_combo",
    "sample_rate_dropdown",
    "interferograms_input",
    "bulk_limit_input",
    "bulk_unit_combo",
    "threshold_slider",
    "save_enabled_checkbox",
    "save_file_input",
    "browse_button",
    "save_format_combo",
    "save_when_combo",
    "channel1_checkbox",
    "channel2_checkbox",
    "channel3_checkbox",
    "channel4_checkbox",
    "input_range_dropdown",
    "max_capture_rate_input",
    "pre_trigger_input",
    "post_trigger_input",
    "trigger_source_dropdown",
    "trigger_edge_dropdown",
    "trigger_threshold_input",
    "external_trigger_coupling_dropdown",
    "external_trigger_input_range_dropdown",
    "external_trigger_impedance_dropdown",
    "d_frep_input",
    "frio_input",
    "m1_input",
    "m2_input",
    "m3_input",
    "spectrum_axis_combo",
    "reset_defaults_button",
)


def enabled_channels():
    """Return channel numbers currently checked in the UI / state."""
    channels = []
    if ifm.channel1:
        channels.append(1)
    if ifm.channel2:
        channels.append(2)
    if ifm.channel3:
        channels.append(3)
    if ifm.channel4:
        channels.append(4)
    return channels


def update_mode_dependent_widgets():
    """Show only the limit controls and spectrum chart that apply to the mode."""
    if not dpg.does_item_exist("average_controls"):
        return
    show_average = ifm.mode == Mode.AVERAGE
    show_bulk = ifm.mode == Mode.COLLECT
    dpg.configure_item("average_controls", show=show_average)
    dpg.configure_item("bulk_controls", show=show_bulk)
    if dpg.does_item_exist("average_status_text"):
        if not show_average:
            dpg.set_value("average_status_text", "")
    _set_spectrum_chart_visible(show_average)


def _set_spectrum_chart_visible(show: bool) -> None:
    """Show the FFT plot only in Average mode; interferogram fills the rest."""
    if dpg.does_item_exist("reset_spectrum_button"):
        dpg.configure_item("reset_spectrum_button", show=show)
    if dpg.does_item_exist("spectrum_settings_header"):
        dpg.configure_item("spectrum_settings_header", show=show)
    if dpg.does_item_exist("spectrum_plot"):
        dpg.configure_item("spectrum_plot", show=show)
    if dpg.does_item_exist("plot_subplots"):
        if show:
            dpg.configure_item("plot_subplots", rows=2, row_ratios=(1.0, 1.0))
        else:
            # Collapse the unused spectrum row so the interferogram is full height.
            dpg.configure_item("plot_subplots", rows=1, row_ratios=(1.0,))


def update_external_trigger_controls():
    """Show external-trigger options only when the source is External."""
    if not dpg.does_item_exist("external_trigger_controls"):
        return
    dpg.configure_item(
        "external_trigger_controls",
        show=(ifm.trigger_source == "External"),
    )


def set_settings_enabled(enabled: bool):
    """Enable or gray out acquisition settings (not screen controls)."""
    for tag in SETTINGS_WIDGETS:
        if not dpg.does_item_exist(tag):
            continue
        if enabled:
            dpg.enable_item(tag)
        else:
            dpg.disable_item(tag)


def set_gathering_ui(gathering: bool):
    """Sync Start/Stop button and settings lock with gathering state."""
    ifm.gathering = gathering
    if gathering:
        dpg.bind_item_theme("startstop_button", "stop_button_theme")
        dpg.set_item_label("startstop_button", "Stop")
        set_settings_enabled(False)
    else:
        dpg.bind_item_theme("startstop_button", "start_button_theme")
        dpg.set_item_label("startstop_button", "Start")
        set_settings_enabled(True)
        # Keep the last average on the plot when Average mode finishes;
        # clear the snapshot when leaving other modes.
        if ifm.mode != Mode.AVERAGE:
            ifm.average_result = None


def apply_ui_settings_to_state(ui):
    """Copy a load_ui_settings() dict onto ifm (in-memory app state)."""
    ifm.save_file = ui["save_file"]
    ifm.save_enabled = ui["save_enabled"]
    ifm.save_format = ui["save_format"]
    ifm.save_when = ui["save_when"]
    ifm.mode = Mode[ui["mode"]]
    ifm.samplerate = ui["samplerate"]
    ifm.input_range = ui["input_range"]
    ifm.pre_trigger_samples = ui["pre_trigger_samples"]
    ifm.post_trigger_samples = ui["post_trigger_samples"]
    ifm.max_capture_rate_hz = ui["max_capture_rate_hz"]
    ifm.d_frep_hz = ui["d_frep_hz"]
    ifm.frio_mhz = ui["frio_mhz"]
    ifm.m1 = ui["m1"]
    ifm.m2 = ui["m2"]
    ifm.m3 = ui["m3"]
    ifm.spectrum_axis = normalize_spectrum_axis(ui["spectrum_axis"])
    ifm.apodization = normalize_apodization(ui["apodization"])
    ifm.trigger_source = ui["trigger_source"]
    ifm.trigger_edge = ui["trigger_edge"]
    ifm.trigger_threshold = ui["trigger_threshold"]
    ifm.ext_trigger_coupling = ui["ext_trigger_coupling"]
    ifm.ext_trigger_input_range = ui["ext_trigger_input_range"]
    ifm.ext_trigger_impedance = ui["ext_trigger_impedance"]
    ifm.channel1 = ui["channel1"]
    ifm.channel2 = ui["channel2"]
    ifm.channel3 = ui["channel3"]
    ifm.channel4 = ui["channel4"]
    ifm.threshold = ui["threshold"]
    ifm.interferograms_target = ui["interferograms"]


def _set_widget_value(tag, value):
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, value)


def apply_ui_settings_to_widgets(ui):
    """Push a settings dict into the main-window controls, if they exist."""
    _set_widget_value("mode_combo", MODE_LABELS[Mode[ui["mode"]]])
    _set_widget_value("interferograms_input", ui["interferograms"])
    _set_widget_value("threshold_slider", ui["threshold"])
    _set_widget_value("apodization_combo", ui["apodization"])
    _set_widget_value("bulk_limit_input", ui["bulk_limit"])
    _set_widget_value("bulk_unit_combo", ui["bulk_unit"])
    _set_widget_value(
        "sample_rate_dropdown", samplerate_to_label(ui["samplerate"])
    )
    _set_widget_value("channel1_checkbox", ui["channel1"])
    _set_widget_value("channel2_checkbox", ui["channel2"])
    _set_widget_value("channel3_checkbox", ui["channel3"])
    _set_widget_value("channel4_checkbox", ui["channel4"])
    _set_widget_value(
        "input_range_dropdown", input_range_to_label(ui["input_range"])
    )
    _set_widget_value("max_capture_rate_input", ui["max_capture_rate_hz"])
    _set_widget_value("pre_trigger_input", ui["pre_trigger_samples"])
    _set_widget_value("post_trigger_input", ui["post_trigger_samples"])
    _set_widget_value("trigger_source_dropdown", ui["trigger_source"])
    _set_widget_value("trigger_edge_dropdown", ui["trigger_edge"])
    _set_widget_value("trigger_threshold_input", ui["trigger_threshold"])
    _set_widget_value(
        "external_trigger_coupling_dropdown", ui["ext_trigger_coupling"]
    )
    _set_widget_value(
        "external_trigger_input_range_dropdown",
        trigger_input_range_to_label(ui["ext_trigger_input_range"]),
    )
    _set_widget_value(
        "external_trigger_impedance_dropdown", ui["ext_trigger_impedance"]
    )
    _set_widget_value("spectrum_axis_combo", ui["spectrum_axis"])
    _set_widget_value("d_frep_input", ui["d_frep_hz"])
    _set_widget_value("frio_input", ui["frio_mhz"])
    _set_widget_value("m1_input", ui["m1"])
    _set_widget_value("m2_input", ui["m2"])
    _set_widget_value("m3_input", ui["m3"])
    _set_widget_value("save_enabled_checkbox", ui["save_enabled"])
    _set_widget_value("save_file_input", ui["save_file"])
    _set_widget_value("save_format_combo", ui["save_format"])
    _set_widget_value("save_when_combo", ui["save_when"])


def reset_settings_to_defaults():
    """Restore acquisition, trigger, spectrum, and save settings to defaults.

    No-op while gathering so a live capture cannot change board settings
    under itself. Font scale is left alone (it has its own chooser).
    """
    if ifm.gathering:
        print("Cannot reset settings while collecting data")
        return False
    ui = default_ui_settings()
    apply_ui_settings_to_state(ui)
    apply_ui_settings_to_widgets(ui)
    update_mode_dependent_widgets()
    update_external_trigger_controls()
    update_channel_series_visibility()
    apply_live_axis_limits()
    _update_spectrum_axis_label()
    refresh_spectrum_from_cache()
    save_config(ui, quiet=True)
    print("Reset all settings to defaults")
    return True


def _current_threshold() -> float:
    if dpg.does_item_exist("threshold_slider"):
        try:
            value = float(dpg.get_value("threshold_slider"))
            return max(0.0, min(1.0, value))
        except (TypeError, ValueError):
            pass
    return max(0.0, min(1.0, float(ifm.threshold)))


def _current_interferograms_target() -> int:
    if dpg.does_item_exist("interferograms_input"):
        try:
            return max(1, int(dpg.get_value("interferograms_input")))
        except (TypeError, ValueError):
            pass
    return max(1, int(ifm.interferograms_target))


def _settings_panel_text_wrap() -> int:
    """Pixel wrap width so sidebar text stays left of the vertical divider."""
    if dpg.does_item_exist("settings_panel"):
        try:
            width = dpg.get_item_rect_size("settings_panel")[0]
            if width and width > 60:
                # Window padding (20 each side) + a little slack before the border.
                return max(40, int(width) - 48)
        except Exception:
            pass
    return 280


def refresh_settings_text_wraps():
    """Keep long sidebar labels wrapping inside the settings column."""
    wrap = _settings_panel_text_wrap()
    for tag in ("average_status_text", "gage_less_warning"):
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, wrap=wrap)


def update_average_status(result=None):
    """Refresh the Average-mode progress text under the mode controls."""
    if not dpg.does_item_exist("average_status_text"):
        return
    if result is None:
        result = ifm.average_result
        if result is None:
            dpg.set_value("average_status_text", "")
            return
    target = result.target
    accepted = result.accepted
    rejected = result.rejected
    peak = result.last_peak_corr
    eta = format_eta_seconds(result.eta_seconds)
    # Multi-line so a narrow settings column does not clip mid-sentence.
    # ETA sits to the right of the xxx/xxx counter on the first line.
    if result.complete:
        text = (
            f"Done: {accepted}/{target}  ETA 0s\n"
            f"{rejected} rejected\n"
            f"last r={peak:.3f}"
        )
    elif accepted > 0 or rejected > 0:
        text = (
            f"Averaging: {accepted}/{target}  ETA {eta}\n"
            f"{rejected} rejected\n"
            f"last r={peak:.3f}"
        )
    else:
        text = f"Averaging: 0/{target}  ETA …\nwaiting for trigger"
    refresh_settings_text_wraps()
    dpg.set_value("average_status_text", text)


def _build_trigger_dict():
    return {
        "source": ifm.trigger_source,
        "edge": ifm.trigger_edge,
        "level": ifm.trigger_threshold,
        "ext_coupling": ifm.ext_trigger_coupling,
        "ext_range_mv": ifm.ext_trigger_input_range,
        "ext_impedance": ifm.ext_trigger_impedance,
    }


def _build_spectrum_dict():
    return {
        "frio_mhz": float(ifm.frio_mhz),
        "m3": float(ifm.m3),
        "d_frep_hz": float(ifm.d_frep_hz),
        "axis_mode": ifm.spectrum_axis,
        "apodization": ifm.apodization,
        "zpd_index": int(ifm.pre_trigger_samples),
    }


def _warn_if_record_too_short(pre: int, post: int) -> None:
    """Warn when the capture is shorter than one dual-comb beat period."""
    period = dual_comb_period_samples(ifm.samplerate, ifm.d_frep_hz)
    n = int(pre) + int(post)
    if period <= 0 or n >= period:
        return
    print(
        f"Warning: {n} samples is shorter than one dual-comb period "
        f"(N ≥ f_s/Δf_rep = {period} at {ifm.samplerate} S/s, "
        f"Δf_rep={ifm.d_frep_hz} Hz). "
        f"Increase pre+post trigger samples to at least {period}."
    )


def _start_capture_engine(
    channels,
    pre,
    post,
    label: str,
    *,
    average: dict | None = None,
) -> bool:
    """Configure and start the live capture engine. Returns False on failure."""
    if ifm.live_engine is None:
        show_error_window("Live View engine is not available.")
        return False
    trigger = _build_trigger_dict()
    print(
        f"Starting {label}: rate={ifm.samplerate} S/s, "
        f"range={input_range_to_label(ifm.input_range)}, "
        f"window={pre}+{post} samples, channels={channels}, "
        f"max_capture={ifm.max_capture_rate_hz} Hz, "
        f"trigger={ifm.trigger_source}/{ifm.trigger_edge}"
        f"@{ifm.trigger_threshold}%"
        + (
            f", ext=[{ifm.ext_trigger_coupling}, "
            f"{trigger_input_range_to_label(ifm.ext_trigger_input_range)}, "
            f"{ifm.ext_trigger_impedance}]"
            if ifm.trigger_source == "External"
            else ""
        )
        + ("" if ifm.has_gage else " (simulated)")
    )
    try:
        trigger_timeout = (
            AVERAGE_TRIGGER_TIMEOUT if average else LIVE_TRIGGER_TIMEOUT
        )
        ifm.live_engine.configure(
            ifm.samplerate,
            channels,
            ifm.input_range,
            pre_trigger_samples=pre,
            post_trigger_samples=post,
            trigger=trigger,
            max_capture_rate_hz=ifm.max_capture_rate_hz,
            trigger_timeout=trigger_timeout,
        )
        ifm.live_engine.start(
            channels,
            average=average,
            spectrum=_build_spectrum_dict() if ifm.mode == Mode.AVERAGE else None,
        )
        _warn_if_record_too_short(pre, post)
        apply_live_axis_limits()
        ifm.live_last_frame = time.monotonic()
        ifm.spectrum_needs_recenter = True
    except Exception as e:
        try:
            ifm.live_engine.stop()
        except Exception:
            pass
        show_error_window(f"Failed to start {label}: {e}")
        return False
    return True


def _stop_capture_engine():
    if ifm.live_engine is not None:
        try:
            ifm.live_engine.stop()
        except Exception:
            pass


def finish_averaging(completed: bool):
    """Stop capture after average mode ends (target reached or user stop)."""
    # Halt the board first so Stop is immediate; then fetch the stack.
    _stop_capture_engine()
    ifm.gathering = False
    result = None
    engine = ifm.live_engine
    if engine is not None:
        getter = getattr(engine, "get_average_result", None)
        if callable(getter):
            try:
                result = getter(timeout_s=1.0)
            except TypeError:
                try:
                    result = getter()
                except Exception:
                    result = None
            except Exception:
                result = None
    if result is None:
        result = ifm.average_result
    ifm.average_result = result
    if result is not None and result.accepted > 0:
        plot = {}
        spec = None
        if result.x is not None and result.averages:
            for ch, y in result.averages.items():
                plot[ch] = (result.x, y)
            first_y = next(iter(result.averages.values()))
            spec = {
                "n": int(np.asarray(first_y).size),
                "sample_rate": float(ifm.samplerate),
                "d_frep_hz": float(ifm.d_frep_hz or 0.0),
                "mag": None,
                "y": first_y,
            }
        apply_live_view_data(plot, spectrum=spec)
        update_average_status(result)
        _save_average_spectrum(wait=True, announce=True)
        if completed:
            print(
                f"Average complete: {result.accepted} accepted, "
                f"{result.rejected} rejected "
                f"(threshold r≥{ifm.threshold:.3f})"
            )
    set_gathering_ui(False)
    ifm.ignore_start_until = time.monotonic() + 0.4


def series_tag(channel: int) -> str:
    return f"series{channel}"


def update_channel_series_visibility():
    """Show/hide plot series to match the channel checkboxes."""
    enabled = set(enabled_channels())
    for ch in ALL_CHANNELS:
        tag = series_tag(ch)
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, show=ch in enabled)


def input_range_half_scale_volts(range_mv: int) -> float:
    """Half-scale voltage for a Gage InputRange (mV peak-to-peak)."""
    return max(1, int(range_mv)) / 2000.0


def _live_axis_limit_values():
    """Return (x0, x1, y0, y1) for the full capture window / input range."""
    half_v = input_range_half_scale_volts(ifm.input_range)
    pre, post = normalize_live_window(
        ifm.pre_trigger_samples, ifm.post_trigger_samples
    )
    # Trigger at sample 0; show [-pre, post).
    x_max = max(post - 1, 0) if post > 0 else 0
    return (float(-pre), float(x_max), -half_v, half_v)


def _push_live_axis_limits():
    """Apply stored full-window limits (DPG re-applies them every frame until auto)."""
    if ifm.axis_limits is None:
        return
    x0, x1, y0, y1 = ifm.axis_limits
    if dpg.does_item_exist("live_x_axis"):
        dpg.set_axis_limits("live_x_axis", x0, x1)
    if dpg.does_item_exist("amp"):
        dpg.set_axis_limits("amp", y0, y1)


def apply_live_axis_limits():
    """Reset Live View axes to full bipolar range / pre–post window.

    Dear PyGui only honors ``set_axis_limits`` while the plot is rendered with
    those limits "armed". Unlocking on the very next loop iteration (before a
    frame draws) cancels the reset — which is why Reset View appeared broken.

    Strategy: force the target limits for several frames, *then* call
    ``set_axis_limits_auto`` so the user can pan/zoom from the reset view.
    """
    ifm.axis_limits = _live_axis_limit_values()
    # Hold long enough that at least one full plot render sees the limits.
    ifm.axis_force_frames = 3
    ifm.axis_unlock_pending = False
    ifm.time_lod_dirty = True
    _push_live_axis_limits()


def process_live_axis_limits():
    """Per-frame axis reset state machine. Call once before each render."""
    if ifm.axis_force_frames > 0:
        # Keep limits armed so ImPlot applies them on this frame's draw.
        _push_live_axis_limits()
        ifm.axis_force_frames -= 1
        if ifm.axis_force_frames == 0:
            # Unlock on the *following* frame, after this forced view is drawn.
            ifm.axis_unlock_pending = True
        if ifm.axis_limits is not None:
            _push_live_view(ifm.axis_limits[0], ifm.axis_limits[1])
        return

    if ifm.axis_unlock_pending:
        ifm.axis_unlock_pending = False
        if dpg.does_item_exist("amp"):
            dpg.set_axis_limits_auto("amp")
        if dpg.does_item_exist("live_x_axis"):
            dpg.set_axis_limits_auto("live_x_axis")
        ifm.time_lod_dirty = True

    _refresh_live_view_lod()


def _series_xy(x, y):
    """Contiguous float64 vectors for Dear PyGui line series."""
    return [
        np.ascontiguousarray(np.asarray(x, dtype=np.float64).ravel()),
        np.ascontiguousarray(np.asarray(y, dtype=np.float64).ravel()),
    ]


def apply_live_view_data(channel_data: dict, spectrum=None):
    """Store full-resolution traces and draw the visible window."""
    if channel_data:
        if ifm.last_time_data is None:
            ifm.last_time_data = {}
        replaced = False
        for ch, xy in channel_data.items():
            x, y = xy
            y_arr = np.ascontiguousarray(np.asarray(y, dtype=np.float64).ravel())
            prev = ifm.last_time_data.get(int(ch))
            # Never replace a full-resolution trace with an 8k plot envelope.
            if prev is not None and prev[1].size > y_arr.size * 2:
                continue
            ifm.last_time_data[int(ch)] = (
                np.ascontiguousarray(np.asarray(x, dtype=np.float64).ravel()),
                y_arr,
            )
            replaced = True
        if replaced:
            ifm.time_lod_dirty = True
            _push_live_view()
    # Never FFT a decimated time-domain envelope — that aliases the spectrum.
    if spectrum is not None:
        _ingest_spectrum_payload(spectrum)


def _current_live_x_limits():
    if not dpg.does_item_exist("live_x_axis"):
        return ifm.axis_limits[:2] if ifm.axis_limits is not None else None
    try:
        lo, hi = dpg.get_axis_limits("live_x_axis")
        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            return float(lo), float(hi)
    except Exception:
        pass
    if ifm.axis_limits is not None:
        return ifm.axis_limits[0], ifm.axis_limits[1]
    return None


def _push_live_view(x_lo=None, x_hi=None) -> None:
    """Draw full-resolution time traces sliced to the current X window."""
    data = ifm.last_time_data
    if not data:
        return
    if x_lo is None or x_hi is None:
        limits = _current_live_x_limits()
        if limits is None:
            # Fall back to the full stored record.
            for x, _y in data.values():
                if x is not None and len(x) >= 2:
                    x_lo, x_hi = float(x[0]), float(x[-1])
                    break
        else:
            x_lo, x_hi = limits
    if x_lo is None or x_hi is None:
        return
    enabled = set(enabled_channels())
    for ch in ALL_CHANNELS:
        tag = series_tag(ch)
        if not dpg.does_item_exist(tag):
            continue
        if ch in data and ch in enabled:
            x, y = data[ch]
            xs, ys = slice_xy_for_view(
                x, y, float(x_lo), float(x_hi), max_points=TIME_PLOT_MAX_POINTS
            )
            if xs.size < 2:
                xs, ys = x, y
                if xs.size > TIME_PLOT_MAX_POINTS:
                    xs, ys = slice_xy_for_view(
                        xs, ys, float(xs[0]), float(xs[-1]),
                        max_points=TIME_PLOT_MAX_POINTS,
                    )
            dpg.set_value(tag, _series_xy(xs, ys))
            dpg.configure_item(tag, show=True)
        else:
            dpg.configure_item(tag, show=ch in enabled)
            if ch not in enabled:
                dpg.set_value(tag, [PLACEHOLDER_X, PLACEHOLDER_Y])
    ifm.time_lod_limits = (float(x_lo), float(x_hi))
    ifm.time_lod_dirty = False


def _refresh_live_view_lod() -> None:
    if not ifm.last_time_data:
        return
    limits = _current_live_x_limits()
    if limits is None:
        return
    prev = ifm.time_lod_limits
    if (
        not ifm.time_lod_dirty
        and prev is not None
        and abs(prev[0] - limits[0]) < 1e-9
        and abs(prev[1] - limits[1]) < 1e-9
    ):
        return
    _push_live_view(limits[0], limits[1])


def _spectrum_plot_title() -> str:
    """Plot title that follows the Spectrum X-axis dropdown."""
    if is_wavenumber_axis(ifm.spectrum_axis):
        return f"Spectrum (FFT)  wavenumber ({WAVENUMBER_UNIT})"
    return "Spectrum (FFT)  wavelength (nm)"


def _spectrum_axis_label() -> str:
    """X-axis caption for the current Spectrum X-axis setting."""
    if is_wavenumber_axis(ifm.spectrum_axis):
        return f"wavenumber ({WAVENUMBER_UNIT})"
    return "wavelength (nm)"


def _remap_cached_rf_spectrum() -> bool:
    """Rebuild the optical (x, mag) arrays from the cached rFFT."""
    mag = ifm.last_rf_mag
    n = int(ifm.last_rf_n)
    rate = float(ifm.last_rf_rate or ifm.samplerate)
    if mag is None or mag.size < 2 or rate <= 0:
        return False
    d_frep = float(ifm.last_d_frep or 0.0)
    period = dual_comb_period_samples(rate, d_frep)
    if period > 0:
        # Δf_rep-sampled RF axis: bin k is k · f_s / period ≈ k · Δf_rep.
        frf = np.arange(mag.size, dtype=np.float64) * (rate / float(period))
    elif n >= 2:
        frf = np.fft.rfftfreq(n, d=1.0 / rate)
        if frf.size != mag.size:
            frf = np.fft.rfftfreq(max(2, (mag.size - 1) * 2), d=1.0 / rate)
    else:
        return False
    x_axis, y_out, x_frf0 = map_rf_to_optical(
        frf,
        mag,
        frio_mhz=ifm.frio_mhz,
        m3=ifm.m3,
        axis_mode=ifm.spectrum_axis,
    )
    if x_axis.size < 2:
        return False
    ifm.last_spectrum_x = x_axis
    ifm.last_spectrum_mag = y_out
    ifm.last_spectrum_x_frf0 = x_frf0
    ifm.spectrum_lod_dirty = True
    return True


def _save_axis_mode() -> str:
    """Optical X unit for the current save-format choice."""
    if ifm.save_format == SAVE_FORMAT_CSV_WN:
        return SPECTRUM_AXIS_WAVENUMBER
    if ifm.save_format == SAVE_FORMAT_BINARY:
        return ifm.spectrum_axis
    return SPECTRUM_AXIS_WAVELENGTH


def _spectrum_xy_for_save():
    """Full-resolution (x, amplitude) for the chosen save format, or None."""
    axis_mode = _save_axis_mode()
    mag = ifm.last_rf_mag
    n = int(ifm.last_rf_n)
    rate = float(ifm.last_rf_rate or ifm.samplerate)
    if mag is not None and mag.size >= 2 and rate > 0:
        d_frep = float(ifm.last_d_frep or 0.0)
        period = dual_comb_period_samples(rate, d_frep)
        if period > 0:
            frf = np.arange(mag.size, dtype=np.float64) * (rate / float(period))
        elif n >= 2:
            frf = np.fft.rfftfreq(n, d=1.0 / rate)
            if frf.size != mag.size:
                frf = np.fft.rfftfreq(max(2, (mag.size - 1) * 2), d=1.0 / rate)
        else:
            frf = None
        if frf is not None:
            x_axis, y_out, _x0 = map_rf_to_optical(
                frf,
                mag,
                frio_mhz=ifm.frio_mhz,
                m3=ifm.m3,
                axis_mode=axis_mode,
            )
            if x_axis.size >= 1:
                return x_axis, y_out
    if (
        ifm.last_spectrum_x is not None
        and ifm.last_spectrum_mag is not None
        and normalize_spectrum_axis(ifm.spectrum_axis)
        == normalize_spectrum_axis(axis_mode)
    ):
        return ifm.last_spectrum_x, ifm.last_spectrum_mag
    if ifm.last_spectrum_y is not None:
        from src.spectrum import compute_spectrum

        x_axis, y_out, _x0 = compute_spectrum(
            ifm.last_spectrum_y,
            rate,
            frio_mhz=ifm.frio_mhz,
            m3=ifm.m3,
            d_frep_hz=float(ifm.last_d_frep or ifm.d_frep_hz or 0.0),
            axis_mode=axis_mode,
            apodization=ifm.apodization,
            zpd_index=int(ifm.pre_trigger_samples),
        )
        if x_axis.size >= 1:
            return x_axis, y_out
    return None


_save_lock = threading.Lock()


def _write_average_spectrum_file(path: str, x, y, fmt: str) -> Path:
    binary = fmt == SAVE_FORMAT_BINARY
    x_column = "wavenumber" if fmt == SAVE_FORMAT_CSV_WN else "nm"
    if binary and is_wavenumber_axis(ifm.spectrum_axis):
        x_column = "wavenumber"
    with _save_lock:
        return write_spectrum_file(path, x, y, binary=binary, x_column=x_column)


def _save_average_spectrum(*, wait: bool, announce: bool) -> bool:
    """Write the current averaged spectrum. Returns True if a write started."""
    if not ifm.save_enabled:
        return False
    dest = (ifm.save_file or "").strip()
    if not dest:
        return False
    xy = _spectrum_xy_for_save()
    if xy is None:
        if announce:
            print("Skip save: no spectrum data yet")
        return False
    x, y = xy
    fmt = ifm.save_format
    x = np.ascontiguousarray(np.asarray(x, dtype=np.float64).ravel())
    y = np.ascontiguousarray(np.asarray(y, dtype=np.float64).ravel())

    def job():
        try:
            out = _write_average_spectrum_file(dest, x, y, fmt)
            print(f"Saved spectrum ({fmt}) to {out}")
        except Exception as e:
            msg = f"Failed to save spectrum: {e}"
            print(msg)
            if announce:
                try:
                    show_error_window(msg)
                except Exception:
                    pass

    if wait:
        job()
        return True
    threading.Thread(target=job, name="spectrum-save", daemon=True).start()
    return True


def _maybe_autosave_spectrum() -> None:
    if not ifm.save_enabled or ifm.save_when != SAVE_WHEN_AUTOSAVE:
        return
    if not (ifm.save_file or "").strip():
        return
    now = time.monotonic()
    if ifm.last_autosave_t > 0.0 and (now - ifm.last_autosave_t) < AUTOSAVE_INTERVAL_S:
        return
    if _save_average_spectrum(wait=False, announce=False):
        ifm.last_autosave_t = now


def _ingest_spectrum_payload(spectrum) -> None:
    """Accept a full-resolution RF spectrum dict or a time-domain y vector."""
    if spectrum is None:
        return
    # Same object as last time: FFT worker has not published a new result.
    if spectrum is getattr(ifm, "_ingested_spectrum", None):
        return
    ifm._ingested_spectrum = spectrum
    if isinstance(spectrum, dict) and spectrum.get("mag") is not None:
        mag = np.asarray(spectrum["mag"])
        n = int(spectrum.get("n") or (mag.size - 1) * 2)
        rate = float(spectrum.get("sample_rate") or ifm.samplerate)
        try:
            d_frep = float(spectrum.get("d_frep_hz", ifm.d_frep_hz) or 0.0)
        except (TypeError, ValueError):
            d_frep = float(ifm.d_frep_hz or 0.0)
        ifm.last_rf_mag = np.ascontiguousarray(mag, dtype=np.float32)
        ifm.last_rf_n = n
        ifm.last_rf_rate = rate
        ifm.last_d_frep = d_frep
        if spectrum.get("y") is not None:
            ifm.last_spectrum_y = np.asarray(spectrum["y"])
    elif isinstance(spectrum, dict) and spectrum.get("y") is not None:
        update_spectrum_plot(spectrum["y"])
        return
    elif isinstance(spectrum, (tuple, list)) and len(spectrum) >= 2:
        # Legacy (x, mag[, x_frf0]) from older call sites.
        ifm.last_spectrum_x = np.asarray(spectrum[0], dtype=np.float64)
        ifm.last_spectrum_mag = np.asarray(spectrum[1], dtype=np.float64)
        ifm.last_spectrum_x_frf0 = spectrum[2] if len(spectrum) > 2 else ifm.last_spectrum_x_frf0
        ifm.spectrum_lod_dirty = True
        _push_spectrum_view()
        if ifm.spectrum_needs_recenter:
            ifm.spectrum_needs_recenter = False
            apply_spectrum_axis_limits()
        return
    else:
        return
    if not _remap_cached_rf_spectrum():
        return
    _update_spectrum_axis_label()
    if ifm.spectrum_needs_recenter:
        ifm.spectrum_needs_recenter = False
        apply_spectrum_axis_limits()
    else:
        _push_spectrum_view()


def _update_spectrum_axis_label() -> None:
    """Keep the FFT plot title and X-axis unit in sync with the dropdown."""
    axis_label = _spectrum_axis_label()
    plot_title = _spectrum_plot_title()
    if dpg.does_item_exist("spectrum_x_axis"):
        dpg.set_item_label("spectrum_x_axis", axis_label)
        dpg.configure_item("spectrum_x_axis", label=axis_label)
    if dpg.does_item_exist("spectrum_plot"):
        dpg.set_item_label("spectrum_plot", plot_title)
        dpg.configure_item("spectrum_plot", label=plot_title)


def _current_spectrum_x_limits():
    if not dpg.does_item_exist("spectrum_x_axis"):
        return ifm.spectrum_x_limits
    try:
        lo, hi = dpg.get_axis_limits("spectrum_x_axis")
        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            return float(lo), float(hi)
    except Exception:
        pass
    return ifm.spectrum_x_limits


def _push_spectrum_view(x_lo=None, x_hi=None) -> None:
    """Draw the full-resolution spectrum sliced to the current X window."""
    if not dpg.does_item_exist("spectrum_series"):
        return
    x = ifm.last_spectrum_x
    mag = ifm.last_spectrum_mag
    if x is None or mag is None or len(x) < 2:
        dpg.set_value("spectrum_series", [PLACEHOLDER_X, PLACEHOLDER_Y])
        return
    if x_lo is None or x_hi is None:
        limits = _current_spectrum_x_limits()
        if limits is None:
            x_lo, x_hi = float(np.nanmin(x)), float(np.nanmax(x))
        else:
            x_lo, x_hi = limits
    xs, ys = slice_spectrum_for_view(
        x, mag, float(x_lo), float(x_hi), max_points=SPECTRUM_PLOT_MAX_POINTS
    )
    if xs.size < 2:
        xs, ys = np.asarray(x, dtype=np.float64), np.asarray(mag, dtype=np.float64)
        if xs.size > SPECTRUM_PLOT_MAX_POINTS:
            xs, ys = slice_spectrum_for_view(
                xs, ys, float(xs.min()), float(xs.max()),
                max_points=SPECTRUM_PLOT_MAX_POINTS,
            )
    dpg.set_value("spectrum_series", _series_xy(xs, ys))
    ifm.spectrum_lod_limits = (float(x_lo), float(x_hi))
    ifm.spectrum_lod_dirty = False


def update_spectrum_plot(y_samples):
    """FFT a *full-resolution* time trace and map Frf → λ or wavenumber."""
    if not dpg.does_item_exist("spectrum_series"):
        return
    if y_samples is None:
        ifm.last_spectrum_y = None
        ifm.last_rf_mag = None
        dpg.set_value("spectrum_series", [PLACEHOLDER_X, PLACEHOLDER_Y])
        return
    y_arr = np.asarray(y_samples)
    ifm.last_spectrum_y = y_arr
    if y_arr.size < 4:
        dpg.set_value("spectrum_series", [PLACEHOLDER_X, PLACEHOLDER_Y])
        return
    _frf, mag, n = compute_rf_spectrum(
        y_arr,
        float(ifm.samplerate),
        d_frep_hz=float(ifm.d_frep_hz or 0.0),
        apodization=ifm.apodization,
        zpd_index=int(ifm.pre_trigger_samples),
    )
    ifm.last_rf_mag = mag
    ifm.last_rf_n = int(n)
    ifm.last_rf_rate = float(ifm.samplerate)
    ifm.last_d_frep = float(ifm.d_frep_hz or 0.0)
    if not _remap_cached_rf_spectrum():
        return
    _update_spectrum_axis_label()
    if ifm.spectrum_needs_recenter:
        ifm.spectrum_needs_recenter = False
        apply_spectrum_axis_limits()
    else:
        _push_spectrum_view()


def apply_spectrum_axis_limits():
    """Center the spectrum view on Frf = 0; fit Y.

    Limits are forced for a few frames before unlock (same DPG timing issue as
    the interferogram Reset View button).
    """
    if not dpg.does_item_exist("spectrum_x_axis"):
        return
    if (
        (ifm.last_spectrum_x is None or len(ifm.last_spectrum_x) < 2)
        and ifm.last_rf_mag is not None
    ):
        _remap_cached_rf_spectrum()
    if (
        (ifm.last_spectrum_x is None or len(ifm.last_spectrum_x) < 2)
        and ifm.last_spectrum_y is not None
    ):
        update_spectrum_plot(ifm.last_spectrum_y)
        return
    x = ifm.last_spectrum_x
    mag = ifm.last_spectrum_mag
    if x is None or mag is None or len(x) < 2 or len(mag) < 2:
        return
    try:
        x_lo, x_hi = spectrum_view_limits(
            x, mag, x_frf0=ifm.last_spectrum_x_frf0
        )
        ifm.spectrum_x_limits = (x_lo, x_hi)
        ifm.spectrum_force_frames = 3
        dpg.set_axis_limits("spectrum_x_axis", x_lo, x_hi)
        _push_spectrum_view(x_lo, x_hi)
        dpg.fit_axis_data("spectrum_y_axis")
    except Exception:
        try:
            dpg.fit_axis_data("spectrum_x_axis")
            dpg.fit_axis_data("spectrum_y_axis")
        except Exception:
            pass


def process_spectrum_axis_limits():
    """Per-frame spectrum view force/unlock and resolution-preserving LOD."""
    if ifm.mode != Mode.AVERAGE:
        return
    if ifm.spectrum_force_frames > 0 and ifm.spectrum_x_limits is not None:
        x_lo, x_hi = ifm.spectrum_x_limits
        if dpg.does_item_exist("spectrum_x_axis"):
            dpg.set_axis_limits("spectrum_x_axis", x_lo, x_hi)
        ifm.spectrum_force_frames -= 1
        if ifm.spectrum_force_frames == 0:
            if dpg.does_item_exist("spectrum_x_axis"):
                dpg.set_axis_limits_auto("spectrum_x_axis")
            if dpg.does_item_exist("spectrum_y_axis"):
                dpg.set_axis_limits_auto("spectrum_y_axis")
        _push_spectrum_view(x_lo, x_hi)
        return
    # After unlock: if the user zoomed, re-slice so the window is full-res.
    if ifm.last_spectrum_x is None:
        return
    limits = _current_spectrum_x_limits()
    if limits is None:
        return
    prev = ifm.spectrum_lod_limits
    dirty = ifm.spectrum_lod_dirty
    if (
        not dirty
        and prev is not None
        and abs(prev[0] - limits[0]) < 1e-9
        and abs(prev[1] - limits[1]) < 1e-9
    ):
        return
    _push_spectrum_view(limits[0], limits[1])


def refresh_spectrum_from_cache():
    """Recompute the optical axis after calibration / axis-mode changes."""
    ifm.spectrum_needs_recenter = True
    d_frep = float(ifm.d_frep_hz or 0.0)
    # Δf_rep changes the RF frequency grid — must re-FFT, not just remap λ.
    if (
        ifm.last_spectrum_y is not None
        and abs(d_frep - float(ifm.last_d_frep or 0.0)) > 1e-12
    ):
        update_spectrum_plot(ifm.last_spectrum_y)
        return
    if ifm.last_rf_mag is not None and _remap_cached_rf_spectrum():
        _update_spectrum_axis_label()
        apply_spectrum_axis_limits()
        return
    if ifm.last_spectrum_y is not None:
        update_spectrum_plot(ifm.last_spectrum_y)


def _engine_progress_time(engine) -> float:
    """Latest of a real frame or a child heartbeat (wait/calib)."""
    hb = float(getattr(engine, "last_heartbeat", 0.0) or 0.0)
    return max(float(ifm.live_last_frame or 0.0), hb)


def _restart_stalled_capture(engine, channels, label: str) -> None:
    """Recycle the capture engine only when the child is dead or hung.

    Analog calibration and long waits between interferograms send heartbeats
    without frames. Killing the child there re-Commits the Razor and clicks
    the input relays, and wipes the running average.
    """
    last = _engine_progress_time(engine)
    stalled_for = time.monotonic() - last if last > 0.0 else 0.0
    alive = True
    child_alive = getattr(engine, "child_alive", None)
    if callable(child_alive):
        alive = bool(child_alive())
    phase = str(getattr(engine, "board_phase", "") or "")
    if alive and stalled_for < CAPTURE_STALL_TIMEOUT_S:
        return
    if alive:
        print(
            f"{label}: child hung "
            f"({stalled_for:.1f}s silent, phase={phase or 'unknown'}); "
            f"recycling Gage process..."
        )
    else:
        print(
            f"{label}: Gage child exited; restarting capture..."
        )
    engine.restart_capture(channels)
    ifm.live_last_frame = time.monotonic()


def live_view_tick():
    """Pull the latest Live View frame from the capture worker and update the plot."""
    engine = ifm.live_engine
    if engine is None or not ifm.gathering or ifm.mode != Mode.MONITOR:
        return

    now = time.monotonic()
    if now - ifm.live_last_tick < capture_min_interval_s():
        return
    ifm.live_last_tick = now

    channels = enabled_channels()
    if not channels:
        return

    try:
        progress = _engine_progress_time(engine)
        if progress > 0.0 and (now - progress) > CAPTURE_STALL_TIMEOUT_S:
            _restart_stalled_capture(engine, channels, "Live View")
            return

        data = engine.capture(channels)
        if data is not None:
            ifm.live_last_frame = time.monotonic()
        hb = float(getattr(engine, "last_heartbeat", 0.0) or 0.0)
        if hb > ifm.live_last_frame:
            ifm.live_last_frame = hb
        take = getattr(engine, "take_spectrum", None)
        spec = take() if callable(take) else None
        newer = getattr(engine, "full_traces_if_newer", lambda: None)()
        if newer is not None:
            apply_live_view_data(newer, spectrum=spec)
            ifm.live_last_frame = time.monotonic()
        elif spec is not None:
            _ingest_spectrum_payload(spec)
        elif data:
            apply_live_view_data(data, spectrum=None)
        ifm.live_error = None
    except Exception as e:
        msg = str(e)
        print(f"Live View error: {msg}")
        ifm.live_error = msg
        try:
            engine.stop()
        except Exception:
            pass
        set_gathering_ui(False)
        show_error_window(msg)


def average_tick():
    """Pull averaging progress from the capture worker and refresh the UI.

    Alignment and stacking run in the Gage child (or the simulator) so the
    UI thread never touches full-resolution traces at 40+ Hz.
    """
    engine = ifm.live_engine
    if engine is None or not ifm.gathering or ifm.mode != Mode.AVERAGE:
        return

    now = time.monotonic()
    channels = enabled_channels()
    if not channels:
        return

    try:
        progress = _engine_progress_time(engine)
        if progress > 0.0 and (now - progress) > CAPTURE_STALL_TIMEOUT_S:
            _restart_stalled_capture(engine, channels, "Average mode")
            status = getattr(engine, "average_status", lambda: None)()
            if status is not None:
                ifm.average_result = status
                update_average_status(status)
            return

        data = engine.capture(channels)
        hb = float(getattr(engine, "last_heartbeat", 0.0) or 0.0)
        if hb > ifm.live_last_frame:
            ifm.live_last_frame = hb
        status = getattr(engine, "average_status", lambda: None)()
        if status is not None:
            prev = ifm.average_result
            ifm.average_result = status
            if prev is None or (
                status.accepted != prev.accepted or status.rejected != prev.rejected
            ):
                ifm.live_last_frame = time.monotonic()
                log_progress = (
                    status.complete
                    or status.accepted <= 3
                    or (
                        prev is not None
                        and status.accepted > prev.accepted
                        and status.accepted % 25 == 0
                    )
                    or (
                        prev is not None
                        and status.rejected > prev.rejected
                        and status.rejected > 0
                        and status.rejected % 25 == 0
                    )
                )
                if log_progress:
                    print(
                        f"Average progress: {status.accepted}/{status.target} "
                        f"accepted, {status.rejected} rejected, "
                        f"last r={status.last_peak_corr:.3f}, lag={status.last_lag}"
                    )
            update_average_status(status)

        take = getattr(engine, "take_spectrum", None)
        spec = take() if callable(take) else None
        newer = getattr(engine, "full_traces_if_newer", lambda: None)()
        if newer is not None:
            apply_live_view_data(newer, spectrum=spec)
            ifm.live_error = None
        elif spec is not None:
            _ingest_spectrum_payload(spec)
            ifm.live_error = None
        elif data:
            apply_live_view_data(data, spectrum=None)
            ifm.live_error = None

        _maybe_autosave_spectrum()

        if status is not None and status.complete and ifm.gathering:
            finish_averaging(completed=True)
    except Exception as e:
        msg = str(e)
        print(f"Average mode error: {msg}")
        import traceback
        traceback.print_exc()
        ifm.live_error = msg
        _stop_capture_engine()
        set_gathering_ui(False)
        show_error_window(msg)


def save_ui_settings_from_widgets():
    """Snapshot current main-window field values into config.json."""
    data = {"mode": ifm.mode.name}
    if dpg.does_item_exist("interferograms_input"):
        data["interferograms"] = int(dpg.get_value("interferograms_input"))
    if dpg.does_item_exist("bulk_limit_input"):
        data["bulk_limit"] = int(dpg.get_value("bulk_limit_input"))
    if dpg.does_item_exist("bulk_unit_combo"):
        data["bulk_unit"] = dpg.get_value("bulk_unit_combo")
    if dpg.does_item_exist("threshold_slider"):
        data["threshold"] = float(dpg.get_value("threshold_slider"))
    if dpg.does_item_exist("save_enabled_checkbox"):
        data["save_enabled"] = bool(dpg.get_value("save_enabled_checkbox"))
        ifm.save_enabled = data["save_enabled"]
    if dpg.does_item_exist("save_file_input"):
        data["save_file"] = dpg.get_value("save_file_input") or ""
        ifm.save_file = data["save_file"]
    if dpg.does_item_exist("save_format_combo"):
        fmt = dpg.get_value("save_format_combo")
        if fmt in SAVE_FORMAT_ITEMS:
            data["save_format"] = fmt
            ifm.save_format = fmt
    if dpg.does_item_exist("save_when_combo"):
        when = dpg.get_value("save_when_combo")
        if when in SAVE_WHEN_ITEMS:
            data["save_when"] = when
            ifm.save_when = when
    if dpg.does_item_exist("mode_combo"):
        mode_label = dpg.get_value("mode_combo")
        mode = LABEL_TO_MODE.get(mode_label)
        if mode is not None:
            data["mode"] = mode.name
            ifm.mode = mode
    if dpg.does_item_exist("sample_rate_dropdown"):
        try:
            data["samplerate"] = parse_samplerate_label(dpg.get_value("sample_rate_dropdown"))
            ifm.samplerate = data["samplerate"]
        except (TypeError, ValueError):
            print(f"Invalid sample rate selected: {dpg.get_value('sample_rate_dropdown')}")
    if dpg.does_item_exist("input_range_dropdown"):
        try:
            data["input_range"] = parse_input_range_label(dpg.get_value("input_range_dropdown"))
            ifm.input_range = data["input_range"]
        except (TypeError, ValueError):
            print(f"Invalid input range selected: {dpg.get_value('input_range_dropdown')}")
    if dpg.does_item_exist("max_capture_rate_input"):
        try:
            hz = int(dpg.get_value("max_capture_rate_input"))
            hz = max(MIN_MAX_CAPTURE_RATE_HZ, min(MAX_MAX_CAPTURE_RATE_HZ, hz))
            data["max_capture_rate_hz"] = hz
            ifm.max_capture_rate_hz = hz
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("d_frep_input"):
        try:
            data["d_frep_hz"] = max(0.0, float(dpg.get_value("d_frep_input")))
            ifm.d_frep_hz = data["d_frep_hz"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("frio_input"):
        try:
            data["frio_mhz"] = max(0.0, float(dpg.get_value("frio_input")))
            ifm.frio_mhz = data["frio_mhz"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("m1_input"):
        try:
            data["m1"] = float(dpg.get_value("m1_input"))
            ifm.m1 = data["m1"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("m2_input"):
        try:
            data["m2"] = float(dpg.get_value("m2_input"))
            ifm.m2 = data["m2"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("m3_input"):
        try:
            data["m3"] = float(dpg.get_value("m3_input"))
            ifm.m3 = data["m3"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("spectrum_axis_combo"):
        axis = normalize_spectrum_axis(dpg.get_value("spectrum_axis_combo"))
        if axis in SPECTRUM_AXIS_ITEMS:
            data["spectrum_axis"] = axis
            ifm.spectrum_axis = axis
    if dpg.does_item_exist("apodization_combo"):
        apo = normalize_apodization(dpg.get_value("apodization_combo"))
        if apo in APODIZATION_ITEMS:
            data["apodization"] = apo
            ifm.apodization = apo
    if dpg.does_item_exist("pre_trigger_input"):
        try:
            data["pre_trigger_samples"] = max(
                MIN_PRE_TRIGGER_SAMPLES, int(dpg.get_value("pre_trigger_input"))
            )
            ifm.pre_trigger_samples = data["pre_trigger_samples"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("post_trigger_input"):
        try:
            data["post_trigger_samples"] = max(
                MIN_POST_TRIGGER_SAMPLES, int(dpg.get_value("post_trigger_input"))
            )
            ifm.post_trigger_samples = data["post_trigger_samples"]
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("channel1_checkbox"):
        data["channel1"] = bool(dpg.get_value("channel1_checkbox"))
        ifm.channel1 = data["channel1"]
    if dpg.does_item_exist("channel2_checkbox"):
        data["channel2"] = bool(dpg.get_value("channel2_checkbox"))
        ifm.channel2 = data["channel2"]
    if dpg.does_item_exist("channel3_checkbox"):
        data["channel3"] = bool(dpg.get_value("channel3_checkbox"))
        ifm.channel3 = data["channel3"]
    if dpg.does_item_exist("channel4_checkbox"):
        data["channel4"] = bool(dpg.get_value("channel4_checkbox"))
        ifm.channel4 = data["channel4"]
    if dpg.does_item_exist("trigger_source_dropdown"):
        source = dpg.get_value("trigger_source_dropdown")
        if source in TRIGGER_SOURCE_ITEMS:
            data["trigger_source"] = source
            ifm.trigger_source = source
    if dpg.does_item_exist("trigger_edge_dropdown"):
        edge = dpg.get_value("trigger_edge_dropdown")
        if edge in TRIGGER_EDGE_ITEMS:
            data["trigger_edge"] = edge
            ifm.trigger_edge = edge
    if dpg.does_item_exist("trigger_threshold_input"):
        try:
            threshold = int(dpg.get_value("trigger_threshold_input"))
            threshold = max(0, min(100, threshold))
            data["trigger_threshold"] = threshold
            ifm.trigger_threshold = threshold
        except (TypeError, ValueError):
            pass
    if dpg.does_item_exist("external_trigger_coupling_dropdown"):
        coupling = dpg.get_value("external_trigger_coupling_dropdown")
        if coupling in TRIGGER_COUPLING_ITEMS:
            data["ext_trigger_coupling"] = coupling
            ifm.ext_trigger_coupling = coupling
    if dpg.does_item_exist("external_trigger_input_range_dropdown"):
        try:
            data["ext_trigger_input_range"] = parse_trigger_input_range_label(
                dpg.get_value("external_trigger_input_range_dropdown")
            )
            ifm.ext_trigger_input_range = data["ext_trigger_input_range"]
        except (TypeError, ValueError):
            print(
                "Invalid external trigger input range selected: "
                f"{dpg.get_value('external_trigger_input_range_dropdown')}"
            )
    if dpg.does_item_exist("external_trigger_impedance_dropdown"):
        impedance = dpg.get_value("external_trigger_impedance_dropdown")
        if impedance in TRIGGER_IMPEDANCE_ITEMS:
            data["ext_trigger_impedance"] = impedance
            ifm.ext_trigger_impedance = impedance
    save_config(data, quiet=True)

def mode_callback(sender, app_data):
    mode = LABEL_TO_MODE.get(app_data)
    if mode is None:
        print(f"Unknown mode selected: {app_data}")
        return

    if ifm.gathering:
        # Mode combo is locked while running; if a stray event gets through,
        # actually halt capture so the child does not keep shooting.
        print("Stopping data gathering (mode change)...")
        _stop_capture_engine()
        set_gathering_ui(False)
        ifm.ignore_start_until = time.monotonic() + 0.4

    ifm.mode = mode
    update_mode_dependent_widgets()
    save_config({"mode": ifm.mode.name}, quiet=True)
    print(f"Mode set to: {ifm.mode.name}")

def samplerate_callback(sender, app_data):
    try:
        value = parse_samplerate_label(app_data)
    except (TypeError, ValueError):
        print(f"Invalid sample rate selected: {app_data}")
        return

    ifm.samplerate = value
    save_config({"samplerate": ifm.samplerate}, quiet=True)
    print(f"Sample rate set to: {ifm.samplerate} S/s")

def input_range_callback(sender, app_data):
    try:
        value = parse_input_range_label(app_data)
    except (TypeError, ValueError):
        print(f"Invalid input range selected: {app_data}")
        return

    ifm.input_range = value
    save_config({"input_range": ifm.input_range}, quiet=True)
    apply_live_axis_limits()
    print(f"Input range set to: {input_range_to_label(ifm.input_range)} ({ifm.input_range} mV pk-pk)")


def max_capture_rate_callback(sender, app_data):
    try:
        value = int(app_data)
    except (TypeError, ValueError):
        return
    value = max(MIN_MAX_CAPTURE_RATE_HZ, min(MAX_MAX_CAPTURE_RATE_HZ, value))
    ifm.max_capture_rate_hz = value
    if dpg.does_item_exist("max_capture_rate_input"):
        if int(dpg.get_value("max_capture_rate_input")) != value:
            dpg.set_value("max_capture_rate_input", value)
    save_config({"max_capture_rate_hz": value}, quiet=True)
    print(f"Max capture rate set to: {value} Hz")


def _sync_m3_from_m1_m2(force: bool = False):
    """Set m3 = m/k from m1,m2 (F_opt = 2 F_rio − m3 F_rf)."""
    new_m3 = default_m3_from_m1_m2(ifm.m1, ifm.m2)
    if force or abs(ifm.m3 - new_m3) > 1e-9:
        ifm.m3 = new_m3
        if dpg.does_item_exist("m3_input"):
            dpg.set_value("m3_input", ifm.m3)


def d_frep_callback(sender, app_data):
    try:
        value = float(app_data)
    except (TypeError, ValueError):
        return
    if value < 0:
        value = 0.0
    ifm.d_frep_hz = value
    save_config({"d_frep_hz": value}, quiet=True)
    refresh_spectrum_from_cache()


def frio_callback(sender, app_data):
    try:
        value = float(app_data)
    except (TypeError, ValueError):
        return
    if value < 0:
        value = 0.0
    ifm.frio_mhz = value
    save_config({"frio_mhz": value}, quiet=True)
    refresh_spectrum_from_cache()


def m1_callback(sender, app_data):
    try:
        value = float(app_data)
    except (TypeError, ValueError):
        return
    ifm.m1 = value
    _sync_m3_from_m1_m2(force=True)
    save_config({"m1": ifm.m1, "m3": ifm.m3}, quiet=True)
    refresh_spectrum_from_cache()


def m2_callback(sender, app_data):
    try:
        value = float(app_data)
    except (TypeError, ValueError):
        return
    ifm.m2 = value
    _sync_m3_from_m1_m2(force=True)
    save_config({"m2": ifm.m2, "m3": ifm.m3}, quiet=True)
    refresh_spectrum_from_cache()


def m3_callback(sender, app_data):
    try:
        value = float(app_data)
    except (TypeError, ValueError):
        return
    ifm.m3 = value
    save_config({"m3": value}, quiet=True)
    refresh_spectrum_from_cache()


def apodization_callback(sender, app_data):
    kind = normalize_apodization(app_data)
    if kind not in APODIZATION_ITEMS:
        return
    ifm.apodization = kind
    if (
        dpg.does_item_exist("apodization_combo")
        and dpg.get_value("apodization_combo") != kind
    ):
        dpg.set_value("apodization_combo", kind)
    save_config({"apodization": kind}, quiet=True)
    if ifm.last_spectrum_y is not None:
        update_spectrum_plot(ifm.last_spectrum_y)
    engine = ifm.live_engine
    updater = getattr(engine, "update_spectrum", None) if engine is not None else None
    if ifm.gathering and callable(updater):
        try:
            updater(_build_spectrum_dict())
        except Exception as e:
            print(f"Could not update live apodization: {e}")


def spectrum_axis_callback(sender, app_data):
    axis = normalize_spectrum_axis(app_data)
    if axis not in SPECTRUM_AXIS_ITEMS:
        return
    ifm.spectrum_axis = axis
    if (
        dpg.does_item_exist("spectrum_axis_combo")
        and dpg.get_value("spectrum_axis_combo") != axis
    ):
        dpg.set_value("spectrum_axis_combo", axis)
    save_config({"spectrum_axis": axis}, quiet=True)
    _update_spectrum_axis_label()
    refresh_spectrum_from_cache()


def _clamp_live_sample_inputs():
    """Keep pre/post fields valid and within MAX_LIVE_SAMPLES total.

    Returns ``(pre, post, changed)`` so callers can skip redundant work when
    the spinner fires repeatedly at a clamp limit.
    """
    try:
        pre = int(dpg.get_value("pre_trigger_input")) if dpg.does_item_exist("pre_trigger_input") else ifm.pre_trigger_samples
    except (TypeError, ValueError):
        pre = ifm.pre_trigger_samples
    try:
        post = int(dpg.get_value("post_trigger_input")) if dpg.does_item_exist("post_trigger_input") else ifm.post_trigger_samples
    except (TypeError, ValueError):
        post = ifm.post_trigger_samples

    pre = max(MIN_PRE_TRIGGER_SAMPLES, min(MAX_LIVE_SAMPLES, pre))
    post = max(MIN_POST_TRIGGER_SAMPLES, min(MAX_LIVE_SAMPLES, post))
    if pre + post > MAX_LIVE_SAMPLES:
        # Prefer preserving post-trigger depth when clamping the total.
        pre = max(MIN_PRE_TRIGGER_SAMPLES, MAX_LIVE_SAMPLES - post)
    pre, post = normalize_live_window(pre, post)
    changed = (
        pre != ifm.pre_trigger_samples or post != ifm.post_trigger_samples
    )
    ifm.pre_trigger_samples = pre
    ifm.post_trigger_samples = post
    if dpg.does_item_exist("pre_trigger_input"):
        if int(dpg.get_value("pre_trigger_input")) != pre:
            dpg.set_value("pre_trigger_input", pre)
    if dpg.does_item_exist("post_trigger_input"):
        if int(dpg.get_value("post_trigger_input")) != post:
            dpg.set_value("post_trigger_input", post)
    return pre, post, changed


def pre_trigger_callback(sender, app_data):
    pre, post, changed = _clamp_live_sample_inputs()
    if not changed:
        return
    save_config(
        {"pre_trigger_samples": pre, "post_trigger_samples": post},
        quiet=True,
    )
    apply_live_axis_limits()
    print(f"Live View window: {pre} pre + {post} post = {pre + post} samples")


def post_trigger_callback(sender, app_data):
    pre, post, changed = _clamp_live_sample_inputs()
    if not changed:
        return
    save_config(
        {"pre_trigger_samples": pre, "post_trigger_samples": post},
        quiet=True,
    )
    apply_live_axis_limits()
    print(f"Live View window: {pre} pre + {post} post = {pre + post} samples")


def channel_callback(sender, app_data, user_data):
    channel_number = user_data
    enabled = bool(app_data)
    if channel_number == 1:
        ifm.channel1 = enabled
    elif channel_number == 2:
        ifm.channel2 = enabled
    elif channel_number == 3:
        ifm.channel3 = enabled
    elif channel_number == 4:
        ifm.channel4 = enabled
    else:
        print(f"Unknown channel number: {channel_number}")
        return

    save_config({
        "channel1": ifm.channel1,
        "channel2": ifm.channel2,
        "channel3": ifm.channel3,
        "channel4": ifm.channel4,
    }, quiet=True)
    update_channel_series_visibility()
    print(f"Channel {channel_number} set to: {'enabled' if enabled else 'disabled'}")

def button1_callback(sender, app_data):
    print("Start/Stop clicked")
    try:
        playsound3.playsound("src/knopka-iz-igry-2.mp3", block=False)
    except Exception:
        pass

    if ifm.gathering:
        print("Stopping data gathering...")
        if ifm.mode == Mode.AVERAGE:
            finish_averaging(completed=False)
        else:
            _stop_capture_engine()
            set_gathering_ui(False)
        # Arm after halt/UI flip: Dear PyGui can fire this same button again
        # as Start once the label changes under a still-held click.
        ifm.ignore_start_until = time.monotonic() + 0.4
        return

    if time.monotonic() < ifm.ignore_start_until:
        print("Ignoring Start (still releasing Stop)")
        return

    channels = enabled_channels()
    if not channels:
        show_error_window("Enable at least one channel before starting.")
        return

    if ifm.mode == Mode.MONITOR:
        pre, post, _ = _clamp_live_sample_inputs()
        if not _start_capture_engine(channels, pre, post, "Live View"):
            return
        set_gathering_ui(True)
        return

    if ifm.mode == Mode.COLLECT:
        show_error_window("Collect bulk data mode is not implemented yet.")
        return

    if ifm.mode == Mode.AVERAGE:
        pre, post, _ = _clamp_live_sample_inputs()
        ifm.threshold = _current_threshold()
        ifm.interferograms_target = _current_interferograms_target()
        ifm.average_result = None
        ifm.last_autosave_t = time.monotonic()
        print(
            f"Average mode: target={ifm.interferograms_target}, "
            f"correlation threshold r≥{ifm.threshold:.3f}, "
            f"reference channel={channels[0]}"
        )
        if not _start_capture_engine(
            channels,
            pre,
            post,
            "Average interferograms",
            average={
                "target": ifm.interferograms_target,
                "threshold": ifm.threshold,
                "reference_channel": channels[0],
            },
        ):
            ifm.average_result = None
            return
        ifm.live_last_frame = time.monotonic()
        update_average_status(
            AverageResult(target=ifm.interferograms_target)
        )
        set_gathering_ui(True)
        return

def interferograms_callback(sender, app_data):
    try:
        value = int(app_data)
    except (TypeError, ValueError):
        return
    ifm.interferograms_target = max(1, value)
    save_config({"interferograms": ifm.interferograms_target}, quiet=True)

def bulk_limit_callback(sender, app_data):
    try:
        value = int(app_data)
    except (TypeError, ValueError):
        return
    save_config({"bulk_limit": value}, quiet=True)

def bulk_unit_callback(sender, app_data):
    if app_data not in BULK_UNITS:
        print(f"Unknown bulk unit selected: {app_data}")
        return
    save_config({"bulk_unit": app_data}, quiet=True)

def threshold_callback(sender, app_data):
    try:
        value = float(app_data)
    except (TypeError, ValueError):
        return
    ifm.threshold = max(0.0, min(1.0, value))
    save_config({"threshold": ifm.threshold}, quiet=True)

def save_enabled_callback(sender, app_data):
    ifm.save_enabled = bool(app_data)
    save_config({"save_enabled": ifm.save_enabled}, quiet=True)

def save_file_text_callback(sender, app_data):
    ifm.save_file = app_data if isinstance(app_data, str) else ""
    save_config({"save_file": ifm.save_file}, quiet=True)

def save_format_callback(sender, app_data):
    if app_data not in SAVE_FORMAT_ITEMS:
        return
    ifm.save_format = app_data
    save_config({"save_format": app_data}, quiet=True)

def save_when_callback(sender, app_data):
    if app_data not in SAVE_WHEN_ITEMS:
        return
    ifm.save_when = app_data
    save_config({"save_when": app_data}, quiet=True)

def save_file_callback(sender, app_data):
    if isinstance(app_data, dict):
        selected_file = app_data.get("file_path_name", "")
    else:
        selected_file = app_data

    ifm.save_file = selected_file
    dpg.set_value("save_file_input", selected_file)
    save_config({"save_file": selected_file}, quiet=True)
    print(f"Save file set to: {ifm.save_file}")

def trigger_source_callback(sender, app_data):
    if app_data not in TRIGGER_SOURCE_ITEMS:
        print(f"Unknown trigger source selected: {app_data}")
        return
    ifm.trigger_source = app_data
    update_external_trigger_controls()
    save_config({"trigger_source": ifm.trigger_source}, quiet=True)
    print(f"Trigger source set to: {ifm.trigger_source}")


def trigger_edge_callback(sender, app_data):
    if app_data not in TRIGGER_EDGE_ITEMS:
        print(f"Unknown trigger edge selected: {app_data}")
        return
    ifm.trigger_edge = app_data
    save_config({"trigger_edge": ifm.trigger_edge}, quiet=True)
    print(f"Trigger edge set to: {ifm.trigger_edge}")


def trigger_threshold_callback(sender, app_data):
    try:
        value = int(app_data)
    except (TypeError, ValueError):
        return
    value = max(0, min(100, value))
    ifm.trigger_threshold = value
    if dpg.does_item_exist("trigger_threshold_input"):
        dpg.set_value("trigger_threshold_input", value)
    save_config({"trigger_threshold": ifm.trigger_threshold}, quiet=True)
    print(f"Trigger threshold set to: {ifm.trigger_threshold}%")


def external_trigger_coupling_callback(sender, app_data):
    if app_data not in TRIGGER_COUPLING_ITEMS:
        print(f"Unknown external trigger coupling selected: {app_data}")
        return
    ifm.ext_trigger_coupling = app_data
    save_config({"ext_trigger_coupling": ifm.ext_trigger_coupling}, quiet=True)
    print(f"External trigger coupling set to: {ifm.ext_trigger_coupling}")


def external_trigger_input_range_callback(sender, app_data):
    try:
        value = parse_trigger_input_range_label(app_data)
    except (TypeError, ValueError):
        print(f"Invalid external trigger input range selected: {app_data}")
        return
    ifm.ext_trigger_input_range = value
    save_config({"ext_trigger_input_range": ifm.ext_trigger_input_range}, quiet=True)
    print(
        "External trigger input range set to: "
        f"{trigger_input_range_to_label(value)} ({value} mV pk-pk)"
    )


def external_trigger_impedance_callback(sender, app_data):
    if app_data not in TRIGGER_IMPEDANCE_ITEMS:
        print(f"Unknown external trigger impedance selected: {app_data}")
        return
    ifm.ext_trigger_impedance = app_data
    save_config({"ext_trigger_impedance": ifm.ext_trigger_impedance}, quiet=True)
    print(f"External trigger impedance set to: {ifm.ext_trigger_impedance}")


def add_settings_label(text: str, font) -> int | str:
    """Left-aligned small caption drawn above a full-width settings control."""
    label_id = dpg.add_text(text)
    dpg.bind_item_font(label_id, font)
    return label_id


def main():
    dpg.create_context()
    # vsync=True: UI frame rate tracks the monitor refresh (60 / 120 / 144…).
    # Waveform capture rate is capped separately by max_capture_rate_hz.
    dpg.create_viewport(title="Interferomatic", vsync=True)

    with dpg.font_registry():
        default_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 80)
        giant_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 240)
        small_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 65)

    dpg.bind_font(default_font)

    with dpg.theme(tag="global_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(
                dpg.mvStyleVar_FrameRounding,
                5,
                category=dpg.mvThemeCat_Core,
            )
            dpg.add_theme_style(
                dpg.mvStyleVar_WindowPadding,
                20,
                10,
                category=dpg.mvThemeCat_Core,
            )
            dpg.add_theme_style(
                dpg.mvStyleVar_FramePadding,
                15,
                6,
                category=dpg.mvThemeCat_Core,
            )
            dpg.add_theme_style(
                dpg.mvPlotStyleVar_LineWeight,
                4.0,
                category=dpg.mvThemeCat_Plots
            )
    dpg.bind_theme("global_theme")

    dpg.setup_dearpygui()

    dpg.show_style_editor()

    dpg.show_viewport()

    font_scale = resolve_font_scale()
    dpg.set_global_font_scale(font_scale)
    print(f"Using font scale: {font_scale:.2f}")

    ui = load_ui_settings()
    apply_ui_settings_to_state(ui)
    print(
        f"Loaded UI settings: mode={ui['mode']}, samplerate={ui['samplerate']}, "
        f"input_range={input_range_to_label(ui['input_range'])}, "
        f"live_window={ui['pre_trigger_samples']}+{ui['post_trigger_samples']}, "
        f"max_capture={ui['max_capture_rate_hz']} Hz, "
        f"trigger={ui['trigger_source']}/{ui['trigger_edge']}@{ui['trigger_threshold']}%, "
        f"ext_trig=[{ui['ext_trigger_coupling']}, "
        f"{trigger_input_range_to_label(ui['ext_trigger_input_range'])}, "
        f"{ui['ext_trigger_impedance']}], "
        f"channels=[{ui['channel1']}, {ui['channel2']}, {ui['channel3']}, {ui['channel4']}], "
        f"interferograms={ui['interferograms']}, bulk_limit={ui['bulk_limit']} {ui['bulk_unit']}, "
        f"threshold={ui['threshold']}, save_file={ui['save_file']!r}"
    )

    # Live View engine (real Gage card when available, otherwise simulated).
    ifm.live_engine = create_live_view_engine(ifm.has_gage)
    try:
        ifm.live_engine.open()
    except Exception as e:
        print(f"Warning: could not open Live View engine: {e}")
        # Fall back to simulation so the UI remains usable.
        ifm.has_gage = False
        ifm.live_engine = create_live_view_engine(False)
        ifm.live_engine.open()

    # Control widths for the left settings column (half-screen layout).
    SETTINGS_WIDTH = -1  # fill the left column

    with dpg.window(label="Main Window", tag="main_window"):
        # File dialog is modal and not part of the layout.
        dpg.add_file_dialog(
            label="Select save file",
            directory_selector=False,
            show=False,
            callback=save_file_callback,
            tag="file_dialog",
            modal=True,
            width=2000,
            height=1000,
        )

        # Two-column layout: settings (left) | Live View (right).
        with dpg.table(
            tag="main_layout_table",
            header_row=False,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
            borders_innerV=True,
            borders_outerV=False,
            borders_innerH=False,
            borders_outerH=False,
            pad_outerX=False,
            height=-1,
        ):
            dpg.add_table_column(
                tag="settings_column",
                init_width_or_weight=0.2,
                width_stretch=True,
            )
            dpg.add_table_column(
                tag="plot_column",
                init_width_or_weight=0.8,
                width_stretch=True,
            )

            with dpg.table_row():
                # --- Left: all controls (scrollable) ---
                with dpg.table_cell():
                    with dpg.child_window(
                        tag="settings_panel",
                        width=-1,
                        height=-1,
                        border=False,
                        autosize_x=False,
                        autosize_y=False,
                        # Without a border, ImGui skips WindowPadding unless this
                        # is set — so width=-1 controls were flush on the right.
                        always_use_window_padding=True,
                    ):
                        dpg.add_spacer(height=20)
                        dpg.add_separator()
                        dpg.add_spacer(height=20)

                        # Center the Start/Stop button in the settings column.
                        with dpg.table(
                            header_row=False,
                            policy=dpg.mvTable_SizingStretchProp,
                            borders_innerV=False,
                            borders_outerV=False,
                            borders_innerH=False,
                            borders_outerH=False,
                            pad_outerX=False,
                        ):
                            dpg.add_table_column(
                                init_width_or_weight=1, width_stretch=True
                            )
                            dpg.add_table_column(
                                init_width_or_weight=0, width_fixed=True
                            )
                            dpg.add_table_column(
                                init_width_or_weight=1, width_stretch=True
                            )
                            with dpg.table_row():
                                dpg.add_table_cell()
                                with dpg.table_cell():
                                    dpg.add_button(
                                        label="Start",
                                        tag="startstop_button",
                                        callback=button1_callback,
                                    )
                                dpg.add_table_cell()

                        if not ifm.has_gage:
                            dpg.add_spacer(height=12)
                            gage_warn = dpg.add_text(
                                "Warning: No Gage card detected! "
                                "Now using fake sample data",
                                tag="gage_less_warning",
                                color=(255, 170, 40, 255),
                                wrap=280,
                            )
                            dpg.bind_item_font(gage_warn, small_font)

                        dpg.add_spacer(height=20)
                        dpg.add_separator()

                        add_settings_label("Choose mode", small_font)
                        dpg.add_combo(
                            items=list(MODE_LABELS.values()),
                            tag="mode_combo",
                            callback=mode_callback,
                            default_value=MODE_LABELS[ifm.mode],
                            width=SETTINGS_WIDTH,
                        )

                        dpg.add_spacer(height=20)
                        dpg.add_separator()

                        with dpg.group(
                            tag="average_controls",
                            show=ifm.mode == Mode.AVERAGE,
                        ):
                            add_settings_label(
                                "Interferograms to average", small_font
                            )
                            dpg.add_input_int(
                                tag="interferograms_input",
                                default_value=ui["interferograms"],
                                width=SETTINGS_WIDTH,
                                callback=interferograms_callback,
                            )
                            with dpg.tooltip("interferograms_input"):
                                dpg.add_text(
                                    "Number of phase-aligned interferograms to "
                                    "accept into the average before stopping. "
                                    "Noise falls roughly as 1/sqrt(N)."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label(
                                "Cross correlational threshold", small_font
                            )
                            dpg.add_slider_float(
                                tag="threshold_slider",
                                default_value=ui["threshold"],
                                min_value=0.0,
                                max_value=1.0,
                                width=SETTINGS_WIDTH,
                                callback=threshold_callback,
                            )
                            with dpg.tooltip("threshold_slider"):
                                dpg.add_text(
                                    "Minimum peak cross-correlation coefficient "
                                    "(0–1) a new reading must reach against the "
                                    "running average to be included. The lag at "
                                    "that peak aligns each interferogram before "
                                    "averaging."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("Apodization", small_font)
                            dpg.add_combo(
                                tag="apodization_combo",
                                items=list(APODIZATION_ITEMS),
                                default_value=ifm.apodization,
                                width=SETTINGS_WIDTH,
                                callback=apodization_callback,
                            )
                            with dpg.tooltip("apodization_combo"):
                                dpg.add_text(
                                    "Window applied to the interferogram "
                                    "before the FFT, centered on the trigger "
                                    "(zero path difference). Tapers the finite "
                                    "record so the spectrum does not ring.\n"
                                    "Boxcar: no taper (highest resolution).\n"
                                    "Triangular / Cosine / Happ-Genzel: "
                                    "weaker sidelobes, slightly broader peaks.\n"
                                    "Lorenz / Gaussian: stronger taper."
                                )

                            dpg.add_spacer(height=8)
                            status_id = dpg.add_text(
                                "",
                                tag="average_status_text",
                                wrap=280,
                            )
                            dpg.bind_item_font(status_id, small_font)
                            dpg.add_spacer(height=20)

                        with dpg.group(
                            tag="bulk_controls",
                            show=ifm.mode == Mode.COLLECT,
                        ):
                            add_settings_label("Collect until", small_font)
                            with dpg.group(horizontal=True):
                                dpg.add_input_int(
                                    tag="bulk_limit_input",
                                    default_value=ui["bulk_limit"],
                                    width=200,
                                    callback=bulk_limit_callback,
                                )
                                dpg.add_combo(
                                    items=list(BULK_UNITS),
                                    tag="bulk_unit_combo",
                                    default_value=ui["bulk_unit"],
                                    width=150,
                                    callback=bulk_unit_callback,
                                )
                            with dpg.tooltip("bulk_limit_input"):
                                dpg.add_text(
                                    "Stop bulk collection after this amount of "
                                    "data or time"
                                )
                            with dpg.tooltip("bulk_unit_combo"):
                                dpg.add_text(
                                    "Unit for the collection limit: data size "
                                    "(MB, GB) or duration (seconds, minutes)"
                                )
                            dpg.add_spacer(height=20)

                        with dpg.collapsing_header(
                            label="Input", default_open=True
                        ):
                            add_settings_label("Sample Rate", small_font)
                            dpg.add_combo(
                                tag="sample_rate_dropdown",
                                items=list(SAMPLERATE_LABELS.values()),
                                default_value=samplerate_to_label(
                                    ifm.samplerate
                                ),
                                width=SETTINGS_WIDTH,
                                callback=samplerate_callback,
                            )

                            dpg.add_spacer(height=8)
                            add_settings_label("Channels", small_font)
                            dpg.add_checkbox(
                                label="Channel 1",
                                tag="channel1_checkbox",
                                default_value=ifm.channel1,
                                callback=channel_callback,
                                user_data=1,
                            )
                            dpg.add_checkbox(
                                label="Channel 2",
                                tag="channel2_checkbox",
                                default_value=ifm.channel2,
                                callback=channel_callback,
                                user_data=2,
                            )
                            dpg.add_checkbox(
                                label="Channel 3",
                                tag="channel3_checkbox",
                                default_value=ifm.channel3,
                                callback=channel_callback,
                                user_data=3,
                            )
                            dpg.add_checkbox(
                                label="Channel 4",
                                tag="channel4_checkbox",
                                default_value=ifm.channel4,
                                callback=channel_callback,
                                user_data=4,
                            )

                            dpg.add_spacer(height=8)
                            add_settings_label("Input Range", small_font)
                            dpg.add_combo(
                                tag="input_range_dropdown",
                                items=INPUT_RANGE_COMBO_ITEMS,
                                default_value=input_range_to_label(
                                    ifm.input_range
                                ),
                                width=SETTINGS_WIDTH,
                                callback=input_range_callback,
                            )
                            with dpg.tooltip("input_range_dropdown"):
                                dpg.add_text(
                                    "Full-scale input range for all active "
                                    "channels (Gage InputRange, peak-to-peak). "
                                    "Sets the Live View vertical scale."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("Max capture rate (Hz)", small_font)
                            dpg.add_input_int(
                                tag="max_capture_rate_input",
                                default_value=ifm.max_capture_rate_hz,
                                width=SETTINGS_WIDTH,
                                min_value=MIN_MAX_CAPTURE_RATE_HZ,
                                max_value=MAX_MAX_CAPTURE_RATE_HZ,
                                min_clamped=True,
                                max_clamped=True,
                                callback=max_capture_rate_callback,
                            )
                            with dpg.tooltip("max_capture_rate_input"):
                                dpg.add_text(
                                    "Maximum Live View / averaging capture "
                                    "attempts per second. Limited by trigger "
                                    "rate, transfer time, and monitor refresh. "
                                    f"Range {MIN_MAX_CAPTURE_RATE_HZ}–"
                                    f"{MAX_MAX_CAPTURE_RATE_HZ} Hz."
                                )

                        dpg.add_separator()
                        dpg.add_spacer(height=20)

                        with dpg.collapsing_header(
                            label="Triggering", default_open=True
                        ):
                            add_settings_label(
                                "Samples before trigger", small_font
                            )
                            dpg.add_input_int(
                                tag="pre_trigger_input",
                                default_value=ifm.pre_trigger_samples,
                                width=SETTINGS_WIDTH,
                                min_value=MIN_PRE_TRIGGER_SAMPLES,
                                max_value=MAX_LIVE_SAMPLES,
                                step=LIVE_DEPTH_INCREMENT,
                                step_fast=LIVE_DEPTH_INCREMENT * 10,
                                min_clamped=True,
                                max_clamped=True,
                                callback=pre_trigger_callback,
                            )
                            with dpg.tooltip("pre_trigger_input"):
                                dpg.add_text(
                                    "Pre-trigger samples shown to the left of "
                                    "the trigger (sample 0). Adjusted in steps "
                                    f"of {LIVE_DEPTH_INCREMENT} (board depth "
                                    "alignment)."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label(
                                "Samples after trigger", small_font
                            )
                            dpg.add_input_int(
                                tag="post_trigger_input",
                                default_value=ifm.post_trigger_samples,
                                width=SETTINGS_WIDTH,
                                min_value=MIN_POST_TRIGGER_SAMPLES,
                                max_value=MAX_LIVE_SAMPLES,
                                step=LIVE_DEPTH_INCREMENT,
                                step_fast=LIVE_DEPTH_INCREMENT * 10,
                                min_clamped=True,
                                max_clamped=True,
                                callback=post_trigger_callback,
                            )
                            with dpg.tooltip("post_trigger_input"):
                                dpg.add_text(
                                    "Post-trigger samples shown to the right of "
                                    "the trigger (sample 0). Default total "
                                    f"window ≈ 20k samples. Steps of "
                                    f"{LIVE_DEPTH_INCREMENT}."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("Trigger Source", small_font)
                            dpg.add_combo(
                                tag="trigger_source_dropdown",
                                items=list(TRIGGER_SOURCE_ITEMS),
                                default_value=ifm.trigger_source,
                                width=SETTINGS_WIDTH,
                                callback=trigger_source_callback,
                            )

                            dpg.add_spacer(height=8)
                            add_settings_label("Trigger Edge", small_font)
                            dpg.add_combo(
                                tag="trigger_edge_dropdown",
                                items=list(TRIGGER_EDGE_ITEMS),
                                default_value=ifm.trigger_edge,
                                width=SETTINGS_WIDTH,
                                callback=trigger_edge_callback,
                            )

                            dpg.add_spacer(height=8)
                            add_settings_label(
                                "Trigger Threshold (%)", small_font
                            )
                            dpg.add_input_int(
                                tag="trigger_threshold_input",
                                default_value=ifm.trigger_threshold,
                                min_value=0,
                                max_value=100,
                                min_clamped=True,
                                max_clamped=True,
                                width=SETTINGS_WIDTH,
                                callback=trigger_threshold_callback,
                            )
                            with dpg.tooltip("trigger_threshold_input"):
                                dpg.add_text(
                                    "Threshold for triggering, as a percentage "
                                    "of the full scale."
                                )

                            with dpg.group(
                                tag="external_trigger_controls",
                                show=(ifm.trigger_source == "External"),
                            ):
                                dpg.add_spacer(height=8)
                                add_settings_label(
                                    "External Trigger Coupling", small_font
                                )
                                dpg.add_combo(
                                    tag="external_trigger_coupling_dropdown",
                                    items=list(TRIGGER_COUPLING_ITEMS),
                                    default_value=ifm.ext_trigger_coupling,
                                    width=SETTINGS_WIDTH,
                                    callback=external_trigger_coupling_callback,
                                )

                                dpg.add_spacer(height=8)
                                add_settings_label(
                                    "External Trigger Input Range", small_font
                                )
                                dpg.add_combo(
                                    tag="external_trigger_input_range_dropdown",
                                    items=TRIGGER_INPUT_RANGE_COMBO_ITEMS,
                                    default_value=trigger_input_range_to_label(
                                        ifm.ext_trigger_input_range
                                    ),
                                    width=SETTINGS_WIDTH,
                                    callback=external_trigger_input_range_callback,
                                )

                                dpg.add_spacer(height=8)
                                add_settings_label(
                                    "External Trigger Impedance", small_font
                                )
                                dpg.add_combo(
                                    tag="external_trigger_impedance_dropdown",
                                    items=list(TRIGGER_IMPEDANCE_ITEMS),
                                    default_value=ifm.ext_trigger_impedance,
                                    width=SETTINGS_WIDTH,
                                    callback=external_trigger_impedance_callback,
                                )

                        dpg.add_separator()
                        dpg.add_spacer(height=20)

                        with dpg.collapsing_header(
                            label="Spectrum (FFT)",
                            tag="spectrum_settings_header",
                            default_open=True,
                            show=ifm.mode == Mode.AVERAGE,
                        ):
                            add_settings_label("Spectrum X axis", small_font)
                            dpg.add_combo(
                                tag="spectrum_axis_combo",
                                items=list(SPECTRUM_AXIS_ITEMS),
                                default_value=ifm.spectrum_axis,
                                width=SETTINGS_WIDTH,
                                callback=spectrum_axis_callback,
                            )
                            with dpg.tooltip("spectrum_axis_combo"):
                                dpg.add_text(
                                    "Horizontal axis for the FFT plot: "
                                    "wavelength lambda = c/F_opt (nm) or "
                                    f"wavenumber 1/lambda ({WAVENUMBER_UNIT})."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("Δf_rep (Hz)", small_font)
                            dpg.add_input_float(
                                tag="d_frep_input",
                                default_value=ifm.d_frep_hz,
                                width=SETTINGS_WIDTH,
                                format="%.4f",
                                callback=d_frep_callback,
                            )
                            with dpg.tooltip("d_frep_input"):
                                dpg.add_text(
                                    "Dual-comb repetition-rate difference "
                                    "Δf_rep. For k=1 typically 22.92 Hz; for "
                                    "k=2 (current setup) 45.84 Hz. "
                                    "k = m₂ − m₁."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("F_rio (MHz)", small_font)
                            dpg.add_input_float(
                                tag="frio_input",
                                default_value=ifm.frio_mhz,
                                width=SETTINGS_WIDTH,
                                format="%.3f",
                                callback=frio_callback,
                            )
                            with dpg.tooltip("frio_input"):
                                dpg.add_text(
                                    "Optical reference frequency F_rio in MHz. "
                                    "F_opt = 2·F_rio − m₃·F_rf."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("m₁", small_font)
                            dpg.add_input_float(
                                tag="m1_input",
                                default_value=ifm.m1,
                                width=SETTINGS_WIDTH,
                                format="%.0f",
                                callback=m1_callback,
                                step=1,
                            )
                            dpg.add_spacer(height=4)
                            add_settings_label("m₂", small_font)
                            dpg.add_input_float(
                                tag="m2_input",
                                default_value=ifm.m2,
                                width=SETTINGS_WIDTH,
                                format="%.0f",
                                callback=m2_callback,
                                step=1,
                            )
                            with dpg.tooltip("m1_input"):
                                dpg.add_text(
                                    "Integer m₁. "
                                    "m = (m₁+m₂)/2, k = m₂−m₁, "
                                    "m₃ defaults to m/k."
                                )
                            with dpg.tooltip("m2_input"):
                                dpg.add_text(
                                    "Integer m₂. "
                                    "m = (m₁+m₂)/2, k = m₂−m₁, "
                                    "m₃ defaults to m/k."
                                )

                            dpg.add_spacer(height=4)
                            add_settings_label("m₃ (F_rf coefficient)", small_font)
                            dpg.add_input_float(
                                tag="m3_input",
                                default_value=ifm.m3,
                                width=SETTINGS_WIDTH,
                                format="%.6f",
                                callback=m3_callback,
                            )
                            with dpg.tooltip("m3_input"):
                                dpg.add_text(
                                    "Multiplier in F_opt = 2·F_rio − m₃·F_rf. "
                                    "Auto-set to m/k when m₁ or m₂ changes; "
                                    "edit freely to override. "
                                    "For k=1: m₃=m; for k=2: m₃=m/2."
                                )

                        dpg.add_separator()
                        dpg.add_spacer(height=20)

                        with dpg.collapsing_header(
                            label="Interferomatic settings",
                            default_open=True,
                        ):
                            dpg.add_checkbox(
                                label="Save to file",
                                tag="save_enabled_checkbox",
                                default_value=ifm.save_enabled,
                                callback=save_enabled_callback,
                            )
                            with dpg.tooltip("save_enabled_checkbox"):
                                dpg.add_text(
                                    "Write the averaged spectrum to the "
                                    "path below. Unchecked: nothing is saved."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("Save file", small_font)
                            dpg.add_input_text(
                                tag="save_file_input",
                                default_value=ui["save_file"],
                                width=SETTINGS_WIDTH,
                                callback=save_file_text_callback,
                            )
                            dpg.add_button(
                                label="Browse",
                                tag="browse_button",
                                callback=lambda: dpg.show_item("file_dialog"),
                            )
                            with dpg.tooltip("save_file_input"):
                                dpg.add_text(
                                    "File for the averaged spectrum. "
                                    "CSV is text; binary is raw float64 pairs."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("Save format", small_font)
                            dpg.add_combo(
                                tag="save_format_combo",
                                items=list(SAVE_FORMAT_ITEMS),
                                default_value=ifm.save_format,
                                width=SETTINGS_WIDTH,
                                callback=save_format_callback,
                            )
                            with dpg.tooltip("save_format_combo"):
                                dpg.add_text(
                                    "Binary: little-endian float64 pairs "
                                    "(x, amplitude) using the current X axis.\n"
                                    "CSV (nm, amplitude): wavelength and "
                                    "raw FFT amplitude.\n"
                                    "CSV (cm^-1, amplitude): wavenumber and "
                                    "raw FFT amplitude."
                                )

                            dpg.add_spacer(height=8)
                            add_settings_label("When to save", small_font)
                            dpg.add_combo(
                                tag="save_when_combo",
                                items=list(SAVE_WHEN_ITEMS),
                                default_value=ifm.save_when,
                                width=SETTINGS_WIDTH,
                                callback=save_when_callback,
                            )
                            with dpg.tooltip("save_when_combo"):
                                dpg.add_text(
                                    "On finish: write once when averaging "
                                    "stops (target reached or Stop).\n"
                                    "Every 10 seconds: overwrite the file "
                                    "while averaging, and again at the end."
                                )

                            dpg.add_spacer(height=8)
                            dpg.add_button(
                                label="Fullscreen",
                                tag="fullscreen_button",
                                callback=lambda: dpg.toggle_viewport_fullscreen(),
                            )
                            dpg.add_button(
                                label="Change Scale",
                                tag="scale_button",
                                callback=lambda: change_font_scale(),
                            )
                            dpg.add_button(
                                label="Reset to Default",
                                tag="reset_defaults_button",
                                callback=lambda: reset_settings_to_defaults(),
                            )
                            with dpg.tooltip("reset_defaults_button"):
                                dpg.add_text(
                                    "Restore all acquisition, trigger, spectrum, "
                                    "and save settings to factory defaults.\n"
                                    "Disabled while collecting data. "
                                    "UI scale is unchanged."
                                )
                            dpg.add_button(
                                label="Exit",
                                tag="exit_button",
                                callback=lambda: dpg.stop_dearpygui(),
                            )

                        dpg.add_spacer(height=20)

                # --- Right: Live View plot ---
                with dpg.table_cell():
                    with dpg.child_window(
                        tag="plot_panel",
                        width=-1,
                        height=-1,
                        border=False,
                        autosize_x=False,
                        autosize_y=False,
                    ):
                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                label="Reset View",
                                tag="reset_view_button",
                                callback=lambda: apply_live_axis_limits(),
                            )
                            with dpg.tooltip("reset_view_button"):
                                dpg.add_text(
                                    "Reset interferogram zoom/pan to the full "
                                    "capture window.\n"
                                    "Scroll = zoom, right-drag = pan, "
                                    "right-drag box = zoom to region, "
                                    "double-click axis = fit."
                                )
                            dpg.add_button(
                                label="Reset Spectrum",
                                tag="reset_spectrum_button",
                                callback=lambda: apply_spectrum_axis_limits(),
                                show=ifm.mode == Mode.AVERAGE,
                            )
                            with dpg.tooltip("reset_spectrum_button"):
                                dpg.add_text(
                                    "Fit the FFT spectrum axes to the current data."
                                )

                        # Top: time-domain interferogram; bottom: optical FFT.
                        with dpg.subplots(
                            2 if ifm.mode == Mode.AVERAGE else 1,
                            1,
                            label="",
                            tag="plot_subplots",
                            width=-1,
                            height=-1,
                            row_ratios=(1.0, 1.0) if ifm.mode == Mode.AVERAGE else (1.0,),
                            no_title=True,
                        ):
                            with dpg.plot(
                                label="Interferogram",
                                tag="chart1",
                                no_inputs=False,
                                no_menus=False,
                                no_box_select=False,
                            ):
                                dpg.add_plot_legend()
                                dpg.add_plot_axis(
                                    dpg.mvXAxis,
                                    label="samples (0 = trigger)",
                                    tag="live_x_axis",
                                )
                                dpg.add_plot_axis(
                                    dpg.mvYAxis,
                                    label="volts",
                                    tag="amp",
                                )
                                enabled = set(enabled_channels())
                                for ch in ALL_CHANNELS:
                                    dpg.add_line_series(
                                        PLACEHOLDER_X,
                                        PLACEHOLDER_Y,
                                        label=f"Channel {ch}",
                                        parent="amp",
                                        tag=series_tag(ch),
                                        show=ch in enabled,
                                    )

                            with dpg.plot(
                                label=_spectrum_plot_title(),
                                tag="spectrum_plot",
                                no_inputs=False,
                                no_menus=False,
                                no_box_select=False,
                                show=ifm.mode == Mode.AVERAGE,
                            ):
                                dpg.add_plot_legend()
                                dpg.add_plot_axis(
                                    dpg.mvXAxis,
                                    label=_spectrum_axis_label(),
                                    tag="spectrum_x_axis",
                                )
                                dpg.add_plot_axis(
                                    dpg.mvYAxis,
                                    label="|FFT|",
                                    tag="spectrum_y_axis",
                                )
                                dpg.add_line_series(
                                    PLACEHOLDER_X,
                                    PLACEHOLDER_Y,
                                    label="Spectrum",
                                    parent="spectrum_y_axis",
                                    tag="spectrum_series",
                                )

        apply_live_axis_limits()
        update_mode_dependent_widgets()
        dpg.bind_item_font("startstop_button", giant_font)

    # Per-channel series themes (colors).
    for ch, color in CHANNEL_COLORS.items():
        theme_tag = f"series{ch}_theme"
        with dpg.theme(tag=theme_tag):
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, color, category=dpg.mvThemeCat_Plots)
        if dpg.does_item_exist(series_tag(ch)):
            dpg.bind_item_theme(series_tag(ch), theme_tag)

    with dpg.theme(tag="start_button_theme"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 180, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (0, 210, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (0, 140, 0))

    with dpg.theme(tag="stop_button_theme"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (180, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (210, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (140, 0, 0))

    dpg.bind_item_theme("startstop_button", "start_button_theme")
    dpg.bind_item_font("chart1", small_font)
    if dpg.does_item_exist("spectrum_plot"):
        dpg.bind_item_font("spectrum_plot", small_font)
    # Match spectrum line weight/color to a clear single trace.
    with dpg.theme(tag="spectrum_series_theme"):
        with dpg.theme_component(dpg.mvLineSeries):
            dpg.add_theme_color(
                dpg.mvPlotCol_Line,
                (255, 200, 80, 255),
                category=dpg.mvThemeCat_Plots,
            )
    if dpg.does_item_exist("spectrum_series"):
        dpg.bind_item_theme("spectrum_series", "spectrum_series_theme")

    dpg.maximize_viewport()
    dpg.set_primary_window("main_window", True)

    # Let layout settle so sidebar wrap width matches the settings column.
    for _ in range(3):
        dpg.render_dearpygui_frame()
    refresh_settings_text_wraps()

    # Manual frame loop so Live View / averaging can capture + refresh.
    # Re-measure wrap occasionally in case the user drags the column divider.
    wrap_refresh_counter = 0
    while dpg.is_dearpygui_running():
        process_live_axis_limits()
        process_spectrum_axis_limits()
        if ifm.gathering:
            if ifm.mode == Mode.MONITOR:
                live_view_tick()
            elif ifm.mode == Mode.AVERAGE:
                average_tick()
        wrap_refresh_counter += 1
        if wrap_refresh_counter >= 30:
            wrap_refresh_counter = 0
            refresh_settings_text_wraps()
        dpg.render_dearpygui_frame()

    # Final snapshot so any last edits are retained even if a callback was missed.
    save_ui_settings_from_widgets()

    if ifm.live_engine is not None:
        try:
            ifm.live_engine.close()
        except Exception as e:
            print(f"Warning: error closing Live View engine: {e}")
        ifm.live_engine = None

    dpg.destroy_context()


if __name__ == "__main__":
    main()
