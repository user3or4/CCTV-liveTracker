"""
Simulated tests for the timing engine (no camera needed).

We fake the passage of time and feed in "who is present" second by second,
then check the reported numbers are right.
"""

from timers import StationMonitor


def simulate(schedule, total, dt=0.2,
             worker_on=2.0, worker_off=2.0, car_on=2.0, car_off=3.0):
    """
    schedule(t) -> (car_present: bool, present_worker_ids: set)
    Steps time from 0 to `total` and returns the report emitted when the car left.
    """
    mon = StationMonitor(worker_on, worker_off, car_on, car_off)
    report = None
    t = 0.0
    while t <= total:
        car, ids = schedule(t)
        r = mon.update(t, car, ids)
        if r is not None:
            report = r
        t += dt
    return report


def approx(a, b, tol=0.6):
    return abs(a - b) <= tol


# --- Scenario 1: one worker, steps away and comes back, someone walks past ---
def scenario1(t):
    car = 2.0 <= t <= 30.0          # car raw-present 2..30
    ids = set()
    if 3.0 <= t <= 10.0:            # worker #1 present 3..10
        ids.add(1)
    if 15.0 <= t <= 20.0:          # worker #1 returns 15..20
        ids.add(1)
    if 22.0 <= t <= 23.0:          # someone (#2) just walks past for 1s
        ids.add(2)
    return car, ids


r1 = simulate(scenario1, total=40.0)
print("Scenario 1 report:", r1)
assert r1 is not None, "no report produced"
# cycle: car debounced on ~t=4, off ~t=33  -> ~29s
assert approx(r1["cycle_time"], 29.0, 1.5), r1["cycle_time"]
# hands-on: on-delay and off-delay cancel, so ~ true presence:
#   ~(10-3) + ~(20-15) = 7 + 5 = ~12s
assert approx(r1["hands_on_time"], 12.0, 1.5), r1["hands_on_time"]
assert r1["unique_workers"] == 1, r1["unique_workers"]      # walker-past not counted
assert r1["on_sessions"] == 2, r1["on_sessions"]            # away and back
print("  -> unique=1, sessions=2, walker-past ignored  OK")


# --- Scenario 2: two workers overlapping (no double counting) ---
def scenario2(t):
    car = 2.0 <= t <= 25.0
    ids = set()
    if 4.0 <= t <= 20.0:
        ids.add(1)
    if 4.0 <= t <= 20.0:
        ids.add(2)
    return car, ids


r2 = simulate(scenario2, total=35.0)
print("Scenario 2 report:", r2)
# both present raw 4..20; counted ~6..22 = ~16s of hands-on (union, NOT ~32)
assert approx(r2["hands_on_time"], 16.0, 1.5), r2["hands_on_time"]
assert r2["unique_workers"] == 2, r2["unique_workers"]
assert r2["on_sessions"] == 1, r2["on_sessions"]            # one continuous stretch
print("  -> two workers, union hands-on ~14s (not doubled)  OK")


# --- Scenario 3: flicker should NOT create on/off churn ---
def scenario3(t):
    car = 2.0 <= t <= 20.0
    ids = set()
    # worker present the whole time EXCEPT a single 0.2s blink at t=10
    if 4.0 <= t <= 18.0 and not (9.9 <= t <= 10.1):
        ids.add(1)
    return car, ids


r3 = simulate(scenario3, total=30.0)
print("Scenario 3 report:", r3)
assert r3["on_sessions"] == 1, r3["on_sessions"]            # blink ignored -> still one session
print("  -> single blink ignored, stays one session  OK")

print("\nALL TIMING TESTS PASSED")
