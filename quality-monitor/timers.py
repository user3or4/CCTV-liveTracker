"""
The timing engine for the worker hands-on timer.

Two different clocks, in plain words:

  - CYCLE TIME  = how long the car sits in the station, start to finish.
                  Runs the whole time the car is there. "Clock on the wall".

  - HANDS-ON TIME = how long a worker is actually present in the work area.
                    Counts UP only while a worker is there and PAUSES when the
                    area is empty. "Stopwatch that only runs while working".

To stop the numbers flickering, we "debounce": a worker must be seen for a
short time in a row before we count them ON, and must be gone for a while in a
row before we count them OFF. The OFF wait is deliberately long (20 seconds)
so a car briefly hidden behind a worker - or a worker who steps out for a
moment - does not falsely end things.

Each worker inside one car's visit is numbered 1, 2, 3, ... (in the order they
first start working), so every station reports per-person times starting from 1.

This file is pure timing logic with no camera or screen code (see test_timers.py).
"""

# Default debounce windows, in seconds.
WORKER_ON_SECONDS = 2.0      # must be present ~2s in a row before counting ON
WORKER_OFF_SECONDS = 20.0    # must be absent ~20s in a row before counting OFF
CAR_ON_SECONDS = 2.0
CAR_OFF_SECONDS = 20.0       # a car must be gone ~20s before we end the cycle


class Debouncer:
    """Turns a jittery yes/no signal into a steady one."""

    def __init__(self, on_secs, off_secs, start_state=False):
        self.on_secs = on_secs
        self.off_secs = off_secs
        self.state = start_state
        self._pending_since = None

    def update(self, raw, now):
        if raw == self.state:
            self._pending_since = None
        else:
            if self._pending_since is None:
                self._pending_since = now
            needed = self.on_secs if raw else self.off_secs
            if now - self._pending_since >= needed:
                self.state = raw
                self._pending_since = None
        return self.state


class WorkerState:
    """Tracks one worker (by their tracking ID) within a single car's visit."""

    def __init__(self, worker_id, on_secs, off_secs):
        self.id = worker_id
        self.deb = Debouncer(on_secs, off_secs)
        self.on_time = 0.0       # seconds this worker was actively present
        self.sessions = 0        # how many separate times they were "on"
        self.local_index = None  # 1, 2, 3, ... assigned when they first count on


class CarSession:
    """Everything measured for one car, from when it arrives until it leaves."""

    def __init__(self, start_time, on_secs, off_secs):
        self.start = start_time
        self.on_secs = on_secs
        self.off_secs = off_secs
        self.workers = {}          # worker_id -> WorkerState
        self.hands_on_time = 0.0   # union time: any worker present (no double count)
        self.on_sessions = 0       # separate "on" stretches (any worker)
        self.station_on = False
        self.car_track_id = None   # the tracking ID of the car being worked on
        self._next_index = 1       # next per-station worker number to hand out

    def tick(self, now, dt, present_worker_ids, present_car_ids):
        # Remember the car's tracking ID (first one we see in the station).
        if self.car_track_id is None and present_car_ids:
            self.car_track_id = min(present_car_ids)

        for wid in present_worker_ids:
            if wid not in self.workers:
                self.workers[wid] = WorkerState(wid, self.on_secs, self.off_secs)

        for wid, w in self.workers.items():
            was_on = w.deb.state
            is_on = w.deb.update(wid in present_worker_ids, now)
            if is_on and not was_on:
                w.sessions += 1
                if w.local_index is None:          # first time this worker works
                    w.local_index = self._next_index
                    self._next_index += 1
            if is_on:
                w.on_time += dt

        station_on_now = any(w.deb.state for w in self.workers.values())
        if station_on_now and not self.station_on:
            self.on_sessions += 1
        if station_on_now:
            self.hands_on_time += dt
        self.station_on = station_on_now

    def counted_workers(self):
        """Workers who actually worked, sorted by their per-station number."""
        got = [w for w in self.workers.values() if w.local_index is not None]
        return sorted(got, key=lambda w: w.local_index)

    def finish(self, end_time):
        workers = self.counted_workers()
        return {
            "car_track_id": self.car_track_id,
            "start_epoch": self.start,      # when the car arrived (for started_at)
            "end_epoch": end_time,          # when the car left (for finished_at)
            "cycle_time": round(end_time - self.start, 1),
            "hands_on_time": round(self.hands_on_time, 1),
            "unique_workers": len(workers),
            "on_sessions": self.on_sessions,
            "workers": [
                {"index": w.local_index,
                 "hands_on": round(w.on_time, 1),
                 "sessions": w.sessions}
                for w in workers
            ],
        }


class StationMonitor:
    """
    One station: watches for a car arriving/leaving and, while a car is present,
    runs the hands-on timers for the workers.

    Call update(...) once per processed frame. Returns a finished report dict
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

    def update(self, now, car_ids, worker_present_ids):
        """
        now                : current time in seconds (time.time()).
        car_ids            : set/list of car tracking IDs in the station now.
        worker_present_ids : set/list of worker tracking IDs in the work area now.

        Returns a report dict when a car has just left, else None.
        """
        car_ids = set(car_ids)
        worker_present_ids = set(worker_present_ids)

        dt = 0.0 if self.last_time is None else now - self.last_time
        self.last_time = now

        car_here = self.car_deb.update(len(car_ids) > 0, now)

        if car_here and self.session is None:
            self.session = CarSession(now, self.worker_on, self.worker_off)

        if self.session is not None:
            self.session.tick(now, dt, worker_present_ids, car_ids)

        if not car_here and self.session is not None:
            report = self.session.finish(now)
            self.session = None
            return report

        return None

    def is_car_present(self):
        return self.session is not None

    def live_readout(self):
        if self.session is None:
            return None
        s = self.session
        return {
            "cycle_time": now_minus(s.start, self.last_time),
            "hands_on_time": round(s.hands_on_time, 1),
            "worker_on": s.station_on,
            "on_sessions": s.on_sessions,
            "workers_seen": len(s.counted_workers()),
        }


def now_minus(start, now):
    if now is None:
        return 0.0
    return round(now - start, 1)
