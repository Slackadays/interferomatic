#ifndef INTERFEROMATIC_GAGE_ACQ_H
#define INTERFEROMATIC_GAGE_ACQ_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Wait results (non-negative). Driver errors are returned as negative CsGetStatus
 * / CsDo / CsTransfer codes. */
#define GAGE_ACQ_READY    0
#define GAGE_ACQ_STOPPED  1
#define GAGE_ACQ_TIMEOUT  2

/* CsTestQt polls GetStatus every 10 ms. Faster polling races the Linux
 * HWEventHandler and can leave the SSM stuck off READY. */
#define GAGE_ACQ_POLL_MS  10

/* Calibration (relay clicking) is not a shot timeout. */
#define GAGE_ACQ_CALIB_TIMEOUT_MS  60000

int gage_acq_status(uint32_t handle);
int gage_acq_start(uint32_t handle);
int gage_acq_abort(uint32_t handle);
int gage_acq_force(uint32_t handle);

/* Wait until ACQ_STATUS_READY.
 *
 * timeout_ms < 0 waits forever (aside from calib_timeout). BUSY_CALIB pauses
 * the shot clock so a front-end calibration cannot look like a missed trigger.
 * stop_flag, if non-NULL, is polled each slice; a non-zero value returns
 * GAGE_ACQ_STOPPED without Abort (caller decides whether to abort).
 * out_status, if non-NULL, receives the last CsGetStatus value. */
int gage_acq_wait_ready(uint32_t handle,
                        int timeout_ms,
                        const volatile int *stop_flag,
                        int *out_status);

int gage_acq_transfer(uint32_t handle,
                      uint16_t channel,
                      uint32_t mode,
                      uint32_t segment,
                      int64_t start,
                      int64_t length,
                      void *buffer,
                      int64_t *actual_start,
                      int64_t *actual_length);

int gage_acq_error_string(int code, char *buf, int buf_len);

#ifdef __cplusplus
}
#endif

#endif
