"""Capture-wait policy: calibration pauses must not look like stalls."""

from __future__ import annotations

from src.gage_shim import (
    ACQ_STATUS_BUSY_CALIB,
    ACQ_STATUS_READY,
    ACQ_STATUS_WAIT_TRIGGER,
    RESULT_READY,
    RESULT_STOPPED,
    RESULT_TIMEOUT,
    can_start_capture,
    wait_until_ready,
)
from src.live_view import (
    AVERAGE_TRIGGER_TIMEOUT,
    AVERAGE_WAIT_TIMEOUT_S,
    LIVE_TRIGGER_TIMEOUT,
    _mode_for_channels,
)


def test_wait_ready_through_calibration():
    """BUSY_CALIB must not consume the shot timeout (no Abort/Force/Commit)."""
    clock = {"t": 0.0}
    statuses = [ACQ_STATUS_BUSY_CALIB] * 8 + [ACQ_STATUS_READY]
    seen = []

    def get_status():
        i = min(len(seen), len(statuses) - 1)
        st = statuses[i]
        seen.append(st)
        return st

    def sleep(dt):
        clock["t"] += dt

    result = wait_until_ready(
        get_status,
        timeout_s=0.05,
        poll_s=0.01,
        sleep=sleep,
        now=lambda: clock["t"],
        pause_on_calib=True,
    )
    assert result == RESULT_READY
    assert ACQ_STATUS_BUSY_CALIB in seen
    # Shot timeout was 50 ms; calibration held us longer than that.
    assert clock["t"] >= 0.07


def test_wait_ready_timeout_while_waiting_for_trigger():
    clock = {"t": 0.0}

    def get_status():
        return ACQ_STATUS_WAIT_TRIGGER

    def sleep(dt):
        clock["t"] += dt

    result = wait_until_ready(
        get_status,
        timeout_s=0.05,
        poll_s=0.01,
        sleep=sleep,
        now=lambda: clock["t"],
        pause_on_calib=True,
    )
    assert result == RESULT_TIMEOUT
    assert clock["t"] >= 0.05


def test_wait_ready_stopped():
    def get_status():
        return ACQ_STATUS_WAIT_TRIGGER

    result = wait_until_ready(
        get_status,
        timeout_s=10.0,
        poll_s=0.01,
        stop_check=lambda: True,
        sleep=lambda _dt: None,
        now=lambda: 0.0,
    )
    assert result == RESULT_STOPPED


def test_slice_timeout_during_calib_when_not_paused():
    clock = {"t": 0.0}

    def get_status():
        return ACQ_STATUS_BUSY_CALIB

    def sleep(dt):
        clock["t"] += dt

    result = wait_until_ready(
        get_status,
        timeout_s=0.03,
        poll_s=0.01,
        sleep=sleep,
        now=lambda: clock["t"],
        pause_on_calib=False,
    )
    assert result == RESULT_TIMEOUT
    assert clock["t"] >= 0.03


def test_can_start_only_when_ready():
    assert can_start_capture(ACQ_STATUS_READY)
    assert not can_start_capture(ACQ_STATUS_WAIT_TRIGGER)
    assert not can_start_capture(ACQ_STATUS_BUSY_CALIB)


def test_mode_is_masked_channel_count():
    assert _mode_for_channels([1]) == 1
    assert _mode_for_channels([1, 2]) == 2
    assert _mode_for_channels([1, 2, 3, 4]) == 4


def test_average_trigger_timeout_is_finite():
    """Infinite WAIT_TRIGGER hangs the first hidden calibration on Linux."""
    assert AVERAGE_TRIGGER_TIMEOUT > LIVE_TRIGGER_TIMEOUT
    assert AVERAGE_TRIGGER_TIMEOUT == 20_000_000  # 2 s in 100 ns units
    assert AVERAGE_WAIT_TIMEOUT_S >= 2.0
