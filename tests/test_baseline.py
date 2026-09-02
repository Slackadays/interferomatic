"""Baseline mode records a blank; Average mode applies it."""

from __future__ import annotations

import numpy as np

import main
from src.spectrum import SPECTRUM_AXIS_WAVELENGTH


def test_mode_label_and_averaging_helper():
    assert (
        main.MODE_LABELS[main.Mode.BASELINE]
        == "Baseline for Average Interferograms"
    )
    assert main.LABEL_TO_MODE["Baseline for Average Interferograms"] is main.Mode.BASELINE
    original = main.ifm
    main.ifm = main.ifmstate()
    try:
        main.ifm.mode = main.Mode.AVERAGE
        assert main.is_averaging_mode()
        main.ifm.mode = main.Mode.BASELINE
        assert main.is_averaging_mode()
        main.ifm.mode = main.Mode.MONITOR
        assert not main.is_averaging_mode()
        assert main.is_averaging_mode(main.Mode.BASELINE)
    finally:
        main.ifm = original


def test_apply_baseline_xy_only_in_average_mode():
    original = main.ifm
    main.ifm = main.ifmstate()
    try:
        x = np.array([1060.0, 1064.0, 1070.0])
        y = np.array([10.0, 12.0, 9.0])
        main.ifm.baseline_x = x.copy()
        main.ifm.baseline_y = np.array([8.0, 8.5, 8.0])
        main.ifm.baseline_axis = SPECTRUM_AXIS_WAVELENGTH
        main.ifm.mode = main.Mode.BASELINE
        assert not main._baseline_is_applied()
        _, y_collect = main._apply_baseline_xy(x, y, SPECTRUM_AXIS_WAVELENGTH)
        np.testing.assert_allclose(y_collect, y)

        main.ifm.mode = main.Mode.AVERAGE
        assert main._baseline_is_applied()
        _, y_sub = main._apply_baseline_xy(x, y, SPECTRUM_AXIS_WAVELENGTH)
        np.testing.assert_allclose(y_sub, y - main.ifm.baseline_y)

        main.ifm.baseline_x = None
        main.ifm.baseline_y = None
        _, y_raw = main._apply_baseline_xy(x, y, SPECTRUM_AXIS_WAVELENGTH)
        np.testing.assert_allclose(y_raw, y)
    finally:
        main.ifm = original
