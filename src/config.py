"""Machine-local preferences persisted in config.json at the project root."""

import json
from pathlib import Path

# Project root is the parent of src/
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_INTERFEROGRAMS = 100000
DEFAULT_THRESHOLD = 0.5
DEFAULT_SAVE_FILE = ""


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


def load_ui_settings():
    """
    Load persisted UI field values from config.json.
    Returns a dict with keys: interferograms, threshold, save_file.
    """
    config = load_config()

    interferograms = config.get("interferograms", DEFAULT_INTERFEROGRAMS)
    # bool is a subclass of int; reject it. Accept whole-number floats from JSON.
    if isinstance(interferograms, bool):
        interferograms = DEFAULT_INTERFEROGRAMS
    elif isinstance(interferograms, int):
        pass
    elif isinstance(interferograms, float) and interferograms == int(interferograms):
        interferograms = int(interferograms)
    else:
        interferograms = DEFAULT_INTERFEROGRAMS
    if interferograms < 1:
        interferograms = DEFAULT_INTERFEROGRAMS

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

    return {
        "interferograms": interferograms,
        "threshold": threshold,
        "save_file": save_file,
    }
