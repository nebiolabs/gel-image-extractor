# AGENTS.md

Working notes for AI-assisted development on `gel-image-extractor`. This document
captures project scope and collaboration ground rules as they're established.
Update it as understanding evolves — it should stay in sync with what's actually
been discussed, not get ahead of it.

## Project Overview

This project merges two separate internal NEB proposals ("Decodeonauts" hackathon
projects) that both boil down to the same underlying problem: **extracting
quantitative or categorical information from images of electrophoresis gels**,
replacing manual/eyeballed operator judgment with a standardized, reproducible
computational pipeline.

### Sub-project 1 — Protein Purity Quantification (`data/daria_data`)

- Submitter: the submitter ([email removed]), Process Development Scientist II,
  Formulation & Purification Discovery (Process Development).
- Problem: protein purity is currently assessed by eye from a single SDS-PAGE
  (Coomassie-stained) gel lane. Purification handoffs come with quantitative
  targets (e.g. "&gt;95% purity"), but there's no fast in-team way to produce an
  actual number. ImageJ can do it, but requires manual baseline selection that's
  sensitive to pixel-level user error, so it isn't used routinely.
- Observed gel layout: a "Total" (undiluted) sample lane plus a dilution series,
  often run alongside a reference ladder of known-purity standards (e.g. 50 /
  75 / 88 / 94 / 97 / 98 / 99%) — see `03_EcoRI_HF_Lot_15_QC_Report.png` and
  `04_BtgZI_Lot_9___Lot_10_QC_Report.png`. Some example images are raw gel
  photos without that standard ladder (`01_HpyCH4IV...png`, `02_FCE_T7...jpg`).
  QC report gels don't always record which molecular weight ladder was used.
- Goal (per submitter): a fast, reliable, user-friendly way to convert a gel
  image into a quantitative purity value, without depending on another group.

### Sub-project 2 — Activity Gel Extraction (`data/gia_data`)

- Submitters: the submitter ([email removed]); team includes Jacob Miller and
  a team member.
- Problem: restriction enzyme stability is assessed by running digests in
  96-well plate format across many time points and buffer conditions, then
  imaging the resulting gel. An operator currently manually reads every one of
  the 96 lanes at every time point and classifies digestion as **active (1.0)**,
  **partial (0.5)**, or **dead (0)** by comparing band patterns to the ladder.
  Manual scoring is slow and operator-dependent; raw gel images alone are also a
  poor way to communicate the data — it's normally turned into a heatmap
  (well position vs. time).
- Observed example dataset: SfiI digestion of pXba substrate, 1kb+ ladder, at
  two salt conditions (100 mM and 500 mM NaCl) across 12 time points (Day 0
  through Day 42), one 96-well gel image per condition/time point
  (`data/gia_data/attachments/05...26*.tif`). Companion `.txt` files
  (`03_500mMNaClADD_Plate-SfiI.txt`, `04_100mMNaClADD_Plate-SfiI.txt`) contain
  the target output shape: a time (rows) × well (columns) matrix of
  0 / 0.5 / 1.0 scores. Companion PDFs (`01...pdf`, `02...pdf`) show the
  resulting heatmap visualizations for each condition.
- Known challenges called out by the submitter:
  - Many possible substrates and cutting patterns — may need NEBcutter-style
    logic to know what band pattern indicates "active."
  - Gels get imaged under inconsistent settings (zoom, focus, saturation,
    contrast, exposure) — normalization across images is nontrivial.
  - Gels must be sectioned correctly to separate samples from ladders, and
    samples must be correctly mapped back to well position.
  - a reviewer (comment on the proposal) suggested the tool should accept a
    substrate sequence and cross-check against expected fragment size(s),
    ideally reusing the existing NEBcutter tool rather than reimplementing
    fragment analysis.

### How the two projects overlap

Both are fundamentally: **image in → lane/well + ladder detection → band
detection/densitometry → calibrated numeric or categorical output**. Shared
sub-problems likely include image ingestion across formats (TIFF, PNG, JPG),
gel/lane/well segmentation, band detection, ladder-relative calibration, and
robustness to inconsistent imaging conditions. They differ in throughput (one
lane vs. 96 wells at a time) and in what the final output looks like
(continuous % purity vs. a 3-state activity classification feeding a heatmap).
Decided (2026-07-13): **shared architecture, separate workflows** — see Design
Decisions below.

## Design Decisions

Decided as of 2026-07-13, through direct discussion — not to be revisited
without cause:

- **Shared core, separate workflows.** One project/repo with a shared `core`
  (image I/O, ladder detection/calibration, lane/grid segmentation, band/peak
  detection) and two thin, independent workflow modules on top — `purity` and
  `activity` — rather than two separate repos or one forced single pipeline.
  Rough intended layout:
  ```
  gel_extractor/
    core/       # shared image-processing primitives
    purity/     # sub-project 1 workflow
    activity/   # sub-project 2 workflow
  ```
- **Build order: purity first.** It's a single small gel (not a 96-well grid),
  so it validates `core`'s primitives (ladder detection, band/peak finding) on
  the simpler case before tackling grid detection. Activity gel work is
  deferred until purity is working.
- **Activity gel classification will be rule-based/classical, not ML.** The
  existing labeled dataset (`data/gia_data`, ~2,304 well-observations) is
  effectively a single independent experiment (one enzyme, one substrate, one
  pool, one ladder, one imaging setup) resampled over time — not independently
  varying data. A model trained on it would likely learn "what this specific
  SfiI/pXba decay looks like" rather than generalizing across substrates/cut
  patterns/imaging settings, which is exactly the challenge the original
  proposal calls out. Rough estimate: robust generalization would need
  independent variation across dozens of enzyme/substrate combinations, several
  independent pools/preps per condition, and deliberately varied imaging
  settings — likely tens of thousands of independently-varying labeled wells,
  a couple of orders of magnitude beyond what exists today. Revisit if/when
  NEB accumulates that kind of labeled data as a byproduct of normal QC work.
- **Interface: CLI for now**, structured so a UI can be layered on top later
  without a rework (i.e. keep core logic decoupled from any CLI-specific
  concerns).
- **Purity workflow: auto-detect lanes, no manual coordinates.** Lane
  auto-detection for a single small gel is a well-established classical CV
  technique (sum pixel columns → find the valleys between lanes in the
  projection profile → each peak region is a lane), unlike full 96-well grid
  detection, which is much harder. This sidesteps needing CLI-unfriendly manual
  coordinate/bounding-box input. Default CLI behavior: given just a gel image,
  auto-detect all lanes and output a table of lane index → purity %. An
  optional `--lane N` flag scopes the analysis to one auto-detected lane by
  index (not raw pixel coordinates).
- **Core purity computation:** for a given lane, extract the intensity
  profile, subtract baseline, and compute `target_band_area / total_lane_area`
  as purity %. This works off the sample lane alone — it does not depend on a
  known-purity standards ladder being present, since several example gels
  don't have one (`01_HpyCH4IV...png`, `02_FCE_T7...jpg`).
- **Target band identification: MW-based, primary; heuristic only as an
  explicit opt-in escape hatch (decided 2026-07-13).**
  - Primary method: calibrate the ladder lane (known NEB ladder lookup table,
    e.g. P7719, or a user-supplied `--ladder-bands` override), then identify
    the target band as whichever detected band falls nearest the protein's
    known expected MW, within a tolerance (placeholder: ±15-20% of expected
    MW — approximate on purpose, to be tuned empirically against the example
    gels once real code exists, not decided precisely up front).
  - **If the ladder can't be calibrated** (unrecognized ladder and no
    `--ladder-bands` given), the tool refuses to process by default (hard
    error) rather than silently degrading to a less reliable method.
  - **`--allow-heuristic` is an explicit, non-default escape hatch.** It never
    triggers automatically. When passed, it permits falling back to a
    largest/darkest-band heuristic so the user can still get *some* number out
    when calibration isn't possible, but the result must be clearly flagged as
    lower-confidence in the output (e.g. a `confidence: heuristic` vs.
    `confidence: mw-matched` field) — never presented as equivalent to an
    MW-matched result.
  - Embedded purity-standard lanes (50/75/88/94/97/98/99%, when present in a
    gel like the EcoRI-HF/BtgZI QC reports) are an optional secondary
    validation check against the computed number, not required for and not
    part of the core calibration — exact mechanism for using them is a future
    detail, not blocking for MVP.
- **CLI usability requirement: flags must be clearly self-documented in
  `--help`.** Since end users aren't CLI-comfortable (per discussion), every
  flag — `--lane`, `--ladder-bands`, `--allow-heuristic`, and any added later —
  needs a clear, complete description in the tool's own help output, not just
  in external docs. This applies from the first CLI implementation onward,
  not as a later polish pass.

## Open Questions

No open design questions remain as of this update (2026-07-13). The next
things likely to surface are implementation-level details (e.g. tuning the
MW-match tolerance against real example gels) rather than open architecture
questions.

## Data Inventory

- `data/daria_data/project.md` — original proposal text for sub-project 1.
- `data/daria_data/attachments/` — 4 example gel images (PNG/JPG) + 1 PDF of an
  email thread with additional per-protein context (molecular weights, ladder
  used, Benchling links).
- `data/gia_data/project.md` — original proposal text for sub-project 2.
- `data/gia_data/attachments/` — 22 raw 96-well gel scans (TIFF), 2 PDFs of the
  resulting heatmap plots, 2 `.txt` files with the target well-by-time
  activity-score matrices.
- The `data/` directory is gitignored (see below) — it does not live in version
  control.

## Working Agreements

- **No git actions without explicit consent.** Never run `git commit`,
  `git push`, or any other git state-changing command unless the user
  explicitly asks for it in that moment. Read-only git commands (status, log,
  diff) are fine.
- **No unilateral design assumptions.** This is a from-scratch project; decide
  architecture, libraries, algorithms, and scope iteratively and explicitly
  with the user rather than inferring intent. When in doubt, ask.
- **Current phase: design decided, no code written yet.** Architecture and the
  purity-workflow approach have been discussed and decided (see Design
  Decisions above), but implementation hasn't started. Don't start writing the
  actual pipeline code until that's explicitly requested — decisions being
  made doesn't imply a green light to implement.

## Architecture Diagram

`diagrams/program-flow.mmd` (raw Mermaid source) and `diagrams/program-flow.png`
(rendered) capture how the program works. As of this writing there is no code,
so the diagram is a conceptual data-flow sketch of the shared pipeline
described above, not a real class/method or sequence diagram — it should be
replaced with one once actual implementation exists and architecture has been
discussed. Render with: `mmdc -i diagrams/program-flow.mmd -o diagrams/program-flow.png -b white -s 2`.

### "update docs" convention

When the user says **"update docs"**, **"update documentation"**, or anything
clearly equivalent, treat it as shorthand for: refresh persistent memory,
`AGENTS.md`, `README.md`, and the architecture diagram (both
`diagrams/program-flow.mmd` and the re-rendered `diagrams/program-flow.png`)
to reflect what's actually been decided/built since they were last updated.
Re-render the PNG any time the `.mmd` changes — don't let them drift out of
sync.

## Repo Infrastructure Notes

- `.gitignore` was cleaned up from the GitHub-generated Python default (which
  included a lot of irrelevant boilerplate — Django, Flask, Scrapy, Celery,
  RabbitMQ, Streamlit, etc.) down to what's actually relevant: Python
  build/cache artifacts, virtual envs, `.idea/` (JetBrains), standard macOS
  junk files, and the `data/` directory.
- No language/framework/dependency tooling has been chosen yet.
