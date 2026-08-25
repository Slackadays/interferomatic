"""Machine-local preferences persisted in config.json at the project root."""

import json
from pathlib import Path

# Project root is the parent of src/
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_INTERFEROGRAMS = 100000
DEFAULT_THRESHOLD = 0.5
DEFAULT_SAVE_FILE = ""
DEFAULT_SAVE_ENABLED = False
SAVE_FORMAT_BINARY = "Binary (float64)"
SAVE_FORMAT_CSV_NM = "CSV (nm, amplitude)"
SAVE_FORMAT_CSV_WN = "CSV (cm^-1, amplitude)"
SAVE_FORMAT_ITEMS = (SAVE_FORMAT_BINARY, SAVE_FORMAT_CSV_NM, SAVE_FORMAT_CSV_WN)
DEFAULT_SAVE_FORMAT = SAVE_FORMAT_CSV_NM
SAVE_WHEN_FINISH = "On finish"
SAVE_WHEN_AUTOSAVE = "Every 10 seconds"
SAVE_WHEN_ITEMS = (SAVE_WHEN_FINISH, SAVE_WHEN_AUTOSAVE)
DEFAULT_SAVE_WHEN = SAVE_WHEN_FINISH
AUTOSAVE_INTERVAL_S = 10.0
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
# Long enough for several dual-comb beat periods at 200 MS/s
# (N ≥ f_s / Δf_rep ≈ 4.36 MSa at 200 MS/s, 45.84 Hz).
MAX_LIVE_SAMPLES = 16_000_000
# Live View / average capture attempts per second (UI + Gage child throttle).
DEFAULT_MAX_CAPTURE_RATE_HZ = 60
MIN_MAX_CAPTURE_RATE_HZ = 1
MAX_MAX_CAPTURE_RATE_HZ = 1000
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

# Dual-comb spectrum axis calibration (see src/spectrum.py).
DEFAULT_DFREP_HZ = 45.84
DEFAULT_FRIO_MHZ = 281_720_536.100
DEFAULT_M1 = 3_505_709
DEFAULT_M2 = 3_505_711
DEFAULT_M3 = 1_752_855.0  # m/k for k = 2
DEFAULT_SPECTRUM_AXIS = "Wavelength (nm)"
VALID_SPECTRUM_AXES = ("Wavelength (nm)", "Wavenumber (cm^-1)")
DEFAULT_APODIZATION = "Boxcar"
VALID_APODIZATIONS = (
    "Boxcar",
    "Triangular",
    "Happ-Genzel",
    "Cosine",
    "Lorenz",
    "Gaussian",
)
# Older configs stored a unicode superscript minus the UI font cannot draw.
_LEGACY_SPECTRUM_AXIS_WAVENUMBER = "Wavenumber (cm⁻¹)"


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


def _parse_float(value, default, minimum=None, maximum=None):
    """Parse a float; fall back to *default* if invalid or out of range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    parsed = float(value)
    if minimum is not None and parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    if parsed != parsed:  # NaN
        return default
    return parsed


def default_ui_settings():
    """Return factory-default UI field values (same keys as load_ui_settings)."""
    return {
        "interferograms": DEFAULT_INTERFEROGRAMS,
        "threshold": float(DEFAULT_THRESHOLD),
        "save_file": DEFAULT_SAVE_FILE,
        "save_enabled": DEFAULT_SAVE_ENABLED,
        "save_format": DEFAULT_SAVE_FORMAT,
        "save_when": DEFAULT_SAVE_WHEN,
        "mode": DEFAULT_MODE,
        "samplerate": DEFAULT_SAMPLERATE,
        "input_range": DEFAULT_INPUT_RANGE,
        "bulk_limit": DEFAULT_BULK_LIMIT,
        "bulk_unit": DEFAULT_BULK_UNIT,
        "channel1": DEFAULT_CHANNEL1,
        "channel2": DEFAULT_CHANNEL2,
        "channel3": DEFAULT_CHANNEL3,
        "channel4": DEFAULT_CHANNEL4,
        "pre_trigger_samples": DEFAULT_PRE_TRIGGER_SAMPLES,
        "post_trigger_samples": DEFAULT_POST_TRIGGER_SAMPLES,
        "max_capture_rate_hz": DEFAULT_MAX_CAPTURE_RATE_HZ,
        "d_frep_hz": float(DEFAULT_DFREP_HZ),
        "frio_mhz": float(DEFAULT_FRIO_MHZ),
        "m1": float(DEFAULT_M1),
        "m2": float(DEFAULT_M2),
        "m3": float(DEFAULT_M3),
        "spectrum_axis": DEFAULT_SPECTRUM_AXIS,
        "apodization": DEFAULT_APODIZATION,
        "trigger_source": DEFAULT_TRIGGER_SOURCE,
        "trigger_edge": DEFAULT_TRIGGER_EDGE,
        "trigger_threshold": DEFAULT_TRIGGER_THRESHOLD,
        "ext_trigger_coupling": DEFAULT_EXT_TRIGGER_COUPLING,
        "ext_trigger_input_range": DEFAULT_EXT_TRIGGER_INPUT_RANGE,
        "ext_trigger_impedance": DEFAULT_EXT_TRIGGER_IMPEDANCE,
    }


def load_ui_settings():
    """
    Load persisted UI field values from config.json.
    Returns a dict with keys: interferograms, threshold, save_file,
    save_enabled, save_format, save_when, apodization, mode,
    samplerate, input_range, bulk_limit, bulk_unit, channel1–channel4,
    pre/post trigger samples, max_capture_rate_hz, and trigger options.
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

    save_enabled = config.get("save_enabled", DEFAULT_SAVE_ENABLED)
    if not isinstance(save_enabled, bool):
        save_enabled = DEFAULT_SAVE_ENABLED

    save_format = config.get("save_format", DEFAULT_SAVE_FORMAT)
    if save_format not in SAVE_FORMAT_ITEMS:
        save_format = DEFAULT_SAVE_FORMAT

    save_when = config.get("save_when", DEFAULT_SAVE_WHEN)
    if save_when not in SAVE_WHEN_ITEMS:
        save_when = DEFAULT_SAVE_WHEN

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

    max_capture_rate_hz = _parse_nonneg_int(
        config.get("max_capture_rate_hz", DEFAULT_MAX_CAPTURE_RATE_HZ),
        DEFAULT_MAX_CAPTURE_RATE_HZ,
        minimum=MIN_MAX_CAPTURE_RATE_HZ,
        maximum=MAX_MAX_CAPTURE_RATE_HZ,
    )

    d_frep_hz = _parse_float(
        config.get("d_frep_hz", DEFAULT_DFREP_HZ),
        DEFAULT_DFREP_HZ,
        minimum=0.0,
    )
    frio_mhz = _parse_float(
        config.get("frio_mhz", DEFAULT_FRIO_MHZ),
        DEFAULT_FRIO_MHZ,
        minimum=0.0,
    )
    m1 = _parse_float(config.get("m1", DEFAULT_M1), float(DEFAULT_M1))
    m2 = _parse_float(config.get("m2", DEFAULT_M2), float(DEFAULT_M2))
    m3 = _parse_float(config.get("m3", DEFAULT_M3), float(DEFAULT_M3))
    spectrum_axis = config.get("spectrum_axis", DEFAULT_SPECTRUM_AXIS)
    if (
        spectrum_axis == _LEGACY_SPECTRUM_AXIS_WAVENUMBER
        or (
            isinstance(spectrum_axis, str)
            and "wavenumber" in spectrum_axis.casefold()
        )
    ):
        spectrum_axis = "Wavenumber (cm^-1)"
    if spectrum_axis not in VALID_SPECTRUM_AXES:
        spectrum_axis = DEFAULT_SPECTRUM_AXIS
    apodization = config.get("apodization", DEFAULT_APODIZATION)
    if apodization not in VALID_APODIZATIONS:
        apodization = DEFAULT_APODIZATION

    return {
        "interferograms": interferograms,
        "threshold": threshold,
        "save_file": save_file,
        "save_enabled": save_enabled,
        "save_format": save_format,
        "save_when": save_when,
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
        "max_capture_rate_hz": max_capture_rate_hz,
        "d_frep_hz": d_frep_hz,
        "frio_mhz": frio_mhz,
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "spectrum_axis": spectrum_axis,
        "apodization": apodization,
        "trigger_source": trigger_source,
        "trigger_edge": trigger_edge,
        "trigger_threshold": trigger_threshold,
        "ext_trigger_coupling": ext_trigger_coupling,
        "ext_trigger_input_range": ext_trigger_input_range,
        "ext_trigger_impedance": ext_trigger_impedance,
    }
