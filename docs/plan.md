# Plan and Specification

What Scan-to-Schema must do, how success is measured, and the order of work. For how it is built, see [architecture.md](architecture.md).

## 1. Problem

Phone-based photogrammetry and LiDAR apps produce room meshes that look right but are structurally unusable: noisy, full of holes, slightly tilted, with furniture fused into the walls. Anything that needs the room's actual architecture has to recover it from that mess first. Scan-to-Schema does the recovery and outputs a validated, structured room document.

## 2. Goals

1. Reconstruct a single room's **footprint, ceiling height, doors, windows, closets, and recesses** from a phone-quality scan.
2. Emit a valid **room-core** document with real dimensions and a **calibrated confidence** per element.
3. Prove accuracy with a **reproducible benchmark**, so every change shows up as a number.
4. Stay **lightweight**: pure Python, installs with pip, runs on a laptop CPU.

## 3. Non-goals (v1)

| Out of scope | Reason |
|---|---|
| Multiple rooms or whole floors | Single-room accuracy first; multi-room is assembly, a separate problem |
| Sloped, vaulted, or multi-level ceilings | The output format's geometry assumes flat ceilings |
| Curved walls, arched openings | Not representable in the output format |
| Furniture recognition | Furniture is clutter to be ignored, not an output |
| Appearance (materials, colours, flooring) | Not reliably recoverable from geometry; core format omits it |
| Stairs | Requires multi-level support |
| True compass bearings without a supplied heading | A scan has no compass (architecture §2) |
| Real-time or on-device processing | Batch, offline tool |

## 4. Inputs and outputs

**Inputs**

- Point clouds (`.ply`) or meshes (`.obj`, `.glb`) of a single room interior, as exported by common phone scanning apps
- Any consistent unit, with the up axis unknown and the floor not necessarily at zero
- Optional: a heading (to produce true bearings); capture poses (improves opening evidence)

**Output**

- One room-core JSON document that passes `s2s validate`, with `Confidence` on the room, every wall, and every element
- Optional diagnostics JSON (stage timings, counts, intermediate fits)

**Behaviour on failure**

- If no valid room can be built, the tool exits non-zero with a reason. It never writes an invalid document.

## 5. Benchmark tiers

Difficulty is defined by the room and the scan together. Profiles are frozen once published (architecture §5.3).

| Tier | Rooms | Scan profile | Clutter |
|---|---|---|---|
| Easy | Rectangles and squares; 1 door, 0–2 windows | `clean` → `phone` | none |
| Medium | Plus L-shapes and notches; closets; 2–4 windows | `phone` | moderate, some against walls |
| Hard | Plus U/T and irregular rectilinear; recesses; adjacent and stacked elements | `phone_cluttered`, `phone_drift` | heavy, occluding openings |

## 6. Success criteria

Initial targets, applied to the full benchmark suite. They will be recalibrated once M5 produces the first real numbers; any change is recorded here with the reason.

| Metric | Easy | Medium | Hard |
|---|---|---|---|
| Footprint IoU | ≥ 0.97 | ≥ 0.93 | ≥ 0.85 |
| Mean corner error | ≤ 3 cm | ≤ 6 cm | ≤ 12 cm |
| Wall count exact | ≥ 95% of rooms | ≥ 85% | ≥ 70% |
| Ceiling height error | ≤ 2 cm | ≤ 3 cm | ≤ 5 cm |
| Door + window F1 | ≥ 0.95 | ≥ 0.85 | ≥ 0.70 |
| Closet + recess F1 | n/a | ≥ 0.75 | ≥ 0.60 |
| Element dimension error (matched) | ≤ 3 cm | ≤ 5 cm | ≤ 8 cm |
| Confidence ECE | | ≤ 0.10 overall | |
| Runtime per room | | ≤ 60 s on a laptop CPU | |

## 7. Milestones

Every milestone ends with tests passing, CI green, and the project in a usable state. Sizes are relative (S < M < L).

### M0: Room format and validator ✅

Done. room-capture vendored, room-core defined, geometric validator, CLI, examples, CI.

### M1: Synthetic room generator (M)

**Scope:** `RoomParams`, tiered sampler, ground-truth document builder, labelled mesh builder, GLB export. Clutter boxes included.

**Deliverables**

- `s2s generate --tier medium --seed 7 --out room.json --mesh room.glb`
- Shape families: rectangle, square, L, U, T, notched
- Elements: doors, windows, closets, recesses, with realistic residential sizes

**Acceptance**

- 1,000 sampled rooms across all tiers: every document passes `validate_room()`
- Every mesh is closed from the inside: rays cast from interior points never escape (they hit room surfaces or the exterior backdrop)
- Every triangle carries a label; every hole in a wall face corresponds to exactly one element
- Same seed gives byte-identical document and mesh
- GLB opens correctly in Blender (manual check, screenshot in the PR)

### M2: Scan simulator (M)

**Scope:** trajectory generation, ray casting, degradation effects, named profiles.

**Deliverables**

- `s2s scan room.glb --profile phone --seed 7 --out scan.ply`
- Profiles: `clean`, `phone`, `phone_cluttered`, `phone_drift`

**Acceptance**

- Per-point labels match the triangle hit (checked on the `clean` profile)
- Each degradation effect has a test showing it changes the cloud as specified (noise σ measured on a flat wall within 10% of target; dropout rate within tolerance; drift grows with trajectory length)
- Point density and noise of the `phone` profile are documented and plausible against published phone-scanner characteristics
- Deterministic per seed

### M3: Structure extraction (L)

**Scope:** preprocess, frame (up axis, floor, ceiling, Manhattan), wall lines, footprint, export of walls and height (no elements yet).

**Deliverables**

- `s2s reconstruct scan.ply --out room.json`
- Stage diagnostics in `--diagnostics` output

**Acceptance**

- `clean` profile, easy and medium rooms: footprint IoU ≥ 0.98, height error ≤ 1 cm
- `phone` profile: meets the Easy-tier footprint, corner, wall-count, and height targets from §6
- Output always validates, or the tool exits non-zero with a reason
- Architecture test passes: `recon` imports nothing from `synth` or `eval`

**Main risk:** walls largely hidden by furniture. Mitigation is the floor-region footprint (architecture D7); measure it explicitly on cluttered rooms before moving on.

### M4: Element detection (L)

**Scope:** per-wall evidence maps, candidate extraction, classification (door, window, closet, recess, occlusion, scan hole), measurement, export.

**Acceptance**

- `clean` profile: element F1 ≥ 0.98, dimensions within 2 cm
- `phone` profile: meets the Easy-tier element targets from §6
- Dedicated ambiguity tests: a furniture-shadowed blank wall produces no element; a doorway partly blocked by furniture is still found; a scan hole on a blank wall produces no element

**Main risk:** doorway vs scan hole ambiguity. Mitigation: positive evidence required (architecture D8). Track occlusion and scan-hole false positives as their own metric from day one.

### M5: Evaluation harness (M)

**Scope:** alignment, matching, metrics, report, suite definition, baseline, CI gate.

**Deliverables**

- `s2s eval truth.json recon.json` for a single room
- `s2s bench --suite ci` and `--suite full`, writing `report.json` and `report.md`
- Committed baseline; CI fails on regression beyond tolerance

**Acceptance**

- Metric unit tests against hand-computed cases (including a reconstruction rotated 90° and translated, which must score as perfect)
- CI suite completes in under 5 minutes
- First full-suite report committed; §6 targets recalibrated if needed, with reasons

M5 can start alongside M3. A minimal footprint-and-height evaluator is needed by the M3 acceptance criteria anyway.

### M6: Robustness and real input (L)

**Scope:** non-Manhattan fallback, heavy clutter, drift handling, calibrated confidence, real mesh input (`.obj`/`.glb` → cloud).

**Acceptance**

- Medium and Hard targets from §6 met, or the gap documented with failure analysis
- Confidence ECE ≤ 0.10 on the full suite
- Real-scan input path works end to end on openly licensed sample data, with qualitative results documented (real scans have no ground truth, so these are inspected, not scored)

### M7: Round-trip and visual diff (S)

**Scope:** rebuild a mesh from the reconstructed document and overlay it on the input scan, colour-coded by error.

**Deliverables**

- `s2s diff scan.ply recon.json --out diff.glb`
- Per-wall and per-element error annotations in the overlay

**Acceptance**

- Overlay generated for every room in a benchmark run on request
- Manual review checklist in the docs

## 8. Sequencing

```
M0 ──► M1 ──► M2 ──► M3 ──► M4 ──► M6 ──► M7
                      │      ▲
                      └► M5 ─┘   (M5 starts with M3; gate is live before M4)
```

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Doorway vs scan hole ambiguity limits element accuracy | High | High | Positive evidence required; separate false-positive metric; ambiguity tests in M4 |
| Synthetic-to-real gap: benchmark looks great, real scans don't | Medium | High | Profiles modelled on real phone-scan characteristics; real-input checks in M6; widen profiles when real failures show up |
| Furniture hides most of a wall | Medium | Medium | Floor-region footprint; clutter-heavy rooms in the Hard tier |
| Drift bends long walls | Medium | Medium | Drift profile in benchmark; robust line fitting; per-wall residual feeds confidence |
| Non-Manhattan rooms | Low | Medium | Unconstrained fallback; flagged with lower confidence |
| Optional heavy dependency (Open3D) lags Python releases | Medium | Low | Core path is numpy/scipy only; Open3D behind an extra |
| Benchmark overfitting: tuning to the suite rather than the problem | Medium | Medium | Separate held-out seeds that are never used for tuning |

## 10. Open questions

1. ~~**Rectangle `Length` vs `Width`.**~~ Resolved: `Width` is the x extent, `Length` the z extent, and rectangle walls are listed `[Width, Length, Width, Length]` starting from the edge along +x. Enforced by the validator.
2. **Open walls.** When should a missing wall be exported as `Open: true` rather than a very wide opening? Proposed rule: no wall surface along more than 90% of the edge, with floor continuing beyond it.
3. **Capture poses.** Some apps export camera poses. If available, they make the `seen` evidence (architecture §6.4) exact. Worth supporting as an optional input in M6?
4. **Held-out set size.** How many held-out seeds per tier are enough to make the hold-out meaningful without slowing the full suite?
