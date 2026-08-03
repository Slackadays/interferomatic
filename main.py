import dearpygui.dearpygui as dpg
import playsound3
import sys
import time
from pathlib import Path
from enum import Enum

from src.config import (
    save_config,
    load_ui_settings,
    BULK_UNITS,
    DEFAULT_PRE_TRIGGER_SAMPLES,
    DEFAULT_POST_TRIGGER_SAMPLES,
    DEFAULT_TRIGGER_SOURCE,
    DEFAULT_TRIGGER_EDGE,
    DEFAULT_TRIGGER_THRESHOLD,
    DEFAULT_EXT_TRIGGER_COUPLING,
    DEFAULT_EXT_TRIGGER_INPUT_RANGE,
    DEFAULT_EXT_TRIGGER_IMPEDANCE,
    MIN_PRE_TRIGGER_SAMPLES,
    MIN_POST_TRIGGER_SAMPLES,
    MAX_LIVE_SAMPLES,
    VALID_TRIGGER_SOURCES,
    VALID_TRIGGER_EDGES,
    VALID_EXT_TRIGGER_COUPLINGS,
    VALID_EXT_TRIGGER_IMPEDANCES,
)
from src.scaling import resolve_font_scale, change_font_scale
from src.live_view import (
    create_live_view_engine,
    normalize_live_window,
    LIVE_DEPTH_INCREMENT,
)

GAGE_API_DIR = Path(__file__).resolve().parent / "gage_api"
if str(GAGE_API_DIR) not in sys.path:
    sys.path.insert(0, str(GAGE_API_DIR))

# Placeholder empty series so the plot axes exist before the first capture.
PLACEHOLDER_X = [0.0, 1.0]
PLACEHOLDER_Y = [0.0, 0.0]

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
    mode = Mode.MONITOR
    samplerate = 200000000
    input_range = 2000  # mV peak-to-peak (±1 V)
    pre_trigger_samples = DEFAULT_PRE_TRIGGER_SAMPLES
    post_trigger_samples = DEFAULT_POST_TRIGGER_SAMPLES
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

ifm = ifmstate()

# Minimum seconds between Live View captures (keeps UI responsive).
LIVE_VIEW_MIN_INTERVAL_S = 0.03

# Detect the real PyGage extension without importing it into this process.
# Loading PyGage here installs Linux signal handlers that race with Dear PyGui's
# OpenGL threads and corrupt continuous capture (SIGSEGV / stuck READY).
from src.live_view import gage_extension_available

if gage_extension_available():
    ifm.has_gage = True
else:
    print("Running in Gage-less mode. PyGage module not found.")


# Acquisition settings locked while gathering. Start/Stop, Fullscreen,
# Change Scale, Exit, and the live plot stay usable.
SETTINGS_WIDGETS = (
    "mode_combo",
    "sample_rate_dropdown",
    "interferograms_input",
    "bulk_limit_input",
    "bulk_unit_combo",
    "threshold_slider",
    "save_file_input",
    "browse_button",
    "channel1_checkbox",
    "channel2_checkbox",
    "channel3_checkbox",
    "channel4_checkbox",
    "input_range_dropdown",
    "pre_trigger_input",
    "post_trigger_input",
    "trigger_source_dropdown",
    "trigger_edge_dropdown",
    "trigger_threshold_input",
    "external_trigger_coupling_dropdown",
    "external_trigger_input_range_dropdown",
    "external_trigger_impedance_dropdown",
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
    """Show only the limit controls that apply to the current mode."""
    if not dpg.does_item_exist("average_controls"):
        return
    show_average = ifm.mode == Mode.AVERAGE
    show_bulk = ifm.mode == Mode.COLLECT
    dpg.configure_item("average_controls", show=show_average)
    dpg.configure_item("bulk_controls", show=show_bulk)


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


def apply_live_axis_limits():
    """Lock Live View axes: full bipolar range vertically, pre/post window horizontally."""
    half_v = input_range_half_scale_volts(ifm.input_range)
    pre, post = normalize_live_window(
        ifm.pre_trigger_samples, ifm.post_trigger_samples
    )
    if dpg.does_item_exist("amp"):
        dpg.set_axis_limits("amp", -half_v, half_v)
    if dpg.does_item_exist("live_x_axis"):
        # Trigger at sample 0; show [-pre, post).
        x_max = max(post - 1, 0) if post > 0 else 0
        dpg.set_axis_limits("live_x_axis", float(-pre), float(x_max))


def apply_live_view_data(channel_data: dict):
    """Push captured channel data into the Live View plot series."""
    enabled = set(enabled_channels())
    for ch in ALL_CHANNELS:
        tag = series_tag(ch)
        if not dpg.does_item_exist(tag):
            continue
        if ch in channel_data and ch in enabled:
            x, y = channel_data[ch]
            dpg.set_value(tag, [x, y])
            dpg.configure_item(tag, show=True)
        else:
            dpg.configure_item(tag, show=ch in enabled)
            if ch not in enabled:
                dpg.set_value(tag, [PLACEHOLDER_X, PLACEHOLDER_Y])
    # Re-apply after data updates so auto-fit does not zoom to the noise floor.
    apply_live_axis_limits()


def live_view_tick():
    """Pull the latest Live View frame from the capture worker and update the plot."""
    engine = ifm.live_engine
    if engine is None or not ifm.gathering or ifm.mode != Mode.MONITOR:
        return

    now = time.monotonic()
    if now - ifm.live_last_tick < LIVE_VIEW_MIN_INTERVAL_S:
        return
    ifm.live_last_tick = now

    channels = enabled_channels()
    if not channels:
        return

    try:
        data = engine.capture(channels)
        # None: worker has not produced a new frame yet.
        if data is None:
            return
        apply_live_view_data(data)
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
    if dpg.does_item_exist("save_file_input"):
        data["save_file"] = dpg.get_value("save_file_input") or ""
        ifm.save_file = data["save_file"]
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
        # Mode changes are locked while running; still defend against stray events.
        set_gathering_ui(False)

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


def _clamp_live_sample_inputs():
    """Keep pre/post fields valid and within MAX_LIVE_SAMPLES total."""
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
    ifm.pre_trigger_samples = pre
    ifm.post_trigger_samples = post
    if dpg.does_item_exist("pre_trigger_input"):
        dpg.set_value("pre_trigger_input", pre)
    if dpg.does_item_exist("post_trigger_input"):
        dpg.set_value("post_trigger_input", post)
    return pre, post


def pre_trigger_callback(sender, app_data):
    pre, post = _clamp_live_sample_inputs()
    save_config(
        {"pre_trigger_samples": pre, "post_trigger_samples": post},
        quiet=True,
    )
    apply_live_axis_limits()
    print(f"Live View window: {pre} pre + {post} post = {pre + post} samples")


def post_trigger_callback(sender, app_data):
    pre, post = _clamp_live_sample_inputs()
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
        if ifm.live_engine is not None:
            try:
                ifm.live_engine.stop()
            except Exception:
                pass
        set_gathering_ui(False)
        return

    channels = enabled_channels()
    if not channels:
        show_error_window("Enable at least one channel before starting.")
        return

    if ifm.mode == Mode.MONITOR:
        if ifm.live_engine is None:
            show_error_window("Live View engine is not available.")
            return
        pre, post = _clamp_live_sample_inputs()
        trigger = {
            "source": ifm.trigger_source,
            "edge": ifm.trigger_edge,
            "level": ifm.trigger_threshold,
            "ext_coupling": ifm.ext_trigger_coupling,
            "ext_range_mv": ifm.ext_trigger_input_range,
            "ext_impedance": ifm.ext_trigger_impedance,
        }
        print(
            f"Starting Live View: rate={ifm.samplerate} S/s, "
            f"range={input_range_to_label(ifm.input_range)}, "
            f"window={pre}+{post} samples, channels={channels}, "
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
            ifm.live_engine.configure(
                ifm.samplerate,
                channels,
                ifm.input_range,
                pre_trigger_samples=pre,
                post_trigger_samples=post,
                trigger=trigger,
            )
            ifm.live_engine.start(channels)
            apply_live_axis_limits()
        except Exception as e:
            try:
                ifm.live_engine.stop()
            except Exception:
                pass
            show_error_window(f"Failed to start Live View: {e}")
            return
        set_gathering_ui(True)
        return

    if ifm.mode == Mode.COLLECT:
        show_error_window("Collect bulk data mode is not implemented yet.")
        return

    if ifm.mode == Mode.AVERAGE:
        show_error_window("Average interferograms mode is not implemented yet.")
        return

def interferograms_callback(sender, app_data):
    try:
        value = int(app_data)
    except (TypeError, ValueError):
        return
    save_config({"interferograms": value}, quiet=True)

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
    save_config({"threshold": value}, quiet=True)

def save_file_text_callback(sender, app_data):
    ifm.save_file = app_data if isinstance(app_data, str) else ""
    save_config({"save_file": ifm.save_file}, quiet=True)

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
    dpg.create_viewport(title="Interferomatic")

    with dpg.font_registry():
        default_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 80)
        giant_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 240)
        small_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 65)

    dpg.bind_font(default_font)

    dpg.setup_dearpygui()

    dpg.show_style_editor()

    dpg.show_viewport()

    font_scale = resolve_font_scale()
    dpg.set_global_font_scale(font_scale)
    print(f"Using font scale: {font_scale:.2f}")

    ui = load_ui_settings()
    ifm.save_file = ui["save_file"]
    ifm.mode = Mode[ui["mode"]]
    ifm.samplerate = ui["samplerate"]
    ifm.input_range = ui["input_range"]
    ifm.pre_trigger_samples = ui["pre_trigger_samples"]
    ifm.post_trigger_samples = ui["post_trigger_samples"]
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
    print(
        f"Loaded UI settings: mode={ui['mode']}, samplerate={ui['samplerate']}, "
        f"input_range={input_range_to_label(ui['input_range'])}, "
        f"live_window={ui['pre_trigger_samples']}+{ui['post_trigger_samples']}, "
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
                    ):
                        dpg.add_spacer(height=20)
                        dpg.add_separator()

                        dpg.add_button(
                            label="Start",
                            tag="startstop_button",
                            callback=button1_callback,
                        )

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
                                    "Number of interferograms to collect and "
                                    "average before stopping"
                                )
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
                            label="Data processing", default_open=True
                        ):

                            add_settings_label(
                                "Cross correlational threshold", small_font
                            )
                            dpg.add_slider_float(
                                tag="threshold_slider",
                                default_value=ui["threshold"],
                                max_value=1,
                                width=SETTINGS_WIDTH,
                                callback=threshold_callback,
                            )
                            with dpg.tooltip("threshold_slider"):
                                dpg.add_text(
                                    "Minimum threshold for cross-correlation to "
                                    "trigger interferogram averaging"
                                )

                        dpg.add_separator()
                        dpg.add_spacer(height=20)

                        with dpg.collapsing_header(
                            label="Interferomatic settings",
                            default_open=True,
                        ):
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
                                    "File to save the averaged interferogram to"
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
                        with dpg.plot(
                            label="Live View",
                            tag="chart1",
                            height=-1,
                            width=-1,
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

        apply_live_axis_limits()
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

    dpg.maximize_viewport()
    dpg.set_primary_window("main_window", True)

    # Manual frame loop so Live View can capture + refresh between frames.
    while dpg.is_dearpygui_running():
        if ifm.gathering and ifm.mode == Mode.MONITOR:
            live_view_tick()
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
