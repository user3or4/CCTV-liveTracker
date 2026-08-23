"""
Simulated tests for the timing engine (no camera needed).

We fake the passage of time and feed in "which cars/workers are present" second
by second, then check the reported numbers are right.
"""

from timers import StationMonitor


def simulate(schedule, total, dt=0.2,
             worker_on=2.0, worker_off=2.0, car_on=2.0, car_off=2.0):
    """
    schedule(t) -> (car_ids: set, worker_ids: set)
    Steps time from 0 to `total`; returns the report emitted when the car left.
    """
    mon = StationMonitor(worker_on, worker_off, car_on, car_off)
    report = None
    t = 0.0
    while t <= total:
        cars, workers = schedule(t)
        r = mon.update(t, cars, workers)
        if r is not None:
            report = r
        t += dt
    return report


def approx(a, b, tol=0.6):
    return abs(a - b) <= tol


# --- Scenario 1: one worker steps away and back; someone walks past ---
def scenario1(t):
    cars = {100} if 2.0 <= t <= 30.0 else set()   # car #100 in station 2..30
    ids = set()
    if 3.0 <= t <= 10.0:
        ids.add(7)                                # worker #7 present 3..10
    if 15.0 <= t <= 20.0:
        ids.add(7)                                # worker #7 returns 15..20
    if 22.0 <= t <= 23.0:
        ids.add(9)                                # someone walks past for 1s
    return cars, ids


r1 = simulate(scenario1, total=40.0, worker_off=2.0, car_off=2.0)
print("Scenario 1:", r1)
assert r1["car_track_id"] == 100, r1["car_track_id"]
assert r1["unique_workers"] == 1, r1["unique_workers"]          # walker-past ignored
assert r1["on_sessions"] == 2, r1["on_sessions"]               # away and back (2s off)
assert len(r1["workers"]) == 1 and r1["workers"][0]["index"] == 1, r1["workers"]
assert r1["workers"][0]["sessions"] == 2, r1["workers"]
print("  car id captured, 1 worker (numbered 1), 2 sessions, walker ignored  OK")


# --- Scenario 2: two workers overlapping -> numbered 1 and 2, union time ---
def scenario2(t):
    cars = {200} if 2.0 <= t <= 25.0 else set()
    ids = set()
    if 4.0 <= t <= 20.0:
        ids.add(1)
    if 6.0 <= t <= 20.0:
        ids.add(2)
    return cars, ids


r2 = simulate(scenario2, total=35.0, worker_off=2.0, car_off=2.0)
print("Scenario 2:", r2)
assert r2["unique_workers"] == 2, r2["unique_workers"]
idxs = sorted(w["index"] for w in r2["workers"])
assert idxs == [1, 2], idxs                                    # numbered from 1
assert approx(r2["hands_on_time"], 16.0, 1.5), r2["hands_on_time"]  # union, not doubled
print("  two workers numbered 1 & 2, union hands-on ~16s  OK")


# --- Scenario 3: 20s-off tolerance -> a 10s gap does NOT split the session ---
def scenario3(t):
    cars = {300} if 2.0 <= t <= 50.0 else set()
    ids = set()
    # worker present 4..14, gone 14..24 (10s gap), back 24..40
    if (4.0 <= t <= 14.0) or (24.0 <= t <= 40.0):
        ids.add(5)
    return cars, ids


r3 = simulate(scenario3, total=85.0, worker_on=2.0, worker_off=20.0, car_off=20.0)
print("Scenario 3:", r3)
assert r3["on_sessions"] == 1, r3["on_sessions"]              # 10s < 20s -> still one
assert r3["unique_workers"] == 1, r3["unique_workers"]
print("  10s gap tolerated by 20s-off rule -> still one session  OK")


# --- Scenario 4: a gap longer than 20s DOES split into two sessions ---
def scenario4(t):
    cars = {400} if 2.0 <= t <= 90.0 else set()
    ids = set()
    if (4.0 <= t <= 20.0) or (50.0 <= t <= 70.0):   # 30s gap
        ids.add(5)
    return cars, ids


r4 = simulate(scenario4, total=125.0, worker_on=2.0, worker_off=20.0, car_off=20.0)
print("Scenario 4:", r4)
assert r4["on_sessions"] == 2, r4["on_sessions"]
print("  30s gap exceeds 20s-off rule -> two sessions  OK")

print("\nALL TIMING TESTS PASSED")
