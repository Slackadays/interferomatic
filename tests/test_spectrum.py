"""Optical-axis / FFT resolution tests for dual-comb spectra."""

from __future__ import annotations

import numpy as np

from src.spectrum import (
    APODIZATION_COSINE,
    APODIZATION_HAPP_GENZEL,
    APODIZATION_TRIANGULAR,
    SPECTRUM_AXIS_WAVELENGTH,
    SPECTRUM_AXIS_WAVENUMBER,
    aligned_fft_length,
    apodization_window,
    compute_rf_spectrum,
    compute_spectrum,
    dual_comb_period_samples,
    is_wavenumber_axis,
    map_rf_to_optical,
    normalize_apodization,
    normalize_spectrum_axis,
    optical_resolution_pm,
    rf_bin_hz,
    slice_spectrum_for_view,
    write_spectrum_file,
)


def test_apodization_windows_centered_on_zpd():
    n = 101
    zpd = 20
    box = apodization_window(n, "Boxcar", zpd_index=zpd)
    tri = apodization_window(n, "Triangular", zpd_index=zpd)
    hg = apodization_window(n, APODIZATION_HAPP_GENZEL, zpd_index=zpd)
    cos = apodization_window(n, APODIZATION_COSINE, zpd_index=zpd)
    lor = apodization_window(n, "Lorenz", zpd_index=zpd)
    gau = apodization_window(n, "Gaussian", zpd_index=zpd)
    assert np.allclose(box, 1.0)
    assert tri[zpd] == 1.0
    assert tri[-1] == 0.0
    assert hg[zpd] == 1.0
    np.testing.assert_allclose(hg[-1], 0.08, atol=1e-12)
    assert cos[zpd] == 1.0
    np.testing.assert_allclose(cos[-1], 0.0, atol=1e-12)
    assert lor[zpd] == 1.0
    assert gau[zpd] == 1.0
    assert lor[-1] < 0.2
    assert gau[-1] < 0.2
    assert normalize_apodization("lorentz") == "Lorenz"
    assert normalize_apodization("triangle") == APODIZATION_TRIANGULAR


def test_apodization_changes_fft_sidelobes():
    n = 4096
    t = np.arange(n, dtype=np.float64)
    y = np.cos(2.0 * np.pi * 10.3 * t / n)
    _, mag_box, _ = compute_rf_spectrum(y, 1e6, apodization="Boxcar")
    _, mag_tri, _ = compute_rf_spectrum(
        y, 1e6, apodization="Triangular", zpd_index=n // 2
    )
    assert mag_box.size == mag_tri.size
    assert not np.allclose(mag_box, mag_tri)
    assert abs(int(np.argmax(mag_box)) - int(np.argmax(mag_tri))) <= 1


def test_optical_resolution_scales_with_record_length():
    fs = 100_000_000.0
    d20k = optical_resolution_pm(fs, 20_000)
    d2m = optical_resolution_pm(fs, 2_000_000)
    assert d20k is not None and d2m is not None
    # 100× more samples → 100× finer Δλ. At 2 MSa this is well below 1 pm.
    assert d2m < 0.2
    assert abs(d20k / d2m - 100.0) < 1.0
    assert rf_bin_hz(fs, 2_000_000) == fs / 2_000_000


def test_full_fft_keeps_2m_bins():
    rng = np.random.default_rng(0)
    n = 200_000
    y = rng.standard_normal(n).astype(np.float32)
    frf, mag, n_out = compute_rf_spectrum(y, 100_000_000.0)
    assert n_out == n
    assert mag.size == n // 2 + 1
    assert frf.size == mag.size
    x, y_opt, x0 = map_rf_to_optical(frf, mag)
    assert x.size == y_opt.size
    # Almost every positive-F_opt bin is kept.
    assert x.size > 0.9 * mag.size
    assert x0 is not None


def test_view_slice_preserves_bins_when_zoomed():
    n = 100_000
    x = np.linspace(500.0, 600.0, n)
    mag = np.sin(np.linspace(0, 80, n)) ** 2
    # 1 nm window of a 100 nm span → ~1% of bins, under the plot cap.
    xs, ys = slice_spectrum_for_view(x, mag, 532.0, 533.0, max_points=32_768)
    expected = int(np.sum((x >= 532.0 - 0.02) & (x <= 533.0 + 0.02)))
    assert xs.size == expected
    assert xs.size > 800


def test_zoomed_out_envelope_keeps_peaks():
    n = 200_000
    x = np.linspace(500.0, 600.0, n)
    mag = np.zeros(n)
    mag[n // 2] = 12.0
    xs, ys = slice_spectrum_for_view(x, mag, 500.0, 600.0, max_points=4096)
    assert ys.size <= 4096
    assert float(ys.max()) == 12.0


def test_normalize_spectrum_axis_accepts_legacy_superscript():
    assert is_wavenumber_axis("Wavenumber (cm^-1)")
    assert is_wavenumber_axis("Wavenumber (cm⁻¹)")
    assert is_wavenumber_axis("wavenumber")
    assert not is_wavenumber_axis("Wavelength (nm)")
    assert normalize_spectrum_axis("Wavenumber (cm⁻¹)") == SPECTRUM_AXIS_WAVENUMBER
    assert normalize_spectrum_axis("Wavelength (nm)") == SPECTRUM_AXIS_WAVELENGTH
    assert normalize_spectrum_axis("nope") == SPECTRUM_AXIS_WAVELENGTH


def test_map_rf_to_optical_follows_axis_mode():
    frf = np.array([0.0, 1.0e6, 2.0e6])
    mag = np.ones(3)
    x_nm, _, _ = map_rf_to_optical(
        frf, mag, axis_mode=SPECTRUM_AXIS_WAVELENGTH
    )
    x_wn, _, _ = map_rf_to_optical(
        frf, mag, axis_mode=SPECTRUM_AXIS_WAVENUMBER
    )
    x_legacy, _, _ = map_rf_to_optical(
        frf, mag, axis_mode="Wavenumber (cm⁻¹)"
    )
    assert x_nm.size == x_wn.size >= 2
    # ν̃ [cm^-1] = 1e7 / λ [nm]. Each axis sorts independently, so compare sorted.
    np.testing.assert_allclose(
        np.sort(x_wn), np.sort(1.0e7 / x_nm), rtol=1e-9
    )
    np.testing.assert_allclose(x_wn, x_legacy, rtol=1e-12)
    assert float(np.max(np.abs(np.sort(x_nm) - np.sort(x_wn)))) > 1.0


def test_compute_spectrum_wrapper_matches_rf_map():
    y = np.zeros(4096, dtype=np.float32)
    y[2000:2100] = np.hanning(100)
    x1, m1, _ = compute_spectrum(y, 1e8, d_frep_hz=0.0)
    frf, mag, _ = compute_rf_spectrum(y, 1e8, d_frep_hz=0.0)
    x2, m2, _ = map_rf_to_optical(frf, mag)
    np.testing.assert_allclose(x1, x2)
    np.testing.assert_allclose(m1, m2)


def test_write_spectrum_csv_nm(tmp_path):
    path = write_spectrum_file(
        tmp_path / "spec",
        [500.0, 501.5],
        [1.25, 0.5],
        binary=False,
        x_column="nm",
    )
    assert path.suffix == ".csv"
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    assert lines[0] == "nm,amplitude"
    assert lines[1].startswith("500")
    assert ",1.25" in lines[1].replace(" ", "")


def test_write_spectrum_csv_wavenumber(tmp_path):
    path = write_spectrum_file(
        tmp_path / "spec.csv",
        [18779.0, 18780.0],
        [3.0, 4.0],
        binary=False,
        x_column="wavenumber",
    )
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "wavenumber,amplitude"


def test_write_spectrum_binary_pairs(tmp_path):
    x = np.array([532.0, 533.0], dtype=np.float64)
    y = np.array([10.0, 20.0], dtype=np.float64)
    path = write_spectrum_file(tmp_path / "spec.bin", x, y, binary=True)
    data = np.fromfile(path, dtype="<f8")
    assert data.size == 4
    np.testing.assert_allclose(data[0::2], x)
    np.testing.assert_allclose(data[1::2], y)


def test_rf_grid_lands_on_delta_frep():
    fs = 1_000_000.0
    d_frep = 100.0
    period = dual_comb_period_samples(fs, d_frep)
    assert period == 10_000
    assert aligned_fft_length(15_000, fs, d_frep) == 20_000

    n = 2 * period
    t = np.arange(n) / fs
    harmonics = (5, 12, 40)
    y = np.zeros(n, dtype=np.float64)
    for k in harmonics:
        y += np.cos(2.0 * np.pi * k * d_frep * t)

    frf, mag, n_out = compute_rf_spectrum(y, fs, d_frep_hz=d_frep)
    assert n_out == n
    # One sample per Δf_rep harmonic.
    np.testing.assert_allclose(np.diff(frf), d_frep, rtol=1e-9)
    for k in harmonics:
        idx = int(np.argmin(np.abs(frf - k * d_frep)))
        # The bin at k·Δf_rep is a clear local peak.
        assert mag[idx] > 2.0 * np.median(mag)
        assert abs(frf[idx] - k * d_frep) < 1e-6
