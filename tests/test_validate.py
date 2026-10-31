import copy
import json
from pathlib import Path

import pytest

from scan_to_schema import detect_kind, to_core, validate_room
from scan_to_schema.cli import main
from scan_to_schema.geometry import build_plan, turn_angles
from scan_to_schema.validate import load_schema

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.json"))


def load(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text())


@pytest.fixture
def full() -> dict:
    return load("bedroom.full.json")


@pytest.fixture
def core() -> dict:
    return load("l_shaped.core.json")


def messages(room: dict, kind=None) -> str:
    return "\n".join(str(i) for i in validate_room(room, kind))


def wall(room: dict, wall_id: str) -> dict:
    return next(w for w in room["Walls"]["Items"] if w["ID"] == wall_id)


# --- examples and schema relationship --------------------------------------

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path):
    assert validate_room(json.loads(path.read_text())) == []


def test_kind_detection(full, core):
    assert detect_kind(full) == "full"
    assert detect_kind(core) == "core"


def test_full_projects_to_valid_core(full):
    projected = to_core(full)
    assert "Flooring" not in projected and "Ceiling" not in projected
    assert "Material" not in projected["Doors"][0]
    assert validate_room(projected, "core") == []


def test_core_fields_are_a_subset_of_full():
    """Core must never invent a field: every core property exists in full, except Confidence."""
    core_s, full_s = load_schema("core"), load_schema("full")

    def props(schema, node):
        node = schema["$defs"][node] if isinstance(node, str) else node
        return set(node.get("properties", {}))

    assert props(core_s, core_s) - {"Confidence"} <= props(full_s, full_s)
    for name in core_s["$defs"]:
        if name == "Confidence":
            continue
        assert name in full_s["$defs"], name
        assert props(core_s, name) - {"Confidence"} <= props(full_s, name), name


def test_appearance_rejected_in_core(core):
    core["Doors"][0]["Material"] = "Wood"
    assert "Material" in messages(core, "core")


def test_confidence_rejected_in_full(full):
    full["Walls"]["Items"][0]["Confidence"] = 0.9
    assert "Confidence" in messages(full, "full")


# --- plan geometry ------------------------------------------------------------

def test_listing_order_detected(full):
    ccw = build_plan(full["Walls"]["Items"])  # S, E, N, W is counter-clockwise from above
    cw = build_plan(list(reversed(full["Walls"]["Items"])))
    assert cw.listed_clockwise and not ccw.listed_clockwise
    assert cw.area == pytest.approx(120) and ccw.area == pytest.approx(120)


def test_clockwise_listing_rejected(full, core):
    # The generator maps wall index to polygon edge index, so order matters.
    full["Walls"]["Items"].reverse()
    core["Walls"]["Items"].reverse()
    assert "listed clockwise" in messages(full)
    assert "listed clockwise" in messages(core)


def test_rotated_listing_start_is_fine(full):
    # Any wall may come first, as long as the order runs counter-clockwise.
    items = full["Walls"]["Items"]
    full["Walls"]["Items"] = items[2:] + items[:2]
    assert validate_room(full) == []


def test_l_shape_has_one_inside_corner(core):
    plan = build_plan(core["Walls"]["Items"])
    walls = {w["ID"]: w for w in core["Walls"]["Items"]}
    turns = turn_angles([walls[i] for i in plan.wall_ids])
    assert sorted(round(t) for t in turns) == [-90, 90, 90, 90, 90, 90]


def test_units_scale_the_plan(full):
    plan = build_plan(full["Walls"]["Items"], to_m=0.3048)
    assert plan.area == pytest.approx(120 * 0.3048**2)


# --- structural (from the original schema) -------------------------------------

def test_missing_required_field(full):
    del full["Flooring"]
    assert validate_room(full, "full")


def test_rectangle_requires_dimensions(full):
    del full["Dimensions"]
    assert "Dimensions" in messages(full)


def test_bad_enum(full):
    full["Doors"][0]["Type"] = "Portal"
    assert "Portal" in messages(full)


# --- walls and shape -------------------------------------------------------------

def test_walls_must_close(full):
    wall(full, "East")["Length"] = 11
    full["Shape"] = "Irregular"
    assert "don't close" in messages(full)


def test_small_closure_gap_tolerated(core):
    wall(core, "East")["Length"] += 0.004  # scanner-grade error
    assert validate_room(core) == []


def test_outward_facing_matters(full):
    for w in full["Walls"]["Items"]:
        w["Orientation"] = (w["Orientation"] + 180) % 360
    # Geometrically still a valid box; only the compass-named IDs give it away.
    assert "faces outward" in messages(full)


def test_self_intersecting_walls(core):
    # Swap two walls so the chain still closes but crosses itself.
    items = core["Walls"]["Items"]
    items[1], items[3] = items[3], items[1]
    core["Shape"] = "Irregular"
    assert "cross" in messages(core)


def test_duplicate_wall_ids(full):
    wall(full, "West")["ID"] = "North"
    assert "duplicate wall ID" in messages(full)


def test_rectangle_with_wrong_wall_count(core):
    core["Shape"] = "Rectangle"
    core["Dimensions"] = {"Length": 3.6, "Width": 4.1, "Height": 2.6}
    assert "Rectangle needs 4 walls" in messages(core)


def test_dimensions_must_match_walls(full):
    full["Dimensions"]["Length"] = 14
    assert "don't match the walls" in messages(full)


def test_square_needs_equal_sides(full):
    full["Shape"] = "Square"
    assert "Square has sides" in messages(full)


def test_l_shape_needs_one_inside_corner(full):
    full["Shape"] = "L-Shape"
    assert "L-Shape needs" in messages(full)


def test_needs_a_height(core):
    del core["Dimensions"]
    core["Shape"] = "L-Shape"
    assert "no height" in messages(core)


def test_wall_heights_can_replace_dimensions(core):
    del core["Dimensions"]
    for w in core["Walls"]["Items"]:
        w["Height"] = 2.6
    assert validate_room(core) == []


# --- openings ----------------------------------------------------------------------

def test_unknown_wall_reference(full):
    full["Doors"][0]["WallID"] = "Up"
    assert "unknown wall 'Up'" in messages(full)


def test_door_past_end_of_wall(full):
    full["Doors"][0]["Offset"] = 10
    assert "runs past the end of wall 'South'" in messages(full)


def test_window_above_ceiling(full):
    full["Windows"][0]["Height"] = 5.5
    assert "above wall 'North' height" in messages(full)


def test_window_at_floor(full):
    full["Windows"][0]["SillHeight"] = 0
    assert "starts at the floor" in messages(full)


def test_skylight_skips_wall_checks(full):
    full["Windows"][0].update(Type="Skylight", Offset=50)
    assert validate_room(full) == []


def test_door_on_open_wall(full):
    wall(full, "South")["Open"] = True
    assert "open wall 'South'" in messages(full)


def test_overlapping_door_and_closet(full):
    full["Doors"][0]["WallID"] = "East"
    full["Doors"][0]["Offset"] = 3.5
    assert "overlaps" in messages(full)


def test_transom_above_door_is_fine(full):
    full["Windows"].append(
        {"Type": "Fixed", "Material": "Wood", "Color": {"Name": "White"},
         "SillHeight": 6.8, "Width": 2.67, "Height": 1, "WallID": "South", "Offset": 1.5}
    )
    assert validate_room(full) == []


def test_closet_opening_wider_than_interior(full):
    full["Closets"][0]["OpeningWidth"] = 6
    assert "wider than its interior" in messages(full)


def test_off_center_closet_needs_offset(full):
    full["Closets"][0]["OpeningCentered"] = False
    assert "no OpeningOffset" in messages(full)


def test_off_center_closet_opening_fits(full):
    full["Closets"][0].update(OpeningCentered=False, OpeningOffset=0)
    assert validate_room(full) == []
    full["Closets"][0]["OpeningOffset"] = 2
    assert "past the closet's interior edge" in messages(full)


def test_recess_deeper_than_wall(full):
    full["Recesses"][0]["Depth"] = 0.5
    assert "deeper than the wall" in messages(full)


def test_recess_may_use_full_wall_thickness(full):
    full["Recesses"][0]["Depth"] = 0.4  # equals Walls.Defaults.Thickness
    assert validate_room(full) == []


def test_first_wall_starts_at_origin(full):
    plan = build_plan(full["Walls"]["Items"])
    assert plan.points[0] == (0.0, 0.0) and plan.wall_ids[0] == "South"


# --- cli -----------------------------------------------------------------------------

def test_cli_exit_codes(tmp_path, full, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(full))
    bad_room = copy.deepcopy(full)
    bad_room["Doors"][0]["Offset"] = 99
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_room))

    assert main(["validate", str(good)]) == 0
    assert main(["validate", str(good), str(bad)]) == 1
    assert "FAIL" in capsys.readouterr().out
    assert main(["validate", "--kind", "core", str(good)]) == 1  # full doc isn't core
