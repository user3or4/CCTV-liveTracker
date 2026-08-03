"""
The timing engine for the worker hands-on timer.

Two different clocks, in plain words:

  - CYCLE TIME  = how long the car sits in the station, start to finish.
                  It runs the whole time the car is there, whether or not
                  anyone is working on it. Think "wall clock on the wall".

  - HANDS-ON TIME = how long a worker is actually present in the work area
                    next to the car. It counts UP only while a worker is
                    there and PAUSES when the work area is empty. Think
                    "stopwatch that only runs while someone is working".

To stop the numbers flickering when a detection blinks for a frame, we
"debounce": a worker must be seen for about 2 seconds in a row before we
count them as ON, and must be gone for about 2 seconds in a row before we
count them as OFF. The car uses the same idea so a one-frame glitch doesn't
start or end a cycle by mistake.

This file is pure timing logic with no camera or screen code, so it can be
tested on its own (see test_timers.py).
"""

# Default debounce windows, in seconds.
WORKER_ON_SECONDS = 2.0
WORKER_OFF_SECONDS = 2.0
CAR_ON_SECONDS = 2.0
CAR_OFF_SECONDS = 3.0   # a little longer so a brief blip doesn't end a cycle


class Debouncer:
    """
    Turns a jittery yes/no signal into a steady one.

    Feed it the raw yes/no each moment; it only flips its steady answer after
    the raw signal has held the new value long enough (on_secs / off_secs).
    """

    def __init__(self, on_secs, off_secs, start_state=False):
        self.on_secs = on_secs
        self.off_secs = off_secs
        self.state = start_state
        self._pending_since = None  # when the raw signal first disagreed with state

    def update(self, raw, now):
        if raw == self.state:
            self._pending_since = None            # nothing to change
        else:
            if self._pending_since is None:
                self._pending_since = now          # start the waiting clock
            needed = self.on_secs if raw else self.off_secs
            if now - self._pending_since >= needed:
                self.state = raw                   # held long enough: flip
                self._pending_since = None
        return self.state


class WorkerState:
    """Tracks one worker (by their tracking ID) within a single car's visit."""

    def __init__(self, worker_id, on_secs, off_secs):
        self.id = worker_id
        self.deb = Debouncer(on_secs, off_secs)
        self.on_time = 0.0      # seconds this worker was actively present
        self.sessions = 0       # how many separate times they were "on"


class CarSession:
    """Everything measured for one car, from when it arrives until it leaves."""

    def __init__(self, start_time, on_secs, off_secs):
        self.start = start_time
        self.on_secs = on_secs
        self.off_secs = off_secs
        self.workers = {}          # worker_id -> WorkerState
        self.hands_on_time = 0.0   # union time: any worker present (no double counting)
        self.on_sessions = 0       # how many separate "on" stretches (any worker)
        self.station_on = False    # is at least one worker currently on?

    def tick(self, now, dt, present_ids):
        """Advance all the timers by one step. present_ids = worker IDs in the work area now."""
        # Make sure every currently-present worker has a record.
        for wid in present_ids:
            if wid not in self.workers:
                self.workers[wid] = WorkerState(wid, self.on_secs, self.off_secs)

        # Update each known worker's on/off state and their personal on-time.
        for wid, w in self.workers.items():
            was_on = w.deb.state
            is_on = w.deb.update(wid in present_ids, now)
            if is_on and not was_on:
                w.sessions += 1
            if is_on:
                w.on_time += dt

        # Station-level "someone is working" = any worker on. This is the
        # hands-on clock and it never double counts two workers.
        station_on_now = any(w.deb.state for w in self.workers.values())
        if station_on_now and not self.station_on:
            self.on_sessions += 1
        if station_on_now:
            self.hands_on_time += dt
        self.station_on = station_on_now

    def unique_workers(self):
        """How many different workers were actually counted as on at some point."""
        return sum(1 for w in self.workers.values() if w.sessions > 0)

    def finish(self, end_time):
        """Produce the final report for this car."""
        return {
            "cycle_time": round(end_time - self.start, 1),
            "hands_on_time": round(self.hands_on_time, 1),
            "unique_workers": self.unique_workers(),
            "on_sessions": self.on_sessions,
        }


class StationMonitor:
    """
    Ties it together for one station: watches for a car arriving/leaving and,
    while a car is present, runs the hands-on timers for the workers.

    Call update(...) once per video frame. It returns a finished report dict
    the moment a car leaves, otherwise None.
    """

    def __init__(self,
                 worker_on=WORKER_ON_SECONDS, worker_off=WORKER_OFF_SECONDS,
                 car_on=CAR_ON_SECONDS, car_off=CAR_OFF_SECONDS):
        self.worker_on = worker_on
        self.worker_off = worker_off
        self.car_deb = Debouncer(car_on, car_off)
        self.session = None
        self.last_time = None

    def update(self, now, car_present_raw, worker_present_ids):
        """
        now                : current time in seconds (time.time()).
        car_present_raw    : True if at least one car is in the station right now.
        worker_present_ids : set/list of worker tracking IDs in the work area now.

        Returns a report dict when a car has just left, else None.
        """
        dt = 0.0 if self.last_time is None else now - self.last_time
        self.last_time = now

        car_here = self.car_deb.update(bool(car_present_raw), now)

        # A car just arrived -> start a fresh session.
        if car_here and self.session is None:
            self.session = CarSession(now, self.worker_on, self.worker_off)

        # While a car is here, advance the worker timers.
        if self.session is not None:
            self.session.tick(now, dt, set(worker_present_ids))

        # A car just left -> finish and report.
        if not car_here and self.session is not None:
            report = self.session.finish(now)
            self.session = None
            return report

        return None

    # --- Convenience read-outs for the on-screen display ---
    def is_car_present(self):
        return self.session is not None

    def live_readout(self):
        """Current numbers to show on screen while a car is in the station."""
        if self.session is None:
            return None
        s = self.session
        return {
            "cycle_time": now_minus(s.start, self.last_time),
            "hands_on_time": round(s.hands_on_time, 1),
            "worker_on": s.station_on,
            "on_sessions": s.on_sessions,
            "workers_seen": s.unique_workers(),
        }


def now_minus(start, now):
    if now is None:
        return 0.0
    return round(now - start, 1)
