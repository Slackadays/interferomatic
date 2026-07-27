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
BULK_UNITS = ("MB", "GB", "seconds", "minutes")
VALID_MODES = ("MONITOR", "COLLECT", "AVERAGE")
# Allowed InputRange values (mV peak-to-peak), matching the UI dropdown.
VALID_INPUT_RANGES = (200, 400, 1000, 2000, 4000, 10000)


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


def load_ui_settings():
    """
    Load persisted UI field values from config.json.
    Returns a dict with keys: interferograms, threshold, save_file, mode,
    samplerate, input_range, bulk_limit, bulk_unit, channel1–channel4.
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
    }
