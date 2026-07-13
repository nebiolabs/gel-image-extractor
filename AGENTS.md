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
- **Purity input is CLI-flag-driven and fully self-contained (confirmed
  2026-07-13).** No external lookups (e.g. a live Benchling API call) — the
  user supplies `--target-mw` and `--ladder` (or `--ladder-bands` for an
  unrecognized ladder) directly on the command line. Realistic burden in the
  common case: 1-2 flags per run, both values the scientist already has to
  know to interpret the gel by eye today, so this isn't new work for them —
  just typed input instead of an inferred/looked-up value. Possible future
  convenience (not yet decided — tracked in `QUESTIONS_FOR_USERS.md`):
  defaulting `--ladder` to a common ladder (e.g. P7719) if it turns out to be
  the de facto standard, dropping the common case to just `--target-mw`.
- **Activity workflow classification will be baseline-relative, not
  substrate-aware, for the core active/partial/dead call (decided
  2026-07-13).** Every example experiment batch (both SfiI and TfiI) includes
  its own "Normalization" image — a per-run baseline showing full activity
  before any time-based decay. The core classifier will compare each well's
  band pattern over time against that same well's own baseline from the
  Normalization image, rather than requiring the substrate sequence or
  expected absolute fragment size(s). This means **no digest-condition data
  needs to be supplied by the user for the core classification** — it's
  fully self-contained from the image batch alone, consistent with the
  purity workflow's CLI-driven philosophy above. the reviewer's original ask
  (accept a substrate sequence, cross-check against NEBcutter-predicted
  fragment sizes) becomes an optional future validation layer, not a
  requirement — revisit if baseline-relative comparison turns out to miss
  real failure modes (tracked in `QUESTIONS_FOR_USERS.md`).
- **Tech stack (decided 2026-07-13):** Python 3.11+; `numpy` + `scipy` +
  `scikit-image` for image processing (lighter/more idiomatic than OpenCV for
  this scale; OpenCV is worth reconsidering specifically for the activity
  workflow's 96-well grid/circle detection later, since its Hough-circle/
  contour tooling is more mature for that particular problem — not a purity
  concern); `argparse` for the CLI, not `typer` — typer's auto-generated
  `--help` is nicer, but doesn't outweigh Jacob's existing familiarity with
  argparse, so we're using argparse with a deliberate commitment to writing
  complete `help=` text for every flag to still meet the CLI-usability
  requirement above; `pyproject.toml` + `uv` for packaging/dependency
  management (chosen over conda since our whole stack is available as PyPI
  wheels and doesn't need conda's non-Python binary management); `pytest` for
  testing.
- **One CLI entry point with subcommands (confirmed 2026-07-13)**, not
  separate binaries per workflow — e.g. `<tool> purity analyze gel.tif`,
  reflecting the actual shared-core/separate-workflows architecture. Exact
  command name not yet finalized (used as a placeholder in discussion so
  far) — pick something reasonable during implementation, low-stakes enough
  not to need a full design discussion.
- **Output formats (decided 2026-07-13): table (default) + optional CSV/JSON,
  additive not mutually exclusive.** Human-readable table always prints to
  stdout by default. `--csv [PATH]`: if a path is given, writes a CSV file
  there (table still also prints to stdout); if no path is given, prints CSV
  to stdout *instead of* the table, so it stays pipeable. `--json [PATH]`
  works the same way. Both flags can be combined (e.g. write both a CSV and a
  JSON file in one run); using both bare (no path) at once is the one
  disallowed combination, since they'd both want stdout. Shared column/field
  set across all three formats: `lane` (index among sample lanes — the
  ladder lane is excluded from result rows, but which lane was used as the
  ladder is noted once, e.g. a header line above the table or a top-level
  JSON field), `purity_percent`, `confidence` (`mw-matched` / `heuristic`),
  `target_mw_expected`, `matched_band_mw`.
- **Target-band edge cases resolved (2026-07-13):**
  - **Doublets/multiple bands within MW tolerance:** sum all bands that fall
    within the tolerance window as the target signal, rather than picking
    only the single nearest band — more scientifically defensible (often
    legitimately the same protein in two forms), and directly motivated by
    the observed doublet in `251017_..._FusionProtein.tif`.
  - **Which detected lane is the ladder:** default to the leftmost detected
    lane (true in every example seen so far), with an override flag for the
    rare exception. This is a real assumption from a small sample size, not
    a robust detection — worth revisiting if it misfires on real data.
  - **Lane vertical bounds (the "total lane area" denominator):** crop starts
    just below the loading well (excluding well/aggregate smear) through the
    dye front. Proposed on paper; needs visual validation against real gels
    once code exists, not just a decision on paper.
- **Validation strategy (decided 2026-07-13): no external numeric ground
  truth exists** for any example gel (the closest is "EcoRI-HF is >95% pure,"
  a threshold, not an exact value) — confirmed there's no better source
  available right now. Primary correctness signal instead: **a dilution
  series of the same sample should yield roughly the same purity % across
  all dilution lanes**, since diluting shouldn't change purity, only total
  signal. This is the bar implementation should be validated against absent
  real ground truth.
- **Modular, swappable architecture (decided 2026-07-13) — explicit
  requirement, not just good practice, because several decisions above
  (baseline correction method, MW tolerance value, the leftmost-lane
  heuristic, doublet-summing logic) are expected to change once we're
  looking at real output instead of paper decisions.** Concretely:
  - **Pipeline of discrete stages**, not one monolithic function: image →
    intensity profile → baseline-corrected profile → detected bands →
    identified target band(s) → purity % → formatted output. Each stage is
    its own function with a clear input/output contract.
  - **Pluggable algorithms behind a common interface (strategy pattern)** for
    pieces expected to be revised: baseline correction
    (`correct_baseline(profile) -> profile`), band/peak detection, and
    ladder-lane identification — swapping the underlying method should not
    require changing calling code.
  - **A structured internal result object** (e.g. a `LaneResult` dataclass)
    rather than raw dicts/tuples threaded through the code. Table/CSV/JSON
    output are three independent formatter functions that all read from the
    same result object — adding a fourth format later means writing one new
    formatter, not touching core logic.
  - **Centralized, named configuration for tunable values** (MW tolerance %,
    etc.) instead of magic numbers scattered through the code, so tuning them
    later is a one-line change.
- **Robust testing is a project requirement, not optional polish (decided
  2026-07-13).** The modular pipeline-of-stages architecture above exists
  partly *to make this practical* — each stage (baseline correction, band
  detection, target identification, output formatting) should have its own
  unit tests, plus integration tests running the full pipeline against the
  real example gel images in `data/`. The dilution-series self-consistency
  check above (same sample, same purity % across dilutions) should be
  encoded as an actual automated test, not just an informal sanity check —
  it's our main correctness signal given the lack of external ground truth.

## Open Questions

No open internal design/architecture questions remain as of this update
(2026-07-13). This section is for questions Jacob and Claude can resolve
through design discussion alone. Questions that need an answer from the
domain-expert end users (the submitter, the submitter, a team member, the reviewer, etc.) instead live in
`QUESTIONS_FOR_USERS.md` — check there for the current accrued list before
assuming a piece of domain knowledge (e.g. "is this ladder the standard one")
rather than guessing.

## Data Inventory

- `data/daria_data/project.md` — original proposal text for sub-project 1.
- `data/daria_data/attachments/` — 4 example gel images (PNG/JPG) + 1 PDF of an
  email thread with additional per-protein context (molecular weights, ladder
  used, Benchling links).
- `data/gia_data/project.md` — original proposal text for sub-project 2.
- `data/gia_data/attachments/` — 22 raw 96-well gel scans (TIFF), 2 PDFs of the
  resulting heatmap plots, 2 `.txt` files with the target well-by-time
  activity-score matrices (SfiI enzyme).
- `data/gia_data/attachments/TfiI/` — added 2026-07-13. A much larger activity
  gel dataset for a different enzyme (TfiI), same 96-well-grid paradigm as
  SfiI (confirmed by inspection — visible activity decay over time, dead
  wells collapsing to a single band). Adds real complexity beyond SfiI:
  - Four independent screen conditions (General, pH, ADD, CAPS Screen), each
    with its own Normalization baseline + multi-timepoint time course.
  - A `Validation/` subfolder that is **structurally different** — dose-
    response by formulation/lot (4 quadrants × 2-fold dilution series), not a
    96-well time-course grid. Will need its own parsing logic or explicit
    scoping-out; not yet decided (see `QUESTIONS_FOR_USERS.md`).
  - Every timepoint has a plain + `_unlabeled` image pair (identical pixel
    content minus the burned-in well-number text/caption) — the unlabeled
    version is likely preferable for automated segmentation.
  - Per-image `.inf` sidecar files (AlphaImager instrument-export XML:
    exposure, gain, binning, creation timestamp, etc.) — potentially useful
    for cross-image normalization, though the samples checked so far show
    identical default-looking values, so it's unconfirmed whether they vary
    meaningfully in practice (see `QUESTIONS_FOR_USERS.md`).
  - Data-quality anomalies noted (not fixed): 2 files with an unexpected
    `.ory` extension (actually annotation-layer XML, not `.inf`-style
    metadata), a stray `Thumbs.db`, an orphaned `Validation/a.tif`/`a.inf`
    pair (appears to duplicate the Validation Normalization image), several
    `_unlabeled` filename typos, and one oddly-sized TIFF in `Validation/`.
- `data/decodeon_gel_images/Protein Purity/` — added 2026-07-13. 11 more
  example purity gels (TIFF/JPG), same ladder + dilution-series layout as
  `daria_data`. All 11 are the "no embedded standards" case (see Sub-project 1
  above) — this batch adds volume/variety to that case but zero new examples
  of the embedded-purity-standard-ladder case. Two images
  (`260407_protein_purity.tif`, `4.16.26 Protein Purity.tif`) are notably
  low-contrast/washed-out — flagged, not yet resolved whether that's typical
  scan quality (see `QUESTIONS_FOR_USERS.md`). One
  (`251017_..._FusionProtein.tif`) shows a doublet band with explicit
  dilution-fold labels burned in — a useful edge case for band-matching logic.
- `data/decodeon_gel_images/Titers/` — added 2026-07-13. 8 images that are a
  **structurally distinct third category**, not a clean fit for either
  existing workflow: inverted-contrast agarose gels showing a 2-fold enzyme
  dilution series plus `+`/`-` control lanes, used to read a potency/dilution
  endpoint (classic restriction-enzyme titer assay) rather than a purity % or
  a per-well active/partial/dead state. Closer in spirit to the activity
  workflow's core band-pattern problem than to purity, but a 1-D dilution
  series rather than a plate grid. Whether this becomes a third workflow is
  an open scope question — see `QUESTIONS_FOR_USERS.md`. One file
  (`4.16.26 Concentrated Stock Titers.tif`) contains two stacked gel images
  in a single file, worth noting for ingestion.
- The `data/` directory is gitignored (see below) — it does not live in version
  control.

## Questions for End Users

`QUESTIONS_FOR_USERS.md` is a running list of functionality questions that
need an answer from the domain-expert end users (the submitter, the submitter, a team member, the reviewer,
etc.) rather than something resolvable through design discussion alone — the
intent is to accrue these and ask them in a batch rather than one at a time.
Add to it as new questions surface; don't resolve them by guessing.

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
- **Document thoroughly enough to explain the whole system to non-implementing
  stakeholders later.** Jacob needs to be able to walk the submitter/the submitter/a team member/
  the reviewer through what was built and why at the end, not just hand them working
  code. Every Design Decision entry should carry its rationale (the "why"), not
  just the decision itself — this applies to future edits too, not only what's
  already written.
- **Robust testing is required, not optional polish.** See the "Modular,
  swappable architecture" and "Robust testing" entries in Design Decisions —
  every pipeline stage needs unit tests, plus integration tests against the
  real example gel images, plus the dilution-series self-consistency check
  encoded as an actual automated test.

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
`AGENTS.md`, `README.md`, `QUESTIONS_FOR_USERS.md`, and the architecture
diagram (both `diagrams/program-flow.mmd` and the re-rendered
`diagrams/program-flow.png`) to reflect what's actually been decided/built
since they were last updated. Re-render the PNG any time the `.mmd` changes —
don't let them drift out of sync.

## Repo Infrastructure Notes

- `.gitignore` was cleaned up from the GitHub-generated Python default (which
  included a lot of irrelevant boilerplate — Django, Flask, Scrapy, Celery,
  RabbitMQ, Streamlit, etc.) down to what's actually relevant: Python
  build/cache artifacts, virtual envs, `.idea/` (JetBrains), standard macOS
  junk files, and the `data/` directory.
- Language/framework/dependency tooling is now decided — see the "Tech stack"
  entry in Design Decisions (Python 3.11+, numpy/scipy/scikit-image,
  argparse, pyproject.toml + uv, pytest).
