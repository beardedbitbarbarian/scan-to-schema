# CLAUDE.md

Context for any Claude Code session working in this repo.

## What this is

Scan-to-Schema reconstructs structured room documents from noisy, phone-quality 3D room scans. Output is in the **room-capture** format, which a separate room-to-GLB generator consumes. Progress is measured by a synthetic round-trip benchmark: generate a room with known ground truth, simulate a degraded scan, reconstruct, score. See README.md for the milestone table; work through milestones in order.

## Conventions (load-bearing; the downstream generator depends on these)

- **Frame:** plan coordinates are (x, z). +X = east, +Z = north, +Y = up. Internally everything is metres; documents may use `inches`, `feet`, `centimeters`, or `meters` (convert with `geometry.UNIT_TO_M`).
- **Orientation** is the compass bearing a wall's *outward* face points: 0 = north, 90 = east.
- **Walls.Items are listed counter-clockwise viewed from above.** Wall list index *is* the polygon edge index downstream. A wall facing bearing b runs in direction (-cos b, sin b). The first wall starts at the origin.
- **Offset** is the distance along a wall's edge from its *start* vertex (`geometry.OFFSET_FROM = "start"`). Openings occupy [Offset, Offset + Width] along the edge.
- Doors start at the floor. Windows have SillHeight > 0. Closet openings are centred in the closet's interior Width unless `OpeningCentered` is false, in which case `OpeningOffset` is required. Recess Depth may equal, but not exceed, wall thickness.
- Interior faces have inward normals (left perpendicular of each CCW edge).

## Two schemas, one vocabulary

- `schema/room-capture.schema.json`: the full format (geometry + appearance). **Vendored from the generator project; do not edit it here.**
- `schema/room-core.schema.json`: geometry only, plus optional `Confidence` (0..1) on the room and each element. The reconstructor emits core.
- Core must never define a field room-capture lacks (except `Confidence`); `test_core_fields_are_a_subset_of_full` enforces this. `to_core()` projects full → core.

## Data policy

- Develop and test **only** on synthetic rooms and openly licensed public datasets.
- Never commit real scans or real room measurements. `.gitignore` blocks common mesh formats; keep it that way.
- Example files use fictional dimensions.

## Commands

```bash
pip install -e ".[dev]"
pytest -q
s2s validate examples/*.json
```

CI (GitHub Actions) runs the tests on Python 3.10 and 3.12 and validates every example.

## Working style

- Every new behaviour gets a test; every geometric rule in `validate.py` has a failing-case test.
- Error messages name the wall or element and say what to fix.
- Keep dependencies light. Heavy geometry libraries (numpy, trimesh, open3d) live in the `geometry` extra and arrive with M1/M2.
- Prefer small, reviewable commits, one milestone step at a time.
