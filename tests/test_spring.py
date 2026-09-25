import math

import pytest

from motionkit.spring import Spring, resolve_spring


def numeric_ode(spring, x0, target, v0, T, dt=1e-4):
    x, v = x0, v0
    for _ in range(int(T / dt)):
        a = (-spring.stiffness * (x - target) - spring.damping * v) / spring.mass
        # semi-implicit Euler
        v += a * dt
        x += v * dt
    return x


@pytest.mark.parametrize("zeta", [0.2, 1.0, 2.5])
def test_closed_form_matches_integration(zeta):
    s = Spring.from_frequency(1.5, zeta)
    for T in (0.1, 0.4, 1.0):
        assert s.position(T, 0.0, 1.0, 0.7) == pytest.approx(numeric_ode(s, 0.0, 1.0, 0.7, T), abs=2e-3)


def test_regimes_and_overshoot():
    assert Spring.from_frequency(2, 0.3).regime == "underdamped"
    assert Spring.from_frequency(2, 1.0).regime == "critically_damped"
    assert Spring.from_frequency(2, 3.0).regime == "overdamped"
    under = Spring.from_frequency(2, 0.3).describe()
    # theoretical first overshoot exp(-pi zeta / sqrt(1 - zeta^2))
    assert under["overshoot"] == pytest.approx(math.exp(-math.pi * 0.3 / math.sqrt(1 - 0.09)), rel=0.02)
    assert Spring.from_frequency(2, 1.0).describe()["overshoot"] <= 1e-9


def test_settle_time_monotonic_in_damping_for_underdamped():
    t1 = Spring.from_frequency(2, 0.2).settle_time()
    t2 = Spring.from_frequency(2, 0.6).settle_time()
    assert t1 > t2 > 0


def test_resolve_spring_styles():
    s = resolve_spring({"stiffness": 200, "damping": 10})
    assert s.stiffness == 200 and s.damping == 10
    s = resolve_spring({"frequency_hz": 3, "damping_ratio": 0.4})
    assert s.omega0 == pytest.approx(2 * math.pi * 3)
    assert s.zeta == pytest.approx(0.4)
    s = resolve_spring({"period_s": 0.5, "damping_ratio": 1})
    assert s.omega0 == pytest.approx(4 * math.pi)
