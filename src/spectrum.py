"""FFT of interferograms and dual-comb optical axis conversion.

RF frequencies ``Frf`` come from the real FFT of the time-domain trace
(using the acquisition sample rate). Optical frequency is then

    k = m_2 - m_1
    m = (m_1 + m_2) / 2
    F_opt = 2 * F_rio - (m / k) * F_rf     (k ≠ 0)

which matches the user setup:
  * k = 1 → F_opt = 2 F_rio − m F_rf
  * k = 2 → F_opt = 2 F_rio − (m/2) F_rf

``m_3`` is the explicit multiplier stored in the UI (defaults to m/k) so
``F_opt = 2 F_rio − m_3 F_rf``. Wavelength λ = c / F_opt; wavenumber is
1/λ in cm⁻¹.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

import numpy as np

# Speed of light in vacuum (SI). Note: 29_979_245_800 is c in cm/s.
SPEED_OF_LIGHT_M_S = 299_792_458.0

# Defaults for the current (k = 2) setup.
DEFAULT_DFREP_HZ = 45.84
DEFAULT_FRIO_MHZ = 281_720_536.100  # MHz
DEFAULT_M1 = 3_505_709
DEFAULT_M2 = 3_505_711
# m/k = 3_505_710 / 2
DEFAULT_M3 = 1_752_855.0

SPECTRUM_AXIS_WAVELENGTH = "Wavelength (nm)"
# ASCII caret: Science Gothic has no U+207B (superscript minus), so cm⁻¹
# renders as a missing-glyph "broken ^".
SPECTRUM_AXIS_WAVENUMBER = "Wavenumber (cm^-1)"
SPECTRUM_AXIS_ITEMS = (SPECTRUM_AXIS_WAVELENGTH, SPECTRUM_AXIS_WAVENUMBER)
DEFAULT_SPECTRUM_AXIS = SPECTRUM_AXIS_WAVELENGTH
WAVELENGTH_UNIT = "nm"
WAVENUMBER_UNIT = "cm^-1"

APODIZATION_BOXCAR = "Boxcar"
APODIZATION_TRIANGULAR = "Triangular"
APODIZATION_HAPP_GENZEL = "Happ-Genzel"
APODIZATION_COSINE = "Cosine"
APODIZATION_LORENZ = "Lorenz"
APODIZATION_GAUSSIAN = "Gaussian"
APODIZATION_ITEMS = (
    APODIZATION_BOXCAR,
    APODIZATION_TRIANGULAR,
    APODIZATION_HAPP_GENZEL,
    APODIZATION_COSINE,
    APODIZATION_LORENZ,
    APODIZATION_GAUSSIAN,
)
DEFAULT_APODIZATION = APODIZATION_BOXCAR


def is_wavenumber_axis(axis_mode: Optional[str]) -> bool:
    """True for the wavenumber combo item and older cm⁻¹ spellings."""
    if not isinstance(axis_mode, str):
        return False
    folded = axis_mode.casefold()
    if "wavenumber" in folded:
        return True
    compact = (
        axis_mode.replace("\u207b", "^-")
        .replace("⁻", "^-")
        .replace("¹", "1")
        .replace(" ", "")
        .casefold()
    )
    return "cm^-1" in compact or "1/cm" in compact


def normalize_spectrum_axis(axis_mode: Optional[str]) -> str:
    """Map any accepted axis string to a current combo item."""
    if is_wavenumber_axis(axis_mode):
        return SPECTRUM_AXIS_WAVENUMBER
    if isinstance(axis_mode, str) and "wavelength" in axis_mode.casefold():
        return SPECTRUM_AXIS_WAVELENGTH
    return DEFAULT_SPECTRUM_AXIS


def comb_m(m1: float, m2: float) -> float:
    return 0.5 * (float(m1) + float(m2))


def comb_k(m1: float, m2: float) -> float:
    return float(m2) - float(m1)


def default_m3_from_m1_m2(m1: float, m2: float) -> float:
    """Return m/k for the dual-comb coefficient (k ≠ 0)."""
    k = comb_k(m1, m2)
    if abs(k) < 1e-15:
        return DEFAULT_M3
    return comb_m(m1, m2) / k


def frio_mhz_to_hz(frio_mhz: float) -> float:
    return float(frio_mhz) * 1.0e6


def optical_frequency_hz(
    frf_hz: np.ndarray,
    frio_hz: float,
    m3: float,
) -> np.ndarray:
    """F_opt = 2 F_rio − m_3 F_rf (Hz)."""
    return 2.0 * float(frio_hz) - float(m3) * np.asarray(frf_hz, dtype=np.float64)


def wavelength_nm(fopt_hz: np.ndarray, c_m_s: float = SPEED_OF_LIGHT_M_S) -> np.ndarray:
    f = np.asarray(fopt_hz, dtype=np.float64)
    out = np.full_like(f, np.nan, dtype=np.float64)
    positive = f > 0.0
    out[positive] = (float(c_m_s) / f[positive]) * 1.0e9
    return out


def wavenumber_cm(fopt_hz: np.ndarray, c_m_s: float = SPEED_OF_LIGHT_M_S) -> np.ndarray:
    """Wavenumber in cm⁻¹: 1/λ with λ in cm."""
    f = np.asarray(fopt_hz, dtype=np.float64)
    out = np.full_like(f, np.nan, dtype=np.float64)
    positive = f > 0.0
    # ν̃ = f / c  (m⁻¹) → ×0.01 for cm⁻¹?  1/λ_m = f/c [m⁻¹]; cm⁻¹ = m⁻¹ / 100
    out[positive] = f[positive] / float(c_m_s) / 100.0
    return out


def optical_x_at_frf(
    frf_hz: float,
    *,
    frio_mhz: float = DEFAULT_FRIO_MHZ,
    m3: float = DEFAULT_M3,
    axis_mode: str = DEFAULT_SPECTRUM_AXIS,
    c_m_s: float = SPEED_OF_LIGHT_M_S,
) -> Optional[float]:
    """Map a single RF frequency to the spectrum X unit (nm or cm⁻¹)."""
    frio_hz = frio_mhz_to_hz(frio_mhz)
    fopt = float(optical_frequency_hz(np.array([frf_hz]), frio_hz, m3)[0])
    if not np.isfinite(fopt) or fopt <= 0.0:
        return None
    if is_wavenumber_axis(axis_mode):
        return float(wavenumber_cm(np.array([fopt]), c_m_s)[0])
    return float(wavelength_nm(np.array([fopt]), c_m_s)[0])


def rf_bin_hz(sample_rate_hz: float, n_samples: int) -> float:
    """FFT bin spacing ΔF_rf = f_s / N (Hz)."""
    if n_samples < 1 or sample_rate_hz <= 0:
        return 0.0
    return float(sample_rate_hz) / float(n_samples)


def optical_resolution_pm(
    sample_rate_hz: float,
    n_samples: int,
    *,
    frio_mhz: float = DEFAULT_FRIO_MHZ,
    m3: float = DEFAULT_M3,
    frf_hz: float = 0.0,
    c_m_s: float = SPEED_OF_LIGHT_M_S,
) -> Optional[float]:
    """Wavelength resolution of one FFT bin at *frf_hz*, in picometers.

    ΔF_opt = |m₃| · f_s / N, then Δλ = (c / F_opt²) · ΔF_opt.
    At ~2 MSa and 100 MS/s this is ~0.08 pm near Frf = 0.
    """
    df = rf_bin_hz(sample_rate_hz, n_samples)
    if df <= 0.0:
        return None
    frio_hz = frio_mhz_to_hz(frio_mhz)
    fopt = float(optical_frequency_hz(np.array([frf_hz]), frio_hz, m3)[0])
    if not np.isfinite(fopt) or fopt <= 0.0:
        return None
    d_fopt = abs(float(m3)) * df
    d_lambda_m = (float(c_m_s) / (fopt * fopt)) * d_fopt
    return d_lambda_m * 1.0e12


def optical_resolution_wavenumber(
    sample_rate_hz: float,
    n_samples: int,
    *,
    frio_mhz: float = DEFAULT_FRIO_MHZ,
    m3: float = DEFAULT_M3,
    frf_hz: float = 0.0,
    c_m_s: float = SPEED_OF_LIGHT_M_S,
) -> Optional[float]:
    """Wavenumber resolution of one FFT bin at *frf_hz*, in cm⁻¹."""
    df = rf_bin_hz(sample_rate_hz, n_samples)
    if df <= 0.0:
        return None
    frio_hz = frio_mhz_to_hz(frio_mhz)
    fopt = float(optical_frequency_hz(np.array([frf_hz]), frio_hz, m3)[0])
    if not np.isfinite(fopt) or fopt <= 0.0:
        return None
    d_fopt = abs(float(m3)) * df
    return d_fopt / float(c_m_s) / 100.0


def dual_comb_period_samples(sample_rate_hz: float, d_frep_hz: float) -> int:
    """Samples in one dual-comb beat period: ``round(f_s / Δf_rep)``."""
    if d_frep_hz <= 0.0 or sample_rate_hz <= 0.0:
        return 0
    return max(1, int(round(float(sample_rate_hz) / float(d_frep_hz))))


def aligned_fft_length(
    n_captured: int, sample_rate_hz: float, d_frep_hz: float
) -> int:
    """FFT length that puts a bin on every integer multiple of Δf_rep.

    ``N = M · round(f_s / Δf_rep)`` with ``M = ceil(n_captured / period)``.
    Zero-padding up to *N* interpolates; it cannot recover Δf_rep structure
    if the captured record is shorter than one beat period.
    """
    period = dual_comb_period_samples(sample_rate_hz, d_frep_hz)
    n = max(0, int(n_captured))
    if period <= 0:
        return n
    n_periods = max(1, int(np.ceil(n / float(period))))
    return n_periods * period


def normalize_apodization(kind: Optional[str]) -> str:
    """Map a UI / config string onto a known apodization name."""
    if not isinstance(kind, str):
        return DEFAULT_APODIZATION
    folded = kind.strip().casefold().replace(" ", "").replace("_", "-")
    aliases = {
        "boxcar": APODIZATION_BOXCAR,
        "rectangular": APODIZATION_BOXCAR,
        "none": APODIZATION_BOXCAR,
        "triangular": APODIZATION_TRIANGULAR,
        "triangle": APODIZATION_TRIANGULAR,
        "happ-genzel": APODIZATION_HAPP_GENZEL,
        "happgenzel": APODIZATION_HAPP_GENZEL,
        "cosine": APODIZATION_COSINE,
        "cos": APODIZATION_COSINE,
        "lorenz": APODIZATION_LORENZ,
        "lorentz": APODIZATION_LORENZ,
        "lorentzian": APODIZATION_LORENZ,
        "gaussian": APODIZATION_GAUSSIAN,
        "gauss": APODIZATION_GAUSSIAN,
    }
    return aliases.get(folded, DEFAULT_APODIZATION)


def apodization_window(
    n: int,
    kind: str = DEFAULT_APODIZATION,
    *,
    zpd_index: Optional[int] = None,
) -> np.ndarray:
    """FTIR apodization weights for an *n*-sample interferogram.

    *zpd_index* is the zero-path-difference / centerburst sample (the trigger
    at index 0 of the capture window). ξ = |i − zpd| / L with L the distance
    to the farther endpoint, so the window is 1 at ZPD and tapers toward the
    record edges. Formulas follow the usual FTIR set (boxcar, triangle,
    Happ–Genzel, cosine, Lorentz, Gaussian).
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    name = normalize_apodization(kind)
    if name == APODIZATION_BOXCAR:
        return np.ones(n, dtype=np.float64)

    center = n // 2 if zpd_index is None else int(zpd_index)
    center = max(0, min(n - 1, center))
    half = max(float(center), float(n - 1 - center), 1.0)
    xi = np.abs(np.arange(n, dtype=np.float64) - float(center)) / half
    np.minimum(xi, 1.0, out=xi)

    if name == APODIZATION_TRIANGULAR:
        return 1.0 - xi
    if name == APODIZATION_HAPP_GENZEL:
        return 0.54 + 0.46 * np.cos(np.pi * xi)
    if name == APODIZATION_COSINE:
        return np.cos(0.5 * np.pi * xi)
    if name == APODIZATION_LORENZ:
        return np.exp(-2.0 * xi)
    return np.exp(-2.0 * xi * xi)


def compute_rf_spectrum(
    y: Sequence[float],
    sample_rate_hz: float,
    *,
    d_frep_hz: float = 0.0,
    apodization: str = DEFAULT_APODIZATION,
    zpd_index: Optional[int] = None,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """Return ``(frf_hz, magnitude, n_time)`` from a mean-removed real FFT.

    The transform is always the captured length (no zero-pad to 2–4 MSa).
    When *d_frep_hz* > 0 the magnitude is interpolated onto ``n · Δf_rep``
    so the RF axis matches the dual-comb repetition grid.
    *apodization* tapers the interferogram about *zpd_index* before the FFT.
    """
    y_arr = np.ascontiguousarray(np.asarray(y, dtype=np.float32).ravel())
    n = int(y_arr.size)
    if n < 4 or sample_rate_hz <= 0:
        return (
            np.zeros(0, dtype=np.float64),
            np.zeros(0, dtype=np.float32),
            n,
        )
    y_zm = y_arr - y_arr.mean()
    window = apodization_window(n, apodization, zpd_index=zpd_index)
    if window.size == n and normalize_apodization(apodization) != APODIZATION_BOXCAR:
        y_zm = y_zm * window.astype(np.float32, copy=False)
    mag = np.abs(np.fft.rfft(y_zm))
    frf = np.fft.rfftfreq(n, d=1.0 / float(sample_rate_hz))

    if d_frep_hz > 0.0:
        n_grid = int(np.floor((float(sample_rate_hz) * 0.5) / float(d_frep_hz))) + 1
        if n_grid >= 2:
            grid = np.arange(n_grid, dtype=np.float64) * float(d_frep_hz)
            mag = np.interp(grid, frf, mag).astype(np.float32, copy=False)
            frf = grid

    return (
        np.ascontiguousarray(frf, dtype=np.float64),
        np.ascontiguousarray(mag, dtype=np.float32),
        n,
    )


def map_rf_to_optical(
    frf_hz: np.ndarray,
    mag: np.ndarray,
    *,
    frio_mhz: float = DEFAULT_FRIO_MHZ,
    m3: float = DEFAULT_M3,
    axis_mode: str = DEFAULT_SPECTRUM_AXIS,
    c_m_s: float = SPEED_OF_LIGHT_M_S,
) -> Tuple[np.ndarray, np.ndarray, Optional[float]]:
    """Map an RF magnitude spectrum to wavelength (nm) or wavenumber (cm⁻¹)."""
    x_frf0 = optical_x_at_frf(
        0.0, frio_mhz=frio_mhz, m3=m3, axis_mode=axis_mode, c_m_s=c_m_s
    )
    empty_x = np.asarray([0.0, 1.0], dtype=np.float64)
    empty_y = np.asarray([0.0, 0.0], dtype=np.float64)
    frf = np.asarray(frf_hz, dtype=np.float64).ravel()
    mag_arr = np.asarray(mag).ravel()
    n = min(frf.size, mag_arr.size)
    if n < 2:
        return empty_x, empty_y, x_frf0
    frf = frf[:n]
    mag_arr = mag_arr[:n]

    frio_hz = frio_mhz_to_hz(frio_mhz)
    fopt = optical_frequency_hz(frf, frio_hz, m3)
    valid = np.isfinite(fopt) & (fopt > 0.0)
    if not np.any(valid):
        return empty_x, empty_y, x_frf0

    if is_wavenumber_axis(axis_mode):
        x = wavenumber_cm(fopt[valid], c_m_s)
    else:
        x = wavelength_nm(fopt[valid], c_m_s)

    y_out = np.asarray(mag_arr[valid], dtype=np.float64)
    order = np.argsort(x, kind="mergesort")
    x = x[order]
    y_out = y_out[order]
    ok = np.isfinite(x) & np.isfinite(y_out)
    return (
        np.ascontiguousarray(x[ok], dtype=np.float64),
        np.ascontiguousarray(y_out[ok], dtype=np.float64),
        x_frf0,
    )


def slice_spectrum_for_view(
    x: np.ndarray,
    mag: np.ndarray,
    x_lo: float,
    x_hi: float,
    *,
    max_points: int = 32768,
) -> Tuple[np.ndarray, np.ndarray]:
    """Full-resolution slice of *x*/*mag* in ``[x_lo, x_hi]``.

    When the window still has more than *max_points* bins (zoomed out), a
    min/max envelope is used so peaks survive. When the user zooms in far
    enough that the slice fits, every FFT bin is plotted.
    """
    from src.averaging import slice_xy_for_view

    return slice_xy_for_view(x, mag, x_lo, x_hi, max_points=max_points)


def spectrum_view_limits(
    x: Sequence[float],
    mag: Sequence[float],
    *,
    x_frf0: Optional[float],
    half_span_fraction: float = 0.12,
) -> Tuple[float, float]:
    """Return (x_min, x_max) centered on Frf = 0.

    ``x_frf0`` is the optical X value (nm or cm⁻¹) at Frf = 0. The window
    half-width is a fraction of the full spectrum span so the view is zoomed
    without depending on peak finding.
    """
    del mag  # unused; center is strictly Frf = 0
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    if x_arr.size < 2:
        xc = float(x_frf0) if x_frf0 is not None and np.isfinite(x_frf0) else 0.0
        return xc - 1.0, xc + 1.0

    full_lo = float(np.nanmin(x_arr))
    full_hi = float(np.nanmax(x_arr))
    full_span = max(full_hi - full_lo, 1e-12)

    if x_frf0 is not None and np.isfinite(x_frf0):
        x_center = float(x_frf0)
    else:
        x_center = 0.5 * (full_lo + full_hi)

    half = max(half_span_fraction * full_span, 1e-9)
    return x_center - half, x_center + half


def compute_spectrum(
    y: Sequence[float],
    sample_rate_hz: float,
    *,
    frio_mhz: float = DEFAULT_FRIO_MHZ,
    m3: float = DEFAULT_M3,
    d_frep_hz: float = DEFAULT_DFREP_HZ,
    axis_mode: str = DEFAULT_SPECTRUM_AXIS,
    apodization: str = DEFAULT_APODIZATION,
    zpd_index: Optional[int] = None,
    c_m_s: float = SPEED_OF_LIGHT_M_S,
) -> Tuple[np.ndarray, np.ndarray, Optional[float]]:
    """Return (x_axis, magnitude, x_at_frf0) for the spectrum plot.

    * x_axis is wavelength (nm) or wavenumber (cm⁻¹) per *axis_mode*.
    * Uses a mean-removed real FFT. Only bins with F_opt > 0 are kept.
    * ``x_at_frf0`` is the optical X coordinate for Frf = 0 (view anchor).
    * ``d_frep_hz`` aligns the FFT onto the dual-comb repetition grid
      (``n · Δf_rep``).
    """
    frf, mag, _n = compute_rf_spectrum(
        y,
        sample_rate_hz,
        d_frep_hz=d_frep_hz,
        apodization=apodization,
        zpd_index=zpd_index,
    )
    return map_rf_to_optical(
        frf,
        mag,
        frio_mhz=frio_mhz,
        m3=m3,
        axis_mode=axis_mode,
        c_m_s=c_m_s,
    )


def write_spectrum_file(
    path: Union[str, Path],
    x: Sequence[float],
    y: Sequence[float],
    *,
    binary: bool,
    x_column: str = "nm",
) -> Path:
    """Write the optical spectrum to *path*.

    * CSV: ``x_column,amplitude`` rows (``nm`` or ``wavenumber``).
    * Binary: little-endian float64 pairs ``(x, amplitude)`` with no header.
    """
    out = Path(path)
    if not str(out):
        raise ValueError("save path is empty")
    x_arr = np.ascontiguousarray(np.asarray(x, dtype=np.float64).ravel())
    y_arr = np.ascontiguousarray(np.asarray(y, dtype=np.float64).ravel())
    n = min(x_arr.size, y_arr.size)
    if n < 1:
        raise ValueError("no spectrum data to save")
    x_arr = x_arr[:n]
    y_arr = y_arr[:n]

    if out.suffix == "":
        out = out.with_suffix(".bin" if binary else ".csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    tmp = out.with_name(out.name + ".tmp")
    try:
        if binary:
            interleaved = np.empty(n * 2, dtype="<f8")
            interleaved[0::2] = x_arr
            interleaved[1::2] = y_arr
            interleaved.tofile(tmp)
        else:
            header = "wavenumber,amplitude" if x_column == "wavenumber" else "nm,amplitude"
            np.savetxt(
                tmp,
                np.column_stack((x_arr, y_arr)),
                delimiter=",",
                header=header,
                comments="",
                fmt="%.10g",
            )
        tmp.replace(out)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except TypeError:
            if tmp.is_file():
                tmp.unlink()
        raise
    return out
