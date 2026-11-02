"""Room validation: structural (JSON Schema) then geometric.

Two document kinds share field names:
  full  room-capture.schema.json: geometry plus appearance (materials, colours)
  core  room-core.schema.json:    geometry only, plus optional Confidence

JSON Schema can't tell whether a room can exist, so the geometric pass checks
that walls close into a simple polygon, that the declared Shape and
Dimensions match the walls, and that every door, window, closet and recess
fits its wall without overlapping another.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Literal

from jsonschema import Draft202012Validator

from . import geometry as g

Kind = Literal["core", "full"]

SCHEMA_FILES = {"full": "room-capture.schema.json", "core": "room-core.schema.json"}

MIN_WALL_M = 0.05  # shorter walls are almost certainly noise
ANGLE_TOL_DEG = 1.0
EPS = 1e-6

# Wall IDs that name a compass direction must face roughly that way.
COMPASS = {
    "north": 0, "n": 0, "northeast": 45, "ne": 45, "east": 90, "e": 90, "southeast": 135, "se": 135,
    "south": 180, "s": 180, "southwest": 225, "sw": 225, "west": 270, "w": 270, "northwest": 315, "nw": 315,
}


# The downstream generator rejects wall chains whose end misses the start by
# more than 0.001. Measuring that in document units is at least as strict as
# the generator whether it closes the chain before or after converting to
# metres, so anything that passes here also builds there.
CLOSURE_TOL_UNITS = 0.001


@dataclass(frozen=True)
class Issue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


@cache
def load_schema(kind: Kind) -> dict[str, Any]:
    text = resources.files("scan_to_schema").joinpath("schema", SCHEMA_FILES[kind]).read_text()
    return json.loads(text)


@cache
def _validator(kind: Kind) -> Draft202012Validator:
    schema = load_schema(kind)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def detect_kind(room: dict[str, Any]) -> Kind:
    """Full documents carry appearance; core documents never do."""
    return "full" if ("Flooring" in room or "Ceiling" in room) else "core"


def validate_room(room: dict[str, Any], kind: Kind | None = None) -> list[Issue]:
    """Return every problem found. An empty list means the room is valid."""
    kind = kind or detect_kind(room)
    issues = [
        Issue("/".join(str(p) for p in e.absolute_path), e.message)
        for e in sorted(_validator(kind).iter_errors(room), key=lambda e: list(e.absolute_path))
    ]
    if issues:
        return issues  # geometric checks assume a well-formed document
    return list(_geometric_issues(room))


def validate_file(path: str | Path, kind: Kind | None = None) -> list[Issue]:
    try:
        room = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        return [Issue("", f"invalid JSON: {e}")]
    return validate_room(room, kind)


# --- projection ------------------------------------------------------------


def to_core(full: dict[str, Any]) -> dict[str, Any]:
    """Drop everything a scan can't recover, keeping only fields the core schema defines."""
    core = load_schema("core")

    def resolve(node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            node = core["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        return node

    def project(value: Any, node: dict[str, Any]) -> Any:
        node = resolve(node)
        if isinstance(value, dict) and "properties" in node:
            props = node["properties"]
            return {k: project(v, props[k]) for k, v in value.items() if k in props}
        if isinstance(value, list) and "items" in node:
            return [project(v, node["items"]) for v in value]
        return value

    return project(full, core)


# --- geometry ---------------------------------------------------------------


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _geometric_issues(room: dict[str, Any]) -> Iterable[Issue]:
    to_m = g.scale(room)
    walls = room["Walls"]["Items"]
    by_id: dict[str, dict[str, Any]] = {}
    for i, w in enumerate(walls):
        if w["ID"] in by_id:
            yield Issue(f"Walls/Items/{i}/ID", f"duplicate wall ID {w['ID']!r}")
        by_id[w["ID"]] = w
        if w["Length"] * to_m < MIN_WALL_M:
            yield Issue(f"Walls/Items/{i}", f"wall {w['ID']!r} is {w['Length'] * to_m:.3f} m long; near-duplicate corner?")

        bearing = COMPASS.get(w["ID"].strip().lower())
        if bearing is not None and _angle_diff(bearing, w["Orientation"]) > 45:
            yield Issue(f"Walls/Items/{i}/Orientation", f"wall {w['ID']!r} faces {w['Orientation']:g} degrees; Orientation is the bearing the wall faces outward")

    if len(walls) < 3:
        yield Issue("Walls/Items", f"a closed room needs at least 3 walls, found {len(walls)}")
        return

    # Footprint
    plan = g.build_plan(walls, to_m)
    gap_units = plan.closure_gap / to_m
    if gap_units > CLOSURE_TOL_UNITS:
        yield Issue("Walls/Items", f"walls don't close: chain ends {gap_units:.4g} {room['Units']} from its start (tolerance {CLOSURE_TOL_UNITS} {room['Units']})")
    else:
        for i, j in g.self_intersections(plan.points):
            yield Issue("Walls/Items", f"walls {plan.wall_ids[i]!r} and {plan.wall_ids[j]!r} cross; footprint must be a simple polygon")
        if plan.area <= EPS:
            yield Issue("Walls/Items", "walls enclose no area; check Orientation values face outward")
        elif plan.listed_clockwise:
            yield Issue("Walls/Items", "walls are listed clockwise; list them counter-clockwise viewed from above (wall order sets polygon edge order)")
    ccw = [by_id[i] for i in plan.wall_ids]
    for a, b in zip(ccw, ccw[1:] + ccw[:1]):
        if _angle_diff(a["Orientation"], b["Orientation"]) < ANGLE_TOL_DEG:
            yield Issue("Walls/Items", f"walls {a['ID']!r} and {b['ID']!r} are adjacent and face the same way; merge them")

    yield from _shape_issues(room, ccw, to_m)

    # Height
    dims = room.get("Dimensions", {})
    room_h = dims.get("Height")
    if room_h is None and not any("Height" in w for w in walls if not w.get("Open")):
        yield Issue("", "no height: set Dimensions.Height or Height on at least one non-open wall")
        return

    def wall_h(w: dict[str, Any]) -> float:
        return w.get("Height", room_h if room_h is not None else max(x["Height"] for x in walls if "Height" in x))

    # Openings and cut-ins, gathered per wall as (start, end, bottom, top, label, path)
    spans: dict[str, list[tuple[float, float, float, float, str, str]]] = {}

    def place(path: str, label: str, wall_id: str, start: float, width: float, bottom: float, height: float, allow_open: bool = False) -> Iterable[Issue]:
        w = by_id.get(wall_id)
        if w is None:
            yield Issue(f"{path}/WallID", f"{label} references unknown wall {wall_id!r}")
            return
        if w.get("Open") and not allow_open:
            yield Issue(f"{path}/WallID", f"{label} is on open wall {wall_id!r}, which has no surface")
            return
        end, top = start + width, bottom + height
        if end > w["Length"] + EPS:
            yield Issue(path, f"{label} runs past the end of wall {wall_id!r} ({end:g} > {w['Length']:g} {room['Units']})")
        if not w.get("Open") and top > wall_h(w) + EPS:
            yield Issue(path, f"{label} top ({top:g}) is above wall {wall_id!r} height ({wall_h(w):g})")
        spans.setdefault(wall_id, []).append((start, end, bottom, top, label, path))

    for k, d in enumerate(room.get("Doors", [])):
        yield from place(f"Doors/{k}", f"door {k}", d["WallID"], d["Offset"], d["Width"], 0.0, d["Height"])

    for k, win in enumerate(room.get("Windows", [])):
        path = f"Windows/{k}"
        if win.get("Type") == "Skylight":
            continue  # ceiling-mounted; wall geometry doesn't apply
        if win["SillHeight"] <= EPS:
            yield Issue(f"{path}/SillHeight", "window starts at the floor; model a floor-level opening as a door")
        yield from place(path, f"window {k}", win["WallID"], win["Offset"], win["Width"], win["SillHeight"], win["Height"])

    for k, c in enumerate(room.get("Closets", [])):
        path = f"Closets/{k}"
        if c["OpeningWidth"] > c["Width"] + EPS:
            yield Issue(path, f"closet {k} opening ({c['OpeningWidth']:g}) is wider than its interior ({c['Width']:g})")
        if c.get("OpeningCentered", True):
            inset = (c["Width"] - c["OpeningWidth"]) / 2
        elif "OpeningOffset" not in c:
            yield Issue(path, f"closet {k} has OpeningCentered false but no OpeningOffset")
            continue
        else:
            inset = c["OpeningOffset"]
            if inset + c["OpeningWidth"] > c["Width"] + EPS:
                yield Issue(path, f"closet {k} opening runs past the closet's interior edge")
        yield from place(path, f"closet {k} opening", c["WallID"], c["Offset"] + inset, c["OpeningWidth"], 0.0, c["OpeningHeight"])

    for k, r in enumerate(room.get("Recesses", [])):
        path = f"Recesses/{k}"
        w = by_id.get(r["WallID"])
        thickness = (w or {}).get("Thickness", room["Walls"].get("Defaults", {}).get("Thickness"))
        if thickness is not None and r["Depth"] > thickness + EPS:
            yield Issue(f"{path}/Depth", f"recess {k} depth ({r['Depth']:g}) is deeper than the wall (thickness {thickness:g})")
        yield from place(path, f"recess {k}", r["WallID"], r["Offset"], r["Width"], r.get("BottomHeight", 0.0), r["Height"])

    for wall_id, items in spans.items():
        items.sort()
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if b[0] >= a[1] - EPS:
                    break
                if b[2] < a[3] - EPS and a[2] < b[3] - EPS:
                    yield Issue(b[5], f"{b[4]} overlaps {a[4]} on wall {wall_id!r}")


def _shape_issues(room: dict[str, Any], ccw: list[dict[str, Any]], to_m: float) -> Iterable[Issue]:
    shape = room["Shape"]
    if shape == "Irregular":
        return
    turns = g.turn_angles(ccw)
    right_angled = all(min(abs(abs(t) - 90), abs(t)) < ANGLE_TOL_DEG for t in turns)
    tol = CLOSURE_TOL_UNITS

    if shape in ("Rectangle", "Square"):
        if len(ccw) != 4 or not right_angled:
            yield Issue("Shape", f"{shape} needs 4 walls meeting at right angles; found {len(ccw)} walls")
            return
        # The generator builds rectangles from Dimensions alone:
        # (0,0) → (Width,0) → (Width,Length) → (0,Length), so Width runs along x
        # and Length along z, and wall i is edge i. Walls must therefore be listed
        # in that order: [Width, Length, Width, Length], starting with the edge
        # that runs along +x.
        lengths = [w["Length"] for w in room["Walls"]["Items"]]
        dims = room["Dimensions"]
        expected = [dims["Width"], dims["Length"], dims["Width"], dims["Length"]]
        if any(abs(a - b) > tol for a, b in zip(lengths, expected)):
            yield Issue(
                "Walls/Items",
                f"{shape} walls must be listed [Width, Length, Width, Length] = {expected} "
                f"(Width along x, Length along z, starting from the edge along +x); found {lengths}",
            )
        if shape == "Square" and abs(dims["Length"] - dims["Width"]) > tol:
            yield Issue("Dimensions", f"Square has Length {dims['Length']:g} and Width {dims['Width']:g}")
    elif shape == "L-Shape":
        reflex = sum(1 for t in turns if t < -ANGLE_TOL_DEG)
        if len(ccw) != 6 or not right_angled or reflex != 1:
            yield Issue("Shape", f"L-Shape needs 6 right-angled walls with exactly one inside corner; found {len(ccw)} walls, {reflex} inside corners")
