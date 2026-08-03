"""Machine-local preferences persisted in config.json at the project root."""

import json
from pathlib import Path

# Project root is the parent of src/
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_INTERFEROGRAMS = 100000
DEFAULT_THRESHOLD = 0.5
DEFAULT_SAVE_FILE = ""
DEFAULT_MODE = "MONITOR"
DEFAULT_SAMPLERATE = 200000000
# Gage InputRange is millivolts peak-to-peak (e.g. 2000 = ±1 V).
DEFAULT_INPUT_RANGE = 2000
DEFAULT_BULK_LIMIT = 1
DEFAULT_BULK_UNIT = "GB"
DEFAULT_CHANNEL1 = True
DEFAULT_CHANNEL2 = False
DEFAULT_CHANNEL3 = False
DEFAULT_CHANNEL4 = False
# Live View window: samples before / after the trigger (≈20k total by default).
# Multiples of 32 match CSE1642 CAPS_DEPTH_INCREMENT so Commit accepts them.
DEFAULT_PRE_TRIGGER_SAMPLES = 5120
DEFAULT_POST_TRIGGER_SAMPLES = 15360
MIN_PRE_TRIGGER_SAMPLES = 0
MIN_POST_TRIGGER_SAMPLES = 32
MAX_LIVE_SAMPLES = 2_000_000
BULK_UNITS = ("MB", "GB", "seconds", "minutes")
VALID_MODES = ("MONITOR", "COLLECT", "AVERAGE")
# Allowed InputRange values (mV peak-to-peak), matching the UI dropdown.
VALID_INPUT_RANGES = (200, 400, 1000, 2000, 4000, 10000)
# Trigger UI defaults / allowed values (labels match main.py combo items).
DEFAULT_TRIGGER_SOURCE = "Channel 1"
DEFAULT_TRIGGER_EDGE = "Rising"
DEFAULT_TRIGGER_THRESHOLD = 0
DEFAULT_EXT_TRIGGER_COUPLING = "DC"
DEFAULT_EXT_TRIGGER_INPUT_RANGE = 2000  # mV pk-pk (±1 V)
DEFAULT_EXT_TRIGGER_IMPEDANCE = "High Z"
VALID_TRIGGER_SOURCES = ("Channel 1", "External")
VALID_TRIGGER_EDGES = ("Rising", "Falling")
VALID_EXT_TRIGGER_COUPLINGS = ("AC", "DC")
# Allowed external-trigger ExtRange values (mV peak-to-peak).
VALID_EXT_TRIGGER_INPUT_RANGES = (2000, 6600, 10000)
VALID_EXT_TRIGGER_IMPEDANCES = ("50 Ohms", "High Z")


def load_config():
    """Load config.json if present. Returns a dict (empty on missing/invalid)."""
    if not CONFIG_PATH.is_file():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError) as e:
        print(f"Warning: could not read {CONFIG_PATH}: {e}")
    return {}


def save_config(data, quiet=False):
    """Merge *data* into the existing config file and write it back."""
    config = load_config()
    config.update(data)
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        if not quiet:
            print(f"Saved config to {CONFIG_PATH}")
    except OSError as e:
        print(f"Warning: could not write {CONFIG_PATH}: {e}")


def _parse_positive_int(value, default):
    """Parse a positive integer from config; fall back to *default* if invalid."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value == int(value):
        parsed = int(value)
    else:
        return default
    if parsed < 1:
        return default
    return parsed


def _parse_nonneg_int(value, default, minimum=0, maximum=None):
    """Parse a non-negative integer clamped to [minimum, maximum]."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value == int(value):
        parsed = int(value)
    else:
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed


def load_ui_settings():
    """
    Load persisted UI field values from config.json.
    Returns a dict with keys: interferograms, threshold, save_file, mode,
    samplerate, input_range, bulk_limit, bulk_unit, channel1–channel4,
    pre/post trigger samples, and trigger source/edge/threshold/external opts.
    """
    config = load_config()

    interferograms = _parse_positive_int(
        config.get("interferograms", DEFAULT_INTERFEROGRAMS),
        DEFAULT_INTERFEROGRAMS,
    )

    threshold = config.get("threshold", DEFAULT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        threshold = DEFAULT_THRESHOLD
    else:
        threshold = float(threshold)
        if not (0.0 <= threshold <= 1.0):
            threshold = DEFAULT_THRESHOLD

    save_file = config.get("save_file", DEFAULT_SAVE_FILE)
    if not isinstance(save_file, str):
        save_file = DEFAULT_SAVE_FILE

    mode = config.get("mode", DEFAULT_MODE)
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE

    samplerate = _parse_positive_int(
        config.get("samplerate", DEFAULT_SAMPLERATE),
        DEFAULT_SAMPLERATE,
    )

    input_range = _parse_positive_int(
        config.get("input_range", DEFAULT_INPUT_RANGE),
        DEFAULT_INPUT_RANGE,
    )
    if input_range not in VALID_INPUT_RANGES:
        input_range = DEFAULT_INPUT_RANGE

    channel1 = config.get("channel1", DEFAULT_CHANNEL1)
    if not isinstance(channel1, bool):
        channel1 = DEFAULT_CHANNEL1

    channel2 = config.get("channel2", DEFAULT_CHANNEL2)
    if not isinstance(channel2, bool):
        channel2 = DEFAULT_CHANNEL2

    channel3 = config.get("channel3", DEFAULT_CHANNEL3)
    if not isinstance(channel3, bool):
        channel3 = DEFAULT_CHANNEL3

    channel4 = config.get("channel4", DEFAULT_CHANNEL4)
    if not isinstance(channel4, bool):
        channel4 = DEFAULT_CHANNEL4

    bulk_limit = _parse_positive_int(
        config.get("bulk_limit", DEFAULT_BULK_LIMIT),
        DEFAULT_BULK_LIMIT,
    )

    bulk_unit = config.get("bulk_unit", DEFAULT_BULK_UNIT)
    if bulk_unit not in BULK_UNITS:
        bulk_unit = DEFAULT_BULK_UNIT

    pre_trigger_samples = _parse_nonneg_int(
        config.get("pre_trigger_samples", DEFAULT_PRE_TRIGGER_SAMPLES),
        DEFAULT_PRE_TRIGGER_SAMPLES,
        minimum=MIN_PRE_TRIGGER_SAMPLES,
        maximum=MAX_LIVE_SAMPLES,
    )
    post_trigger_samples = _parse_nonneg_int(
        config.get("post_trigger_samples", DEFAULT_POST_TRIGGER_SAMPLES),
        DEFAULT_POST_TRIGGER_SAMPLES,
        minimum=MIN_POST_TRIGGER_SAMPLES,
        maximum=MAX_LIVE_SAMPLES,
    )
    if pre_trigger_samples + post_trigger_samples < 1:
        pre_trigger_samples = DEFAULT_PRE_TRIGGER_SAMPLES
        post_trigger_samples = DEFAULT_POST_TRIGGER_SAMPLES
    if pre_trigger_samples + post_trigger_samples > MAX_LIVE_SAMPLES:
        post_trigger_samples = max(
            MIN_POST_TRIGGER_SAMPLES,
            MAX_LIVE_SAMPLES - pre_trigger_samples,
        )

    trigger_source = config.get("trigger_source", DEFAULT_TRIGGER_SOURCE)
    if trigger_source not in VALID_TRIGGER_SOURCES:
        trigger_source = DEFAULT_TRIGGER_SOURCE

    trigger_edge = config.get("trigger_edge", DEFAULT_TRIGGER_EDGE)
    if trigger_edge not in VALID_TRIGGER_EDGES:
        trigger_edge = DEFAULT_TRIGGER_EDGE

    trigger_threshold = config.get("trigger_threshold", DEFAULT_TRIGGER_THRESHOLD)
    if isinstance(trigger_threshold, bool) or not isinstance(
        trigger_threshold, (int, float)
    ):
        trigger_threshold = DEFAULT_TRIGGER_THRESHOLD
    else:
        trigger_threshold = int(trigger_threshold)
        if not (0 <= trigger_threshold <= 100):
            trigger_threshold = DEFAULT_TRIGGER_THRESHOLD

    ext_trigger_coupling = config.get(
        "ext_trigger_coupling", DEFAULT_EXT_TRIGGER_COUPLING
    )
    if ext_trigger_coupling not in VALID_EXT_TRIGGER_COUPLINGS:
        ext_trigger_coupling = DEFAULT_EXT_TRIGGER_COUPLING

    ext_trigger_input_range = _parse_positive_int(
        config.get("ext_trigger_input_range", DEFAULT_EXT_TRIGGER_INPUT_RANGE),
        DEFAULT_EXT_TRIGGER_INPUT_RANGE,
    )
    if ext_trigger_input_range not in VALID_EXT_TRIGGER_INPUT_RANGES:
        ext_trigger_input_range = DEFAULT_EXT_TRIGGER_INPUT_RANGE

    ext_trigger_impedance = config.get(
        "ext_trigger_impedance", DEFAULT_EXT_TRIGGER_IMPEDANCE
    )
    if ext_trigger_impedance not in VALID_EXT_TRIGGER_IMPEDANCES:
        ext_trigger_impedance = DEFAULT_EXT_TRIGGER_IMPEDANCE

    return {
        "interferograms": interferograms,
        "threshold": threshold,
        "save_file": save_file,
        "mode": mode,
        "samplerate": samplerate,
        "input_range": input_range,
        "bulk_limit": bulk_limit,
        "bulk_unit": bulk_unit,
        "channel1": channel1,
        "channel2": channel2,
        "channel3": channel3,
        "channel4": channel4,
        "pre_trigger_samples": pre_trigger_samples,
        "post_trigger_samples": post_trigger_samples,
        "trigger_source": trigger_source,
        "trigger_edge": trigger_edge,
        "trigger_threshold": trigger_threshold,
        "ext_trigger_coupling": ext_trigger_coupling,
        "ext_trigger_input_range": ext_trigger_input_range,
        "ext_trigger_impedance": ext_trigger_impedance,
    }
