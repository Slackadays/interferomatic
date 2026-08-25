/* Gage CompuScope acquisition helpers.
 *
 * Mirrors CsTestQt's Linux path (GageSystem::WaitForReady / WaitForTrigger):
 * poll CsGetStatus at ~10 ms, wait through ACQ_STATUS_BUSY_CALIB, and never
 * Abort or Force just because a trigger is slow. Abort/Commit click the
 * Razor analog-front-end relays; those belong to config changes and halt,
 * not to ordinary pauses.
 *
 * Event FDs from CsGetEventHandle (pipes on Linux) are polled when available,
 * the same way Sdk/Advanced/GageEvents does. GetStatus remains the source of
 * truth so calibration is visible. */

#include "gage_acq.h"

#include <errno.h>
#include <poll.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "CsPrototypes.h"

#ifndef TRUE
#define TRUE 1
#endif
#ifndef FALSE
#define FALSE 0
#endif

#define ACQ_READY       0
#define ACQ_WAIT_TRIG   1
#define ACQ_TRIGGERED   2
#define ACQ_BUSY_TX     3
#define ACQ_BUSY_CALIB  4

#define ACQ_EVENT_TRIGGERED  0
#define ACQ_EVENT_END_BUSY   1

typedef struct {
    uint32_t handle;
    int fd_trig;
    int fd_end;
    int bound;
} GageAcqEvents;

static GageAcqEvents g_events = {0, -1, -1, 0};

static int64_t monotonic_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void sleep_ms(int ms)
{
    struct timespec ts;
    if (ms < 1) {
        ms = 1;
    }
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {
        /* retry remaining */
    }
}

static void drain_fd(int fd)
{
    char sink;
    if (fd < 0) {
        return;
    }
    ssize_t n = read(fd, &sink, 1);
    (void)n;
}

static void bind_events(uint32_t handle)
{
    int32_t sts;
    int fd;

    if (g_events.bound && g_events.handle == handle) {
        return;
    }
    g_events.handle = handle;
    g_events.fd_trig = -1;
    g_events.fd_end = -1;
    g_events.bound = 1;

    fd = -1;
    sts = CsGetEventHandle(handle, ACQ_EVENT_TRIGGERED, &fd);
    if (sts >= 0 && fd >= 0) {
        g_events.fd_trig = fd;
    }
    fd = -1;
    sts = CsGetEventHandle(handle, ACQ_EVENT_END_BUSY, &fd);
    if (sts >= 0 && fd >= 0) {
        g_events.fd_end = fd;
    }
}

static void poll_events(int timeout_ms)
{
    struct pollfd pfds[2];
    int nfds = 0;
    int i;

    if (g_events.fd_end >= 0) {
        pfds[nfds].fd = g_events.fd_end;
        pfds[nfds].events = POLLIN;
        pfds[nfds].revents = 0;
        nfds++;
    }
    if (g_events.fd_trig >= 0) {
        pfds[nfds].fd = g_events.fd_trig;
        pfds[nfds].events = POLLIN;
        pfds[nfds].revents = 0;
        nfds++;
    }
    if (nfds == 0) {
        sleep_ms(timeout_ms);
        return;
    }
    if (poll(pfds, (nfds_t)nfds, timeout_ms) <= 0) {
        return;
    }
    for (i = 0; i < nfds; i++) {
        if (pfds[i].revents & (POLLIN | POLLERR | POLLHUP)) {
            drain_fd(pfds[i].fd);
        }
    }
}

int gage_acq_status(uint32_t handle)
{
    return (int)CsGetStatus(handle);
}

int gage_acq_start(uint32_t handle)
{
    return (int)CsDo(handle, ACTION_START);
}

int gage_acq_abort(uint32_t handle)
{
    return (int)CsDo(handle, ACTION_ABORT);
}

int gage_acq_force(uint32_t handle)
{
    return (int)CsDo(handle, ACTION_FORCE);
}

int gage_acq_wait_ready(uint32_t handle,
                        int timeout_ms,
                        const volatile int *stop_flag,
                        int *out_status)
{
    int64_t shot_deadline = -1;
    int64_t calib_started = -1;
    int last = 0;

    bind_events(handle);
    if (timeout_ms >= 0) {
        shot_deadline = monotonic_ms() + timeout_ms;
    }

    for (;;) {
        if (stop_flag != NULL && *stop_flag) {
            if (out_status) {
                *out_status = last;
            }
            return GAGE_ACQ_STOPPED;
        }

        last = (int)CsGetStatus(handle);
        if (last < 0) {
            if (out_status) {
                *out_status = last;
            }
            return last;
        }
        if (last == ACQ_READY) {
            if (out_status) {
                *out_status = last;
            }
            return GAGE_ACQ_READY;
        }

        if (last == ACQ_BUSY_CALIB) {
            int64_t now = monotonic_ms();
            if (calib_started < 0) {
                calib_started = now;
            } else if (now - calib_started >= GAGE_ACQ_CALIB_TIMEOUT_MS) {
                if (out_status) {
                    *out_status = last;
                }
                return GAGE_ACQ_TIMEOUT;
            }
            /* Calibration does not consume the shot timeout. */
        } else {
            calib_started = -1;
            if (shot_deadline >= 0 && monotonic_ms() >= shot_deadline) {
                if (out_status) {
                    *out_status = last;
                }
                return GAGE_ACQ_TIMEOUT;
            }
        }

        poll_events(GAGE_ACQ_POLL_MS);
    }
}

int gage_acq_transfer(uint32_t handle,
                      uint16_t channel,
                      uint32_t mode,
                      uint32_t segment,
                      int64_t start,
                      int64_t length,
                      void *buffer,
                      int64_t *actual_start,
                      int64_t *actual_length)
{
    IN_PARAMS_TRANSFERDATA in;
    OUT_PARAMS_TRANSFERDATA out;
    int32_t sts;

    memset(&in, 0, sizeof(in));
    memset(&out, 0, sizeof(out));
    in.u16Channel = channel;
    in.u32Mode = mode;
    in.u32Segment = segment;
    in.i64StartAddress = start;
    in.i64Length = length;
    in.pDataBuffer = buffer;
    in.hNotifyEvent = NULL;
    in.hNotifyEvent_read = 0;
    in.hNotifyEvent_write = 0;

    sts = CsTransfer(handle, &in, &out);
    if (actual_start) {
        *actual_start = out.i64ActualStart;
    }
    if (actual_length) {
        *actual_length = out.i64ActualLength;
    }
    return (int)sts;
}

int gage_acq_error_string(int code, char *buf, int buf_len)
{
    if (buf == NULL || buf_len < 1) {
        return -1;
    }
    buf[0] = '\0';
    return (int)CsGetErrorStringA(code, buf, buf_len);
}
