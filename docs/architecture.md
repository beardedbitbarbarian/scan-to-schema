# Architecture

This document describes how Scan-to-Schema is built: the pipeline stages, the module layout, the data contracts between stages, the algorithms each stage uses, and the design decisions behind them. For scope, milestones, and acceptance criteria, see [plan.md](plan.md).

## 1. System overview

Scan-to-Schema has two halves that share one set of data contracts:

- **The reconstructor** turns a scan (point cloud or mesh) into a `room-core` document.
- **The benchmark** manufactures scans with known answers, runs the reconstructor on them, and scores the results.

```
                        ┌──────────────────────── benchmark ────────────────────────┐
                        │                                                           │
  RoomParams ──► synth.generator ──► ground-truth room-core ─────────────┐          │
                        │                 │                              │          │
                        │                 ▼                              ▼          │
                        │           synth.mesh ──► labelled mesh   eval.score ──► report
                        │                 │                              ▲          │
                        │                 ▼                              │          │
                        │           synth.scanner ──► labelled cloud     │          │
                        └─────────────────┬──────────────────────────────┼──────────┘
                                          │                              │
  real scan (.ply/.obj/.glb) ─────────────┤                              │
                                          ▼                              │
                               ┌──────── recon ────────┐                 │
                               │ preprocess            │                 │
                               │ frame (up, floor, Manhattan)            │
                               │ walls → footprint     │                 │
                               │ openings              │                 │
                               │ confidence            │                 │
                               │ export ───────────────┼──► room-core ───┘
                               └───────────────────────┘
```

The reconstructor never sees ground truth or point labels. Labels exist only so the evaluator can score intermediate stages (for example, how many wall points ended up in a wall plane).

## 2. Conventions

These apply everywhere and match the `room-capture` format consumed downstream.

| Topic | Convention |
|---|---|
| Units | Metres internally. Documents may use any `room-capture` unit; convert on load and on export. |
| 3D frame | **+Y up**, +X east, +Z north, matching the downstream format. Floor at y = 0 after framing. This axis set is left-handed, so anything written to glTF (which is right-handed) maps north to −Z; see §5.2. |
| Plan frame | 2D points are (x, z). |
| Wall order | `Walls.Items` counter-clockwise viewed from above; wall *i* is polygon edge *i*. The first wall starts at the origin. |
| Orientation | Compass bearing of the wall's outward face. A wall facing bearing *b* runs in direction (−cos *b*, sin *b*). |
| Offset | Distance along a wall from its start vertex to the near edge of the element. |
| Normals | Interior surfaces face into the room. |
| Randomness | Every random choice takes an explicit `numpy.random.Generator`. Same seed, same output, on every platform. |

### North is not observable

A scan has no compass. After Manhattan alignment the reconstructor knows the room's axes only up to a 90° rotation. The exporter therefore picks a deterministic frame: the dominant wall axis becomes the x axis, and the first wall is the one starting at the footprint's minimum-(z, x) corner. If a caller supplies a heading (for example, from phone metadata), the exporter rotates into true bearings instead.

Consequence for evaluation: scores are computed after aligning the reconstruction to ground truth (section 7.1), never in raw coordinates.

## 3. Package layout

```
src/scan_to_schema/
├── schema/              room-capture (vendored, read-only) and room-core JSON Schemas
├── validate.py          structural + geometric validation          (M0, done)
├── geometry.py          wall chain ⇄ plan polygon, unit handling     (M0, done)
├── types.py             dataclasses shared between stages
├── io.py                load/save clouds, meshes, documents
├── synth/
│   ├── params.py        RoomParams and the random room sampler
│   ├── generator.py     RoomParams → ground-truth room-core           (M1)
│   ├── mesh.py          room-core → labelled triangle mesh, GLB       (M1)
│   └── scanner.py       labelled mesh → degraded labelled cloud       (M2)
├── recon/
│   ├── preprocess.py    downsample, outliers, normals                 (M3)
│   ├── frame.py         up axis, floor/ceiling, Manhattan alignment   (M3)
│   ├── walls.py         wall planes → 2D lines → footprint            (M3)
│   ├── openings.py      per-wall evidence maps → elements            (M4)
│   ├── confidence.py    evidence features → calibrated scores         (M6)
│   └── export.py        recon result → room-core document             (M3)
├── eval/
│   ├── align.py         reconstruction-to-truth alignment             (M5)
│   ├── match.py         wall and element matching                     (M5)
│   ├── metrics.py       scores                                        (M5)
│   └── bench.py         suite runner, report, regression gate         (M5)
├── viz/
│   └── diff.py          rebuild + overlay for visual review           (M7)
└── cli.py               s2s validate | generate | scan | reconstruct | eval | bench
```

Dependency direction is strictly downward: `recon` may import `geometry`, `types`, `io`; it must never import `synth` or `eval`. An architecture test, added with `recon` in M3, enforces this so the reconstructor cannot accidentally peek at ground truth. Modules marked with a milestone don't exist yet; this layout is the target.

## 4. Data contracts

### 4.1 Documents

- **room-core** is the reconstructor's only output and the generator's ground-truth output. Ground truth omits `Confidence`; reconstructions include it.
- **room-capture** is accepted as input anywhere a document is read. `to_core()` projects it down.
- Every document written by any stage passes `validate_room()`. Stages that cannot produce a valid room raise rather than emit an invalid one.

### 4.2 In-memory types (`types.py`)

```python
@dataclass(frozen=True)
class PointCloud:
    points: np.ndarray             # (N, 3) float64, metres, Y up
    normals: np.ndarray | None     # (N, 3) unit vectors, or None
    labels: np.ndarray | None      # (N,) int32 semantic label ids; synthetic only
    instance: np.ndarray | None    # (N,) int32 element ids (which wall / opening); synthetic only

@dataclass(frozen=True)
class Plane:
    normal: np.ndarray             # (3,) unit
    d: float                       # n·p + d = 0
    inliers: np.ndarray            # indices into the cloud
    residual: float                # RMS point-to-plane distance

@dataclass(frozen=True)
class WallLine:
    direction: np.ndarray          # (2,) unit, in plan frame
    offset: float                  # signed distance from origin
    support: float                 # metres of wall covered by inlier points
    inlier_rms: float

@dataclass(frozen=True)
class ReconResult:
    floor_y: float
    ceiling_y: float
    footprint: list[tuple[float, float]]   # CCW, metres
    walls: list[WallEvidence]              # one per footprint edge
    elements: list[ElementEvidence]        # doors, windows, closets, recesses
    diagnostics: dict[str, Any]            # per-stage timings and counts
```

`WallEvidence` and `ElementEvidence` carry the geometric result plus the raw features later turned into confidence scores (coverage, residuals, rectangularity, and so on).

### 4.3 Semantic labels

| Id | Label | Notes |
|---|---|---|
| 0 | unknown | real scans; outliers |
| 1 | floor | |
| 2 | ceiling | |
| 3 | wall | instance id = wall index |
| 4 | reveal | jamb, header, or sill surface inside an opening |
| 5 | closet_interior | instance id = closet index |
| 6 | recess_interior | instance id = recess index |
| 7 | clutter | furniture boxes; non-structural |
| 8 | exterior | geometry seen through an opening |

### 4.4 Files

| Artifact | Format | Notes |
|---|---|---|
| Room document | `.json` | room-core or room-capture |
| Mesh | `.glb` | per-triangle label and instance stored as glTF extras |
| Point cloud | `.ply` | `label` and `instance` as extra vertex properties |
| Bench report | `.json` + `.md` | machine-readable plus a summary table |

Generated artifacts are never committed. Tests generate small fixtures at run time from fixed seeds.

## 5. Synthetic data (`synth/`)

### 5.1 Room parameters and generator (M1)

`RoomParams` describes a room procedurally: shape family (rectangle, square, L, U, T, notched, irregular rectilinear), side lengths, ceiling height, wall thickness, and lists of element specs. `sample_params(rng, tier)` draws realistic values (residential door and window sizes, sill heights, closet depths) with per-tier difficulty.

`generator.build(params) → room-core` lays out the footprint, places elements with rejection sampling until the document passes `validate_room()`, and returns the ground truth. Element placement respects minimum clearances from corners and between neighbours.

### 5.2 Mesh builder (M1)

`mesh.build(doc, clutter=None) → LabelledMesh` produces a closed, inward-facing interior mesh:

- Floor and ceiling caps, triangulated from the footprint polygon (ear clipping; footprints are simple).
- One inner face per wall, with rectangular holes cut for every element. Each face is built as a 2D (u, v) rectangle minus hole rectangles, triangulated, then lifted into 3D along the wall.
- Reveal quads lining every hole, to the wall thickness.
- Closet and recess interior boxes behind their holes.
- An exterior backdrop (large box) so rays through windows and doors hit something labelled `exterior`.
- Optional clutter: axis-aligned and rotated boxes (beds, dressers, shelves) placed against and away from walls, labelled `clutter`.

**GLB export** writes glTF coordinates as (x, y, −z_north). glTF is right-handed with +Y up, so writing plan z directly would produce a mirror image of the room. A test checks the mapping: in the exported file, a window on the east wall must lie on +X, and the north wall must lie on −Z.

This builder is deliberately independent of any downstream generator. It only needs to be geometrically correct and labelled, not production quality.

### 5.3 Scan simulator (M2)

`scanner.scan(mesh, profile, rng) → PointCloud` emulates a handheld capture:

1. **Trajectory.** A path through the room interior at hand height (1.2–1.6 m) with yaw and pitch sweeps, sampled as a sequence of camera poses.
2. **Ray casting.** For each pose, cast rays over a field of view onto the mesh (`trimesh` ray intersector; Embree when available). Record hit point, triangle label, and instance.
3. **Degradation**, each controlled by the profile:

| Effect | Model |
|---|---|
| Range noise | Gaussian along the ray, σ = σ₀ + k·depth |
| Surface-dependent dropout | Lower return probability at grazing angles and on `exterior` |
| Holes | Random surface patches with zero returns |
| Outliers | Small fraction of points displaced randomly |
| Drift | Pose error accumulated as a random walk along the trajectory |
| Density variation | Non-uniform pose spacing |

Profiles are named (`clean`, `phone`, `phone_cluttered`, `phone_drift`) and frozen once the benchmark is defined, so scores stay comparable over time. Changing a profile means creating a new profile.

## 6. Reconstruction (`recon/`)

Each stage is a pure function from the previous stage's output plus a parameters object, so stages are testable in isolation against synthetic ground truth.

### 6.1 Preprocess (M3)

Voxel downsample (default 2 cm), statistical outlier removal, normal estimation by local PCA over k nearest neighbours, with normals oriented toward the cloud centroid (interior scans see surfaces from inside).

### 6.2 Frame (M3)

1. **Up axis.** Cluster normals; the up axis is the cluster direction with the most support among near-horizontal surfaces (floor and ceiling together). Rotate so it becomes +Y.
2. **Floor and ceiling.** Histogram point heights. The floor is the lowest dense peak, the ceiling the highest. Refine each with a horizontal plane fit. Translate so the floor is y = 0. Height = ceiling − floor.
3. **Manhattan alignment.** Take normals of near-vertical points, compute their yaw angles mod 90°, and find the dominant angle with a circular histogram plus mean-shift refinement. Rotate about Y so walls align with x and z. A room whose yaw histogram has no clear peak is flagged non-Manhattan and handled by the general path in 6.3.

### 6.3 Walls and footprint (M3)

1. **Wall band.** Keep points with 0.3 m < y < height − 0.3 m and near-vertical normals. This drops most floor clutter, ceiling fixtures, and reveal headers.
2. **Wall lines.** Project the band to the plan (x, z). Fit lines with sequential RANSAC constrained to the Manhattan directions (with an unconstrained fallback for non-Manhattan rooms), then refine each line with least squares on its inliers. Merge collinear lines whose offsets differ by less than the wall-detection tolerance.
3. **Interior region.** Rasterise floor points into a 2D occupancy grid (default 5 cm). Close small holes morphologically. The largest connected component is the room interior.
4. **Footprint.** Trace the interior region's boundary, then snap each boundary run to the nearest supporting wall line. Consecutive snapped lines intersect to give corners. Remove edges shorter than the minimum wall length and merge adjacent collinear edges.
5. **Ordering.** Orient the polygon counter-clockwise, rotate the list so the first vertex is the minimum-(z, x) corner, and translate it to the origin.

Using the floor region as the primary footprint signal, rather than wall lines alone, makes the result robust to walls hidden behind furniture: floor under the furniture is often occluded too, but morphological closing and wall-line snapping recover the boundary.

### 6.4 Openings (M4)

Openings are found as missing wall, then justified with evidence, because the core ambiguity of the project is that **a doorway and a scan hole are both missing wall**.

For each footprint wall:

1. **Evidence map.** Build a 2D grid over the wall's (u, v) extent (u along the wall from its start, v up from the floor; default 2 cm cells). Each cell records:
   - `on`: points within ±tolerance of the wall plane
   - `front`: points between the scanner side and the wall that project into the cell (occluders)
   - `behind`: points beyond the wall plane (seen through it)
   - `seen`: whether any ray plausibly reached the cell (free-space evidence, when poses are known; otherwise approximated from `front` density)
2. **Candidates.** Connected regions of cells with no `on` evidence, filtered by size, then fitted with the best axis-aligned rectangle.
3. **Classification.** Each candidate gets a feature vector and a rule-based classifier (later tunable from benchmark data):

| Evidence | Suggests |
|---|---|
| Strong `behind`, rectangle edges match reveal planes perpendicular to the wall | real opening |
| Touches the floor, height 1.9–2.5 m | door |
| Raised bottom edge, sensible sill height | window |
| `behind` points form a shallow box with a back plane and side planes | closet (depth from back plane) |
| Back plane within wall thickness, no through-view | recess |
| Region coincides with `front` occluder shadow, no `behind`, ragged edges | occlusion, rejected |
| Irregular shape, no reveals, no `behind` | scan hole, rejected |

4. **Measurement.** Offset, width, height, and sill come from the fitted rectangle, refined to reveal planes when present. Closet depth comes from the back plane.

### 6.5 Confidence (M6)

Every wall and element carries evidence features (support ratio, inlier RMS, coverage, rectangularity, reveal score, `behind` density). A per-element-type model maps features to a probability of being correct. It starts as a hand-weighted logistic function and is then calibrated on benchmark results (Platt scaling or isotonic regression), with the fitted parameters versioned in the repo. The room's overall confidence is derived from its elements' scores.

### 6.6 Export (M3, extended in M4)

`export.to_document(result) → room-core`:

- Walls in CCW order with `Orientation` from each edge's outward normal and `Length` from the edge.
- **Exact closure.** The downstream generator rejects chains that miss closing by more than 0.001 document units, so lengths are derived from rounded vertex coordinates rather than rounded independently. For Manhattan rooms this closes exactly; for non-Manhattan rooms the final wall's length and orientation absorb any residual.
- **Rectangles.** The downstream generator builds rectangles from `Dimensions` alone, as (0,0) → (Width,0) → (Width,Length) → (0,Length). `Width` is the x extent, `Length` the z extent, and `Walls.Items` are listed starting with the edge along +x: `[Width, Length, Width, Length]`.
- Wall IDs from bearings (`North`, `East`, …), suffixed when duplicated (`North-2`).
- `Shape` classified from turn angles (Rectangle, Square, L-Shape, else Irregular), with `Dimensions` filled where the format requires it.
- Elements with `WallID` and `Offset` measured from each wall's start vertex.
- A wall whose evidence says "no surface, but floor continues" is exported with `Open: true`.
- The output is passed through `validate_room()` before return. A failure there is a reconstructor bug, not a user error, and raises.

## 7. Evaluation (`eval/`)

### 7.1 Alignment

Reconstructions live in an arbitrary frame (section 2), so before scoring, the evaluator finds the rigid 2D transform that best aligns the reconstructed footprint to the truth. It tries the four 90° rotations (plus a mirror check, which should never win and is reported if it does), solves translation by centroid alignment refined with point-to-segment ICP on the footprint boundaries, and keeps the transform with the highest footprint IoU.

### 7.2 Matching

- **Walls:** Hungarian assignment on a cost combining midpoint distance and angle difference, with a gating threshold. Unmatched walls count as misses or false walls.
- **Elements:** matched within corresponding walls by rectangle IoU in (u, v), threshold 0.5, again with Hungarian assignment.

### 7.3 Metrics

| Area | Metric |
|---|---|
| Footprint | 2D IoU; mean corner error (m); wall count error |
| Height | absolute error (m) |
| Walls | length error (m) on matched walls; precision / recall |
| Elements | precision / recall / F1 per kind; kind accuracy on matches; offset, width, height, sill, depth errors (m) |
| Confidence | expected calibration error; AUROC of confidence vs correctness |
| Stages (synthetic only) | wall-point recall in fitted planes; floor/ceiling height error before export |
| Cost | wall-clock time per room |

### 7.4 Benchmark suite and gate

A suite is a list of (tier, seed, scan profile) entries; three tiers (easy, medium, hard, defined in plan.md) with a fixed number of seeded rooms each. `s2s bench` runs the suite, writes the report, and compares it to a committed baseline. CI fails if any gated metric regresses beyond its tolerance. Improving a metric means updating the baseline in the same commit, which makes every accuracy change visible in review.

The CI suite is a small, fast subset; the full suite runs locally.

## 8. Dependencies

| Package | Used for | Stage |
|---|---|---|
| jsonschema | document validation | all |
| numpy | everything numeric | M1+ |
| trimesh | mesh I/O, GLB, ray casting | M1–M2 |
| shapely | polygon IoU, boundary operations | M3+ |
| scipy | KD-trees, Hungarian assignment, morphology | M3+ |
| open3d *(optional)* | mesh-to-cloud conversion and Poisson meshing for real-scan input | M6 |

Core algorithms (RANSAC, histograms, evidence maps) are written against numpy and scipy, so the package installs cleanly on every supported Python version. Open3D stays behind an optional extra because its releases tend to lag new Python versions.

## 9. Testing strategy

- **Unit tests** per function, mostly on tiny hand-built inputs with exact answers.
- **Stage tests** on synthetic rooms: each recon stage is checked against the generator's ground truth with tolerances (for example, floor height within 1 cm on the `clean` profile).
- **Property tests:** generator output always validates; mesh is watertight from the inside; scanning the `clean` profile and reconstructing reproduces the room within tight tolerances.
- **Determinism tests:** same seed, byte-identical documents.
- **Architecture test:** `recon` imports nothing from `synth` or `eval`.
- **Benchmark gate** in CI, as described in 7.4.

## 10. Design decisions

| # | Decision | Why | Alternative rejected |
|---|---|---|---|
| D1 | Emit `room-core`, a strict projection of `room-capture` | Output is directly usable downstream; no adapter layer | A bespoke output format |
| D2 | Y-up, metres, CCW walls internally | Matches the downstream format, so export is a relabelling, not a transform | Z-up internally |
| D3 | Treat north as unobservable; evaluate after alignment | A scan has no compass; pretending otherwise produces fake errors | Requiring heading input |
| D4 | Synthetic-first development with a frozen benchmark | Measurable progress, no data-rights issues | Developing on real scans |
| D5 | Own simple mesh builder for synthesis | Keeps the project self-contained and independent of any downstream implementation | Reusing an external generator |
| D6 | numpy/scipy core, Open3D optional | Clean installs and CI across Python versions | Open3D throughout |
| D7 | Floor region as primary footprint signal, snapped to wall lines | Robust to furniture hiding walls | Wall lines alone |
| D8 | Openings require positive evidence, not just absence | Absence alone cannot separate doorways from scan holes | Gap-only detection |
| D9 | Confidence calibrated on benchmark results | Scores mean something measurable | Uncalibrated heuristics |
| D10 | Regression gate with a committed baseline | Accuracy changes are explicit and reviewed | Manual comparison |
