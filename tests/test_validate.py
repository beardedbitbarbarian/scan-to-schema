import copy
import json
from pathlib import Path

import pytest

from scan_to_schema import validate_room
from scan_to_schema.cli import main
from scan_to_schema.geometry import build_plan, turn_angles

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.json"))


def load(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text())


@pytest.fixture
def box() -> dict:
    return load("box_bedroom.json")


@pytest.fixture
def lshape() -> dict:
    return load("l_shaped.json")


def messages(room: dict) -> str:
    return "\n".join(str(i) for i in validate_room(room))


def wall(room: dict, wall_id: str) -> dict:
    return next(w for w in room["Walls"]["Items"] if w["ID"] == wall_id)


# --- examples and schema relationship --------------------------------------

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path):
    assert validate_room(json.loads(path.read_text())) == []


def test_appearance_fields_rejected(box):
    # room-lshape is geometry only.
    box["Doors"][0]["Material"] = "Wood"
    assert "Material" in messages(box)


def test_confidence_accepted(box):
    box["Walls"]["Items"][0]["Confidence"] = 0.9
    box["Confidence"] = 0.8
    assert validate_room(box) == []
    box["Confidence"] = 1.5
    assert "Confidence" in messages(box)


# --- plan geometry ------------------------------------------------------------

def test_listing_order_detected(box):
    ccw = build_plan(box["Walls"]["Items"])  # N, E, S, W is positive order in (x, z)
    cw = build_plan(list(reversed(box["Walls"]["Items"])))
    assert cw.listed_backwards and not ccw.listed_backwards
    assert cw.area == pytest.approx(120) and ccw.area == pytest.approx(120)


def test_wrong_direction_listing_rejected(box, lshape):
    # Wall i is polygon edge i, so listing direction matters.
    box["Walls"]["Items"].reverse()
    lshape["Walls"]["Items"].reverse()
    assert "wrong direction" in messages(box)
    assert "wrong direction" in messages(lshape)


def test_rotated_listing_start_is_fine_for_chains(lshape):
    # Chain-built rooms may start at any wall, as long as the order is positive.
    items = lshape["Walls"]["Items"]
    lshape["Walls"]["Items"] = items[2:] + items[:2]
    assert validate_room(lshape) == []


def test_rectangle_must_start_with_north_wall(box):
    # Rectangles are built from Dimensions: edge 0 is always the north wall.
    items = box["Walls"]["Items"]
    box["Walls"]["Items"] = items[2:] + items[:2]
    assert "north wall first" in messages(box)


def test_compass_frame():
    from scan_to_schema.geometry import direction
    # Outward bearing 0 (north, -z): the wall runs east, +x.
    assert direction(0) == pytest.approx((1, 0))
    # Outward bearing 90 (east, +x): the wall runs south, +z.
    assert direction(90) == pytest.approx((0, 1))


def test_l_shape_has_one_inside_corner(lshape):
    plan = build_plan(lshape["Walls"]["Items"])
    walls = {w["ID"]: w for w in lshape["Walls"]["Items"]}
    turns = turn_angles([walls[i] for i in plan.wall_ids])
    assert sorted(round(t) for t in turns) == [-90, 90, 90, 90, 90, 90]


def test_units_scale_the_plan(box):
    plan = build_plan(box["Walls"]["Items"], to_m=0.3048)
    assert plan.area == pytest.approx(120 * 0.3048**2)


# --- structural (from the original schema) -------------------------------------

def test_missing_required_field(box):
    del box["Walls"]
    assert "Walls" in messages(box)


def test_rectangle_requires_dimensions(box):
    del box["Dimensions"]
    assert "Dimensions" in messages(box)


def test_bad_enum(box):
    box["Doors"][0]["Type"] = "Portal"
    assert "Portal" in messages(box)


# --- walls and shape -------------------------------------------------------------

def test_walls_must_close(box):
    wall(box, "East")["Length"] = 11
    box["Shape"] = "Irregular"
    assert "don't close" in messages(box)


def test_closure_tolerance(lshape):
    wall(lshape, "East")["Length"] += 0.0005  # within 0.001 document units
    assert validate_room(lshape) == []
    wall(lshape, "East")["Length"] += 0.004  # 4.5 mm: too loose
    assert "don't close" in messages(lshape)


def test_outward_facing_matters(box):
    for w in box["Walls"]["Items"]:
        w["Orientation"] = (w["Orientation"] + 180) % 360
    # Geometrically still a valid box; only the compass-named IDs give it away.
    assert "faces outward" in messages(box)


def test_self_intersecting_walls(lshape):
    # Swap two walls so the chain still closes but crosses itself.
    items = lshape["Walls"]["Items"]
    items[1], items[3] = items[3], items[1]
    lshape["Shape"] = "Irregular"
    assert "cross" in messages(lshape)


def test_duplicate_wall_ids(box):
    wall(box, "West")["ID"] = "North"
    assert "duplicate wall ID" in messages(box)


def test_rectangle_with_wrong_wall_count(lshape):
    lshape["Shape"] = "Rectangle"
    lshape["Dimensions"] = {"Length": 3.6, "Width": 4.1, "Height": 2.6}
    assert "Rectangle needs 4 walls" in messages(lshape)


def test_dimensions_must_match_walls(box):
    box["Dimensions"]["Length"] = 14
    assert "[Width, Length, Width, Length]" in messages(box)


def test_rectangle_walls_start_with_width(box):
    # Width runs along x and is the first edge; swapping Length/Width breaks that.
    d = box["Dimensions"]
    d["Length"], d["Width"] = d["Width"], d["Length"]
    assert "[Width, Length, Width, Length]" in messages(box)


def test_square_needs_equal_sides(box):
    box["Shape"] = "Square"
    assert "Square has Length" in messages(box)


def test_l_shape_needs_one_inside_corner(box):
    box["Shape"] = "L-Shape"
    assert "L-Shape needs" in messages(box)


def test_needs_a_height(lshape):
    del lshape["Dimensions"]
    lshape["Shape"] = "L-Shape"
    assert "no height" in messages(lshape)


def test_wall_heights_can_replace_dimensions(lshape):
    del lshape["Dimensions"]
    for w in lshape["Walls"]["Items"]:
        w["Height"] = 2.6
    assert validate_room(lshape) == []


# --- openings ----------------------------------------------------------------------

def test_unknown_wall_reference(box):
    box["Doors"][0]["WallID"] = "Up"
    assert "unknown wall 'Up'" in messages(box)


def test_door_past_end_of_wall(box):
    box["Doors"][0]["Offset"] = 10
    assert "runs past the end of wall 'South'" in messages(box)


def test_window_above_ceiling(box):
    box["Windows"][0]["Height"] = 5.5
    assert "above wall 'North' height" in messages(box)


def test_window_at_floor(box):
    box["Windows"][0]["SillHeight"] = 0
    assert "starts at the floor" in messages(box)


def test_skylight_skips_wall_checks(box):
    box["Windows"][0].update(Type="Skylight", Offset=50)
    assert validate_room(box) == []


def test_door_on_open_wall(box):
    wall(box, "South")["Open"] = True
    assert "open wall 'South'" in messages(box)


def test_overlapping_door_and_closet(box):
    box["Doors"][0]["WallID"] = "East"
    box["Doors"][0]["Offset"] = 3.5
    assert "overlaps" in messages(box)


def test_transom_above_door_is_fine(box):
    box["Windows"].append(
        {"Type": "Fixed",
         "SillHeight": 6.8, "Width": 2.67, "Height": 1, "WallID": "South", "Offset": 1.5}
    )
    assert validate_room(box) == []


def test_closet_opening_wider_than_interior(box):
    box["Closets"][0]["OpeningWidth"] = 6
    assert "wider than its interior" in messages(box)


def test_off_center_closet_needs_offset(box):
    box["Closets"][0]["OpeningCentered"] = False
    assert "no OpeningOffset" in messages(box)


def test_off_center_closet_opening_fits(box):
    box["Closets"][0].update(OpeningCentered=False, OpeningOffset=0)
    assert validate_room(box) == []
    box["Closets"][0]["OpeningOffset"] = 2
    assert "past the closet's interior edge" in messages(box)


def test_recess_deeper_than_wall(box):
    box["Recesses"][0]["Depth"] = 0.5
    assert "deeper than the wall" in messages(box)


def test_recess_may_use_full_wall_thickness(box):
    box["Recesses"][0]["Depth"] = 0.4  # equals Walls.Defaults.Thickness
    assert validate_room(box) == []


def test_first_wall_starts_at_origin(box):
    plan = build_plan(box["Walls"]["Items"])
    assert plan.points[0] == (0.0, 0.0) and plan.wall_ids[0] == "North"


# --- cli -----------------------------------------------------------------------------

def test_cli_exit_codes(tmp_path, box, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(box))
    bad_room = copy.deepcopy(box)
    bad_room["Doors"][0]["Offset"] = 99
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_room))

    assert main(["validate", str(good)]) == 0
    assert main(["validate", str(good), str(bad)]) == 1
    assert "FAIL" in capsys.readouterr().out
