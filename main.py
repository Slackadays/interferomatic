import dearpygui.dearpygui as dpg
import playsound3
import sys
import time
from pathlib import Path
from enum import Enum

from src.config import save_config, load_ui_settings, BULK_UNITS
from src.scaling import resolve_font_scale, change_font_scale
from src.live_view import create_live_view_engine

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


class ifmstate:
    gathering = False
    has_gage = False
    save_file = ""
    mode = Mode.MONITOR
    samplerate = 200000000
    input_range = 2000  # mV peak-to-peak (±1 V)
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

try:
    import PyGage
    import GageSupport as gs  # noqa: F401
    import GageConstants as gc  # noqa: F401
    # gage_api/PyGage/ is a source tree and can import as an empty namespace
    # package; require the real extension entry points before enabling hardware.
    if not all(hasattr(PyGage, name) for name in ("Initialize", "GetSystem", "TransferData")):
        raise ImportError("PyGage extension is not built/installed")
    ifm.has_gage = True
except ImportError:
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


def live_view_tick():
    """Capture one Live View frame and update the plot (called from the UI loop)."""
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
        engine.configure(ifm.samplerate, channels, ifm.input_range)
        data = engine.capture(channels)
        apply_live_view_data(data)
        ifm.live_error = None
    except Exception as e:
        msg = str(e)
        print(f"Live View error: {msg}")
        ifm.live_error = msg
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
    print(f"Input range set to: {input_range_to_label(ifm.input_range)} ({ifm.input_range} mV pk-pk)")

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
        print(
            f"Starting Live View: rate={ifm.samplerate} S/s, "
            f"range={input_range_to_label(ifm.input_range)}, channels={channels}"
            + ("" if ifm.has_gage else " (simulated)")
        )
        try:
            ifm.live_engine.configure(ifm.samplerate, channels, ifm.input_range)
        except Exception as e:
            show_error_window(f"Failed to configure Live View: {e}")
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

def main():
    dpg.create_context()
    dpg.create_viewport(title="Interferomatic")

    with dpg.font_registry():
        default_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 80)
        giant_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 160)
        small_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 50)

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
    ifm.channel1 = ui["channel1"]
    ifm.channel2 = ui["channel2"]
    ifm.channel3 = ui["channel3"]
    ifm.channel4 = ui["channel4"]
    print(
        f"Loaded UI settings: mode={ui['mode']}, samplerate={ui['samplerate']}, "
        f"input_range={input_range_to_label(ui['input_range'])}, "
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

    with dpg.window(label="Main Window", tag="main_window"):
        dpg.add_button(label="Start", tag="startstop_button", callback=button1_callback)

        dpg.add_combo(
            items=list(MODE_LABELS.values()),
            label="Choose mode",
            tag="mode_combo",
            callback=mode_callback,
            default_value=MODE_LABELS[ifm.mode],
            width=600
        )

        with dpg.group(tag="average_controls", show=ifm.mode == Mode.AVERAGE):
            dpg.add_input_int(
                label="Interferograms to average",
                tag="interferograms_input",
                default_value=ui["interferograms"],
                width=600,
                callback=interferograms_callback,
            )
            with dpg.tooltip("interferograms_input"):
                dpg.add_text("Number of interferograms to collect and average before stopping")

        with dpg.group(tag="bulk_controls", show=ifm.mode == Mode.COLLECT):
            with dpg.group(horizontal=True):
                dpg.add_input_int(
                    label="Collect until",
                    tag="bulk_limit_input",
                    default_value=ui["bulk_limit"],
                    width=400,
                    callback=bulk_limit_callback,
                )
                dpg.add_combo(
                    items=list(BULK_UNITS),
                    tag="bulk_unit_combo",
                    default_value=ui["bulk_unit"],
                    width=200,
                    callback=bulk_unit_callback,
                )
            with dpg.tooltip("bulk_limit_input"):
                dpg.add_text("Stop bulk collection after this amount of data or time")
            with dpg.tooltip("bulk_unit_combo"):
                dpg.add_text("Unit for the collection limit: data size (MB, GB) or duration (seconds, minutes)")

        dpg.add_combo(
            tag="sample_rate_dropdown",
            label="Sample Rate",
            items=list(SAMPLERATE_LABELS.values()),
            default_value=samplerate_to_label(ifm.samplerate),
            width=600,
            callback=samplerate_callback,
        )

        dpg.add_checkbox(label="Channel 1", tag="channel1_checkbox", default_value=ifm.channel1, callback=channel_callback, user_data=1)
        dpg.add_checkbox(label="Channel 2", tag="channel2_checkbox", default_value=ifm.channel2, callback=channel_callback, user_data=2)
        dpg.add_checkbox(label="Channel 3", tag="channel3_checkbox", default_value=ifm.channel3, callback=channel_callback, user_data=3)
        dpg.add_checkbox(label="Channel 4", tag="channel4_checkbox", default_value=ifm.channel4, callback=channel_callback, user_data=4)

        dpg.add_combo(
            tag="input_range_dropdown",
            label="Input Range",
            items=INPUT_RANGE_COMBO_ITEMS,
            default_value=input_range_to_label(ifm.input_range),
            width=600,
            callback=input_range_callback,
        )
        with dpg.tooltip("input_range_dropdown"):
            dpg.add_text("Full-scale input range for all active channels (Gage InputRange, peak-to-peak)")

        dpg.add_slider_float(
            label="Cross correlational threshold",
            tag="threshold_slider",
            default_value=ui["threshold"],
            max_value=1,
            width=600,
            callback=threshold_callback,
        )
        with dpg.tooltip("threshold_slider"):
            dpg.add_text("Minimum threshold for cross-correlation to trigger interferogram averaging")

        dpg.add_file_dialog(label="Select save file", directory_selector=False, show=False, callback=save_file_callback, tag="file_dialog", modal=True, width=2000, height=1000)
        dpg.add_input_text(
            label="Save file",
            tag="save_file_input",
            default_value=ui["save_file"],
            width=1200,
            callback=save_file_text_callback,
        )
        dpg.add_button(
            label="Browse",
            tag="browse_button",
            callback=lambda: dpg.show_item("file_dialog"),
        )
        with dpg.tooltip("save_file_input"):
            dpg.add_text("File to save the averaged interferogram to")

        dpg.add_button(label="Fullscreen", tag="fullscreen_button", callback=lambda: dpg.toggle_viewport_fullscreen())

        dpg.add_button(label="Change Scale", tag="scale_button", callback=lambda: change_font_scale())

        dpg.add_button(label="Exit", tag="exit_button", callback=lambda: dpg.stop_dearpygui())

        with dpg.plot(label="Live View", tag="chart1", height=800, width=1600):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="samples", tag="live_x_axis")
            dpg.add_plot_axis(dpg.mvYAxis, label="volts", tag="amp")
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
