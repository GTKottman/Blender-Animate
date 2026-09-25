import math

import pytest

from motionkit.bezier import BezierPath, cubic_bezier_easing


def test_css_ease_values():
    ease = cubic_bezier_easing(0.25, 0.1, 0.25, 1.0)
    # Known value of CSS 'ease' at t=0.5 is ~0.8024
    assert ease(0.5) == pytest.approx(0.8024, abs=1e-3)
    lin = cubic_bezier_easing(1 / 3, 1 / 3, 2 / 3, 2 / 3)
    for t in (0.1, 0.5, 0.9):
        assert lin(t) == pytest.approx(t, abs=1e-9)


def test_path_through_points_and_arc_length():
    pts = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]]
    p = BezierPath.through_points(pts)
    assert p.length == pytest.approx(3.0, abs=1e-6)
    for f in (0.0, 0.25, 0.5, 1.0):
        assert p.point_at_fraction(f)[0] == pytest.approx(3 * f, abs=1e-3)
    assert p.point(1.0) == pytest.approx((1.0, 0.0, 0.0))


def test_arc_length_parameterization_is_constant_speed():
    p = BezierPath.through_points([[0, 0, 0], [0.2, 1, 0], [3, 1.2, 0], [4, 0, 1]])
    L = p.length
    prev = p.point_at_fraction(0)
    steps = 50
    for k in range(1, steps + 1):
        cur = p.point_at_fraction(k / steps)
        d = math.dist(prev, cur)
        assert d == pytest.approx(L / steps, rel=0.02)
        prev = cur


def test_closed_path_circle_like():
    pts = [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]]
    p = BezierPath.through_points(pts, closed=True)
    assert p.segment_count == 4
    # Catmull-Rom through 4 points bulges between the inscribed square and the circle.
    assert 4 * math.sqrt(2) < p.length < 2 * math.pi
