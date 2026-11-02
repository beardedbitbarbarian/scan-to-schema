"""Turn a room document's wall chain into plan geometry.

Frame matches the downstream room-to-GLB generator: right-handed, +Y up, metres,
with plan coordinates (x, z) equal to glTF x and z. A wall's Orientation is the
bearing of its outward face in document-frame labels (0 "north" = +z, 90 "east"
= +x; not true compass directions). Walking the footprint in positive order in
the (x, z) plane (interior on the left), a wall facing bearing b runs in
direction (-cos b, sin b): the outward normal turned 90 degrees to the left. The room's inward face is
then the left perpendicular of each run, as in the generator.

Walls.Items must be listed counter-clockwise: the generator maps wall list
index directly to polygon edge index. The edge vectors are fixed by
Orientation alone, so a clockwise listing is detectable from the sign of the
polygon's area (it walks the shape backwards). build_plan reverses it so the
geometry can still be analysed; the validator reports it as an error.

Offset is the distance along a wall's edge from its start vertex (the
generator uses it directly as the opening's left coordinate on that edge).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

UNIT_TO_M = {"meters": 1.0, "centimeters": 0.01, "feet": 0.3048, "inches": 0.0254}

# Offset measures from the start of the wall's CCW run ("left" as seen from
# outside the room), matching how the downstream generator places openings.
OFFSET_FROM = "start"

Point = tuple[float, float]


@dataclass(frozen=True)
class Plan:
    """Footprint in metres, with walls in counter-clockwise order."""

    wall_ids: list[str]  # CCW order
    points: list[Point]  # (x, z); points[i] is the start of wall_ids[i]; points[0] is the origin
    closure_gap: float  # distance between the chain's end and its start
    listed_clockwise: bool

    @property
    def area(self) -> float:
        return signed_area(self.points)

    @property
    def perimeter(self) -> float:
        return sum(edge_lengths(self.points))


def scale(doc: dict[str, Any]) -> float:
    return UNIT_TO_M[doc["Units"]]


def direction(bearing_deg: float) -> Point:
    b = math.radians(bearing_deg)
    return (-math.cos(b), math.sin(b))


def build_plan(walls: list[dict[str, Any]], to_m: float = 1.0) -> Plan:
    def walk(items: list[dict[str, Any]]) -> tuple[list[Point], Point]:
        x = y = 0.0
        pts: list[Point] = []
        for w in items:
            pts.append((x, y))
            dx, dy = direction(w["Orientation"])
            x += dx * w["Length"] * to_m
            y += dy * w["Length"] * to_m
        return pts, (x, y)

    pts, end = walk(walls)
    clockwise = signed_area(pts) < 0
    ordered = walls
    if clockwise:
        ordered = list(reversed(walls))
        pts, end = walk(ordered)
    return Plan(
        wall_ids=[w["ID"] for w in ordered],
        points=pts,
        closure_gap=math.hypot(*end),
        listed_clockwise=clockwise,
    )


def signed_area(poly: list[Point]) -> float:
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))


def edge_lengths(poly: list[Point]) -> list[float]:
    return [math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1])]


def turn_angles(walls_ccw: list[dict[str, Any]]) -> list[float]:
    """Signed turn at each corner going CCW, degrees in (-180, 180]. Left turns are positive."""
    out = []
    for a, b in zip(walls_ccw, walls_ccw[1:] + walls_ccw[:1]):
        # bearing increases clockwise, so a left (CCW) turn decreases it
        d = (a["Orientation"] - b["Orientation"]) % 360
        out.append(d - 360 if d > 180 else d)
    return out


_EPS = 1e-9


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        min(a[0], b[0]) - _EPS <= p[0] <= max(a[0], b[0]) + _EPS
        and min(a[1], b[1]) - _EPS <= p[1] <= max(a[1], b[1]) + _EPS
    )


def _segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if d1 * d2 < -_EPS and d3 * d4 < -_EPS:
        return True
    return (
        (abs(d1) <= _EPS and _on_segment(q1, q2, p1))
        or (abs(d2) <= _EPS and _on_segment(q1, q2, p2))
        or (abs(d3) <= _EPS and _on_segment(p1, p2, q1))
        or (abs(d4) <= _EPS and _on_segment(p1, p2, q2))
    )


def self_intersections(poly: list[Point]) -> list[tuple[int, int]]:
    n = len(poly)
    hits = []
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # first and last edges share the start vertex
            if _segments_intersect(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                hits.append((i, j))
    return hits
