# CLAUDE.md

Context for any Claude Code session working in this repo.

## What this is

Scan-to-Schema reconstructs structured room documents from noisy, phone-quality 3D room scans. Output is **room-core**, a geometry-only JSON format this project defines (`schema/room-core.schema.json`). Progress is measured by a synthetic round-trip benchmark: generate a room with known ground truth, simulate a degraded scan, reconstruct, score. Before starting work, read docs/plan.md (scope, success criteria, per-milestone acceptance criteria) and docs/architecture.md (module layout, data contracts, algorithms, design decisions D1–D11). Work through milestones in order; a milestone is done when its acceptance criteria pass. If a change contradicts a design decision, update the decision table in the same commit and say why.

## Format conventions (load-bearing; room-core is a stable contract)

- **Frame:** right-handed, +Y up, **+X east, −Z north** (+Z south). Plan coordinates (x, z) are the glTF x and z axes. Internally everything is metres; documents may use `inches`, `feet`, `centimeters`, or `meters` (convert with `geometry.UNIT_TO_M`).
- **Orientation** is the compass bearing a wall's *outward* face points: 0 = north (−z), 90 = east (+x).
- **Walls.Items are listed in positive order in the (x, z) plane** (positive signed area): clockwise on a north-up map, outward bearings increasing N → E → S → W. Wall list index *is* the polygon edge index. A wall facing bearing b runs in direction (cos b, sin b). The first wall starts at the origin.
- **Offset** is the distance along a wall's edge from its *start* vertex (`geometry.OFFSET_FROM = "start"`). Openings occupy [Offset, Offset + Width] along the edge.
- **Rectangles/Squares** are defined by `Dimensions`: `Width` = x extent, `Length` = z extent, walls listed `[Width, Length, Width, Length]` facing `[0, 90, 180, 270]`, starting with the north wall (the edge along +x).
- **Closure:** wall chains must close within 0.001 document units. Derive lengths from rounded vertices; never round lengths independently.
- **glTF export** writes plan coordinates unchanged (plan x, y, z = glTF x, y, z). Never negate a single axis; that mirrors the room.
- Doors start at the floor. Windows have SillHeight > 0. Closet openings are centred in the closet's interior Width unless `OpeningCentered` is false, in which case `OpeningOffset` is required. Recess Depth may equal, but not exceed, wall thickness.
- Interior faces have inward normals (the (x, z) left perpendicular of each wall's run).

## The format

- `schema/room-core.schema.json` is the only schema here: geometry only, plus optional `Confidence` (0..1) on the room and each element.
- It is a frozen v0.1 contract. Don't rename or remove fields, or change their meaning, without a version bump and a note in docs/architecture.md.
- **Growth rule (D11):** room-core only grows with things a scan can *measure* (dimensions, captured colour, floor class, trim geometry). Styling choices (materials, fixture models, door swing defaults) never go in; consumers map room-core into their own formats, and that mapping lives with them. New fields are additive and bump the minor version. v0.1 is geometry only; measured appearance is M8.

## Data policy

- Develop and test **only** on synthetic rooms and openly licensed public datasets.
- Never commit real scans or real room measurements. `.gitignore` blocks common mesh formats; keep it that way.
- Example files use fictional dimensions.
- Keep committed files self-contained: no references to other projects, people, real places, local machine paths, or specific downstream tools or formats. room-core is the whole interface.
- This is a sole-author project; don't add contributors or vendor third-party code without asking.

## Commands

Use Python 3.10–3.12 (matches CI; heavier geometry dependencies lag newer releases).

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
