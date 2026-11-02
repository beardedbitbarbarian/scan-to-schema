# Scan-to-Schema

Turn noisy, phone-quality 3D room scans into clean, structured room descriptions: footprint, ceiling height, and the doors, windows, and openings cut into each wall. Every element carries real dimensions and a confidence score.

## Why

Consumer photogrammetry and LiDAR apps produce room meshes that *look* right but aren't usable for structured work. They are noisy, full of holes, slightly tilted, and furniture is fused into the walls. Anything downstream that needs the actual architecture (rebuilding a clean model, measuring, placing assets, generating VR-ready geometry) has to recover it first.

## How the project measures itself

Reconstruction quality is easy to eyeball and hard to prove, so the project is built around a round-trip benchmark:

```
procedural room ──► ground-truth schema ──► GLB ──► scan simulator ──► noisy point cloud
                          ▲                                                    │
                          └──────── score ◄──── reconstructed schema ◄─────────┘
```

1. A **generator** builds parameterized rooms with known ground truth.
2. A **scan simulator** degrades them with sensor noise, holes, occluding furniture, and drift.
3. The **reconstructor** recovers a schema from the degraded scan.
4. The **evaluator** scores it: dimension error, wall IoU, opening precision/recall, and confidence calibration.

Every change to the reconstructor is measured against the same benchmark, so progress is a number, not an impression.

## Milestones

| # | Milestone | Status |
|---|---|---|
| M0 | Room format (core + full), geometric validator, examples | ✅ done |
| M1 | Synthetic room generator → GLB + ground-truth schema | next |
| M2 | Scan simulator (noise, holes, occlusion, drift) | |
| M3 | Structure extraction: floor/ceiling, axis alignment, wall planes, footprint | |
| M4 | Opening detection from wall-surface gaps | |
| M5 | Evaluation harness, metrics report, CI regression gates | |
| M6 | Robustness: non-rectangular rooms, heavy clutter, confidence scores, real mesh input | |
| M7 | Round-trip rebuild and visual diff overlay | |

Details: [architecture](docs/architecture.md) covers how it's built; [plan and spec](docs/plan.md) covers scope, success criteria, and acceptance criteria per milestone.

## Room format

Output uses the **room-capture** format, so reconstructed rooms feed directly into an existing room-to-GLB generator. A room is a chain of walls, each described by the compass bearing it faces outward and its length. Doors, windows, closets, and recesses attach to a wall by ID and are placed by offset along it.

```json
{
  "ID": "example-bedroom", "EntityType": "Room", "Units": "feet", "Shape": "Rectangle",
  "Dimensions": { "Length": 12, "Width": 10, "Height": 8 },
  "Walls": {
    "Items": [
      { "ID": "South", "Orientation": 180, "Length": 12 },
      { "ID": "East",  "Orientation": 90,  "Length": 10 },
      { "ID": "North", "Orientation": 0,   "Length": 12 },
      { "ID": "West",  "Orientation": 270, "Length": 10 }
    ]
  },
  "Doors": [{ "WallID": "South", "Offset": 1.5, "Width": 2.67, "Height": 6.67 }]
}
```

Two schemas share the same field names:

| Schema | Contains | Produced by |
|---|---|---|
| [`room-core`](src/scan_to_schema/schema/room-core.schema.json) | Geometry only, plus optional per-element `Confidence` | The reconstructor. A scan can recover geometry; it can't reliably recover paint colours or door swing. |
| [`room-capture`](src/scan_to_schema/schema/room-capture.schema.json) | Geometry plus appearance (materials, colours, flooring, ceiling type) | A later enrichment step, or by hand |

A test enforces that core never defines a field that room-capture doesn't, and `to_core()` projects any full document down to core.

**Frame:** right-handed, +Y up, with plan coordinates (x, z) equal to glTF x and z. Walls are listed in positive (counter-clockwise) order in the (x, z) plane, with the room's interior on the left of each wall. The first wall starts at the origin.

### Validation

JSON Schema accepts a "Rectangle" with a single 99 m wall and a door on a wall that doesn't exist. So validation runs a second, geometric pass:

- Walls close into a loop (within scanner-grade tolerance) and form a simple polygon enclosing positive area
- Walls are listed counter-clockwise, since wall order sets polygon edge order downstream (a clockwise listing is detected from the geometry and rejected)
- `Shape` and `Dimensions` agree with the walls (rectangles, squares, L-shapes with exactly one inside corner)
- Compass-named walls ("North") face that way
- Every door, window, closet opening, and recess references a real wall, fits its length and height, and doesn't overlap another (a transom above a door is fine)
- Nothing sits on an open wall; closet openings fit their interior; recesses are no deeper than the wall

## Usage

```bash
pip install -e ".[dev]"
s2s validate examples/*.json
pytest
```

## Scope and data policy

Development and testing use synthetic rooms and openly licensed public scan datasets only. No private scans are committed to this repository.

## License

Copyright (c) 2026 beardedbitbarbarian. All rights reserved. See [LICENSE](LICENSE).
