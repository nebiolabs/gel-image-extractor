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

- Submitter: a Process Development Scientist II, Formulation & Purification
  Discovery (Process Development).
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

- Submitters: a Process Development team; the working team includes Jacob
  Miller.
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
  - A reviewer (comment on the proposal) suggested the tool should accept a
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
  Actual layout (as implemented):
  ```
  src/gel_extractor/
    core/       # shared image-processing primitives
    purity/     # sub-project 1 workflow (implemented)
    activity/   # sub-project 2 workflow (not yet built)
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
  - **Reconfirmed 2026-07-13, after end-user input on current qualitative
    practice:** end users described their current manual method as
    eyeballing the contaminant "load" in a lane and bracketing it against
    other reference lanes (i.e., something closer to the deferred
    embedded-standards-ladder idea than to a direct single-lane ratio). We
    discussed this explicitly and **decided to keep the single-lane
    densitometric ratio as the sole/primary method** — internally consistent
    against one ladder — rather than pivot to standards-bracketing as the
    primary approach. Flagged so this isn't silently re-litigated: the two
    methods measure different things (a direct measurement vs. a comparative
    judgment), and the choice to keep the direct-ratio method was deliberate,
    not an oversight.
- **Target band identification: MW-based, primary; heuristic only as an
  explicit opt-in escape hatch (decided 2026-07-13).**
  - Primary method: calibrate the ladder lane (known NEB ladder lookup table,
    e.g. P7719, or a user-supplied `--ladder-bands` override), then identify
    the target band as whichever detected band falls nearest the protein's
    known expected MW, within a tolerance (placeholder: ±15-20% of expected
    MW — approximate on purpose, to be tuned empirically against the example
    gels once real code exists, not decided precisely up front).
  - **Ladder calibration relaxed to best-effort subset matching (revised
    2026-07-13, superseding the original "exact band count match" rule
    below).** Requiring every known ladder band to be individually detected
    turned out to be a much higher precision bar than real practice needs —
    a bench scientist anchors off whichever 1-2 nearby rungs are visible, not
    a full curve through every rung. It was also unworkable in practice: it
    failed on every real image tried, for two different reasons (some bands
    genuinely merge at the high-MW end due to SDS-PAGE's log-linear
    migration — a well-known, near-universal compression artifact, not
    randomness; other images over-detect noise as extra "bands").
  - **Which known bands are "missing" is determined empirically, not
    assumed (revised again, same day, after further real testing).** The
    first version of this fix always assumed any undetected bands were the
    highest-MW ones (physically reasonable — that's where SDS-PAGE resolves
    worst). Real testing found a case where the *opposite* alignment fit
    meaningfully better (R² 0.95 vs. 0.89) and was independently corroborated
    by where the dominant band actually sat in a real sample lane — so a
    fixed directional assumption is itself a real source of error. Current
    rule: try every contiguous subset of the known ladder sizes that matches
    the detected band count, fit each, and keep whichever fits best. If more
    bands are detected than known sizes (likely noise), keep only the most
    prominent ones by area before this search. Guardrails so this doesn't
    silently miscalibrate: requires at least 3 matched bands, and rejects
    even the best-fitting alignment if its R² is below 0.85 (loosened from
    an initial 0.9 after confirming a real ~0.89-0.95-R² fit still correctly
    located a real target band; see Implementation Status).
  - **If the ladder still can't be calibrated** (too few bands detected, or
    fit quality too poor even with the relaxed matching above), the tool
    refuses to process by default (hard error) rather than silently
    degrading to a less reliable method.
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
  purity workflow's CLI-driven philosophy above. The reviewer's original ask
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
  separate binaries per workflow — e.g. `gelx purity analyze gel.tif`,
  reflecting the actual shared-core/separate-workflows architecture. Command
  name finalized as **`gelx`** during implementation.
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

## Implementation Status

The purity workflow is implemented and passing its test suite (built the
same day design finished, so no date-of-completion note beyond "2026-07-13").

- **Package layout:** `src/gel_extractor/{core,purity}/` (src-layout),
  entry point `gelx` (`gelx purity analyze <image> --target-mw KDA [...]`).
  Run via `uv run gelx ...`; tests via `uv run pytest`.
- **Test suite:** 36 tests passing — unit tests per `core` module (synthetic,
  deterministic data), an end-to-end pipeline test via a synthetic gel image
  file, CLI tests (table/CSV/JSON/error paths), and integration tests
  against a real example gel image, including the dilution-series
  self-consistency check.
- **Reporting precision fixed:** `purity_percent` now rounds to the nearest
  whole percent (was 1 decimal place — see the former Known Limitations
  entry, now resolved). `matched_band_mw` is unaffected (a different,
  separately-imprecise measurement, not part of this fix).
- **`KNOWN_LADDERS["P7719"]` is now seeded and verified** (`core/ladder.py`)
  — `[250, 180, 130, 95, 72, 55, 43, 34, 26, 17, 10]` kDa. Verified against
  NEB's own labeled product gel image for P7719 (user-provided), which
  matches everything independently found via web research (11 bands total;
  orange reference band at 72 kDa, green at 26 kDa; range 10-250 kDa) — text
  sources alone couldn't confirm the individual band list, but the labeled
  image resolved it. `--ladder P7719` now works as a real named option, not
  just a placeholder.
- **`--help` gotcha fixed:** argparse only expands `%%` → `%` in per-argument
  `help=` text, not in a parser's `description=` — a literal `%%` was
  showing up in `gelx purity analyze --help` until this was caught by
  actually reading the rendered output, not just the source.

### Real findings from running this against real data

These surfaced only once real images hit real code — recorded here because
they're non-obvious and expensive to rediscover:

- **`data/daria_data/attachments/*.png` are Benchling attachment-viewer
  *screenshots* (full UI chrome included), not clean gel photos.** Confirmed
  by matching the filename visible inside
  `01_HpyCH4IV_PDEV1284_Protein_Purity.png`'s screenshot to an actual file in
  `data/decodeon_gel_images/Protein Purity/` — that folder holds the clean
  underlying originals. **Use the `decodeon_gel_images` versions for any
  real image processing/testing against these particular proteins**, not the
  `daria_data` PNGs (which were fine for visual review, not for a pipeline).
- **Real gel photos aren't on a white background**, so the initial lane
  auto-detection (threshold against the column-sum profile's absolute peak)
  completely failed on a real image — it found one giant "lane" spanning the
  whole gel rectangle. Fix: baseline-correct the column-intensity profile
  before thresholding, reusing the *same* rolling-baseline function already
  built for band detection along the other axis
  (`core.bands.rolling_minimum_baseline`). This validated the modular
  architecture decision in practice — the fix was a small, localized change
  in `core/lanes.py`, not a rewrite.
- **Tuned starting values** (still placeholders, per the Design Decisions
  above, but now concrete in code rather than abstract): lane-detection
  threshold fraction 0.03 (of the baseline-corrected column profile's peak),
  baseline rolling-window 51px (same default as band detection, for
  consistency), lane top-margin crop 5%, MW tolerance **20%** (top of the
  originally-discussed ±15-20% range — moved up from an initial 17.5%
  midpoint after real testing showed the true target band sitting ~19% off
  the calibrated MW, just outside 17.5%), ladder-calibration minimum 3
  matched bands and minimum R² 0.85, band-detection noise floor 10× the
  estimated point-to-point noise level. Found via direct experimentation
  against several real images in `decodeon_gel_images/Protein Purity/` —
  expect further tuning as more real gels are run.
- **A per-lane "not-found" state was added**, extending (not contradicting)
  the original target-band-identification decision: that decision covered
  what happens when the *ladder itself* can't be calibrated (hard error
  unless `--allow-heuristic`). It didn't say what should happen when the
  ladder calibrates fine but one *specific* lane's target band isn't found
  within tolerance. Implemented as: that lane gets `confidence: "not-found"`
  and `purity_percent: null` in its own result row, without aborting the
  rest of the lanes in the same run. This is a reasonable extension made
  during implementation, not something pre-approved at this granularity —
  flagging it here in case it should be revisited.
- **The heuristic (largest-band) fallback shows a real, understood bias**:
  on a real dilution series, fainter lanes lose their faint contaminant
  bands below the detection threshold first, which inflates apparent purity
  as dilution increases. The dilution-series self-consistency test passes
  but with a loose bound (~70 points; observed spread was ~59) — documented
  inline in `tests/test_purity_integration.py`.
- **Ladder calibration against real P7719 data validated, then immediately
  surfaced two more real issues — both now root-caused and fixed
  (2026-07-13).** Testing MW-matching against `8.6.25 Protein Purity.tif`
  end to end initially found: (1) one sample lane produced 98 "detected
  bands," almost certainly noise — **fixed** by adding an absolute
  noise-floor gate to `detect_bands` (estimated from point-to-point signal
  variability, which stays low for real peaks regardless of height but is
  comparable to the signal itself on a truly blank lane); that lane now
  correctly detects 0 bands, and the healthy lanes barely changed (one
  marginal band dropped from two of them). (2) the other 9 sample lanes
  found no band within the (then 17.5%) tolerance of the target MW at all —
  investigated by trying to exclude "merged" ladder bands via a width
  heuristic, which turned out fragile (a genuinely single but heavily-
  stained band was nearly indistinguishable by width from an actually-merged
  blob). The real fix ended up being the empirical multi-window search
  described above in Design Decisions, **plus** widening the MW tolerance to
  20% once the better calibration revealed the true target band sat ~19%
  off — just outside the old 17.5% bound. After both fixes: **8 of 10 real
  sample lanes now match consistently on the same MW (34.6-34.9 kDa)**,
  not a scattered mix of coincidental matches.
- **Broader real-image testing (2026-07-13) found and fixed a real
  ladder-calibration gap, and clarified how much accuracy is actually
  validated.** Swept lane detection + ladder calibration across all 11
  `decodeon_gel_images/Protein Purity/` images (not just the one tuned
  against so far). Initially, only 6/11 calibrated; investigating the 5
  failures (by directly viewing each image, not just trusting the error)
  found that 3 of them had a **visibly real but low-contrast ladder lane**
  being wrongly filtered out by the noise-floor gate above — that gate's
  10× threshold, tuned against one image's genuinely-blank lane, was too
  strict for a different image's genuinely-faint-but-real one. **Fixed** by
  using a more lenient noise floor (5×) specifically for ladder-lane
  detection, since calibration has its own downstream guardrails (band
  count, R²) to catch a bad result; sample-lane detection stays at the
  stricter default, since it has no such guardrail and a false positive
  there directly corrupts the purity ratio. Result: **10 of 11 images now
  calibrate successfully** (R² 0.89-0.98). Important scope caveat, prompted
  by direct user question: this only validates the calibration *machinery*
  across those 10 images, not full purity accuracy — see the per-file
  protein-identity notes in Data Inventory above. Only 1 image (HpyCH4IV)
  has both a clean file and a confirmed target MW to validate against
  end-to-end; a second guess (assuming `251017_..._FusionProtein.tif` was
  the FCE-T7 RNAP fusion referenced in the submitter's email) was tested and
  found wrong, so it's been
  retracted rather than reported as a finding. Getting confirmed MWs for the
  other identified proteins (Esp3I, IdeS Protease, TelA, R-218/TET3 fusion,
  CL_ASR29) is now tracked as a question for end users
  (`QUESTIONS_FOR_USERS.md`).
- **Confirmed (not just suspected) limitation: a dilution-detectability
  threshold skews purity at high dilution.** Even after the fixes above, the
  matched-purity trend still increases with dilution (29% → 48% across the
  series) — the same direction as the earlier heuristic-mode bias. Raised
  this directly with the user, who confirmed it's a real, expected concern:
  **at some dilution level, faint contaminant bands become undetectable
  before the target band does, inflating the computed ratio** — not
  primarily a detection-parameter bug, but a fundamental limit-of-detection
  effect that any densitometry approach will face. This affects both
  `heuristic` and `mw-matched` confidence modes. Not yet mitigated — see
  Known Limitations below; also raised as a new question for end users
  (`QUESTIONS_FOR_USERS.md`) about whether there's a recommended dilution
  range or whether the most-concentrated ("Total") lane should be treated
  as the authoritative measurement rather than aggregating across the whole
  series.
- **Real end-to-end accuracy test against 5 newly-confirmed MWs
  (2026-07-13) found real failures, not a validation win.** Ran
  `gelx purity analyze` with each confirmed MW (Esp3I, IdeS Protease, TelA,
  TET3/R-218, CL_ASR29 — see Data Inventory) against its matching image:
  - `7.17.24 PDEV772 Conc Stock.jpg` (TelA): ladder **fails to calibrate at
    all** — confirmed by direct visual inspection, that lane's bands are
    genuinely faint/low-count in this scan, not a detection-parameter issue.
  - `251017_..._FusionProtein.tif` (TET3/R-218, one lot): ladder reports a
    good fit (R²=0.98) but **every sample lane comes back "not-found."**
    Diagnosed by dumping per-lane calibrated band MWs directly: lane
    detection found **13 lanes where the image visually has 9** (1 ladder +
    8 labeled dilution lanes), and nearly every "lane" carries a spurious
    band calibrating to ~300 kDa — consistent with loading-well/aggregate
    smear leaking into the lane crop rather than being excluded (the
    top-margin crop is still just a fixed 5%, never validated against real
    wells — see below), compounded by the lane-count over-detection itself.
    This looks like real evidence for the lane-capture limitation below, not
    a target-MW problem.
  - Esp3I, IdeS Protease, and CL_ASR29 all calibrate and match a plausible
    MW, but **purity swings inconsistently across what should be a
    self-consistent dilution series** (e.g. Esp3I: 3%, 46%, 7%, 5% across
    4 matched lanes) — a materially different failure mode than the
    already-documented dilution-detectability trend (which is a smooth,
    monotonic drift, not this kind of noise). Not yet root-caused.
  - `10.31.25 PDEV1437.tif` (TET3/R-218, the other lot) is the one clean
    result: matched MW lands right on target (58.2-58.4 vs. 58.219 expected)
    consistently across lanes, though absolute purity is low (1-8%,
    decreasing with dilution) — plausible for a real low-purity prep, but
    unverified.
  - **Net effect: the pipeline's real per-image accuracy is worse than the
    single-image (HpyCH4IV) validation suggested.** Under active
    investigation — likely connects to the lane-capture/over-segmentation
    limitation below rather than requiring new target-MW-matching logic.
- **Lane over-segmentation investigation (2026-07-13) — two approaches
  tried, both reverted; not yet solved, root cause narrowed down
  considerably.** Root mechanism confirmed by direct inspection: `detect_lanes`
  collapses the *entire image height* into one column-sum profile before any
  lane boundary exists (`signal.sum(axis=0)` in `core/lanes.py`), and
  `Lane.crop` then applies that column range as a fixed-width rectangle over
  the full height — no row-by-row awareness anywhere in lane detection.
  Diagnosed on `251017_..._FusionProtein.tif` (13 lanes detected vs. 9 real:
  1 ladder + 8 labeled dilution lanes):
  - **Attempt 1 — row-banding + majority vote.** Split image height into 3
    bands, ran column-sum detection independently per band, kept a column
    only if ≥2 of 3 bands agreed it was "in a lane." Rationale going in: a
    real lane runs the gel's full length so should have majority support,
    while a localized artifact (dust, a corner) shouldn't. **Backfired**:
    directly cropping and viewing the suspect region showed it was actually
    the gel slab's own *physical right edge* (gel fading into background
    paper), which — being a literal physical boundary — runs the *entire*
    image height and got votes from all 3 bands, while a real sample lane's
    doublet band turned out to be vertically *localized* near the top of
    the gel (mostly blank below it) and lost the majority vote. Net result:
    lane count dropped 13→12, but a real lane was lost and the actual
    artifact was kept — a regression, not a fix. Reverted.
  - **Attempt 2 — explicit gel-edge trimming.** Added `detect_gel_extent()`:
    sample a background reference from the image's own outer border, compute
    each column's median intensity across full height, classify columns by
    nearest-centroid distance (background reference vs. overall-median
    "interior" reference, using absolute deviation rather than assuming a
    brightness direction — confirmed necessary and sufficient by checking a
    real `data/gia_data` image, which is inverted-contrast: black background,
    bright gel/bands, the opposite of purity's white-background/dark-band
    convention), then walk outward from the image's horizontal center to find
    the contiguous gel region, trimmed inward by a fixed margin. Wired into
    `analyze_image()` to scope `detect_lanes` to the gel's interior only.
    **Real, verified improvement on 10 of 11 images**: every one of the 11
    `decodeon_gel_images/Protein Purity/` images calibrated afterward
    (previously 10/11 — this fixed TelA's total calibration failure too),
    and lane counts dropped meaningfully on most images (e.g. IdeS 12→7,
    CL_ASR29 10→8) by excluding the physical gel edge from the column-sum
    profile. On the fusion-protein image specifically: 13→12, confirmed by
    direct crop that the isolated far-right dust artifact was gone; one
    residual sliver-artifact remained (the edge is slightly *curved/slanted*
    down the image height, so one global cutoff can't perfectly exclude it
    at every row — tried intersecting per-band extents to chase the curve,
    but individual bands with less real content gave noisy/unreliable
    results, one falling back to "couldn't classify" entirely). **However:
    directly re-running all 5 confirmed-MW proteins plus the HpyCH4IV
    baseline surfaced a real regression** — HpyCH4IV (our only
    externally-validated ground truth) went from a stable 29-48% purity
    across 8/10 matching lanes (34.6-34.9 kDa, matches memory of the
    original fix) down to 1-6% purity across only 3/7 matching lanes
    (32.6-33.3 kDa) after edge-trimming. Lane count for this image dropped
    11→8 (untrimmed vs. trimmed) and lane widths/positions shifted
    materially (e.g. one lane went from width 100 to width 57, another from
    103 to 114 with a different position) — **not yet root-caused**: could
    be real lanes getting merged together (changing which "lane index" a
    given physical sample lands on, and/or inflating a lane's total-area
    denominator by merging two real lanes' content), a good real lane being
    partially clipped by the new trim boundary, or something else. Reverted
    both attempts (`git checkout` on `core/lanes.py` and
    `purity/analysis.py`) rather than leave a regressed HpyCH4IV result in
    place — **repo is back to the last known-good state (36 tests passing,
    commit `38327d7`)**.
  - **To resume:** the gel-edge-trimming *idea* (direction-agnostic
    nearest-centroid classification, verified conceptually sound against
    both contrast conventions) still looks like the right general direction
    — it fixed real problems on 10/11 images including TelA's calibration.
    Before re-attempting: root-cause the HpyCH4IV regression specifically
    (compare untrimmed vs. trimmed lane boundaries for that image — recorded
    above — to see exactly which real lanes got merged/clipped and why) and
    fix that *before* re-validating across all 11 images plus the 5
    confirmed-MW proteins again. Don't re-attempt row-banding/majority-vote
    (Attempt 1) — it's now a confirmed dead end for this specific problem,
    given real bands can be vertically localized while physical gel edges
    are not, which is the opposite of what that approach assumed.

## Known Limitations — Flagged for Later

Real, open items surfaced during implementation that haven't been resolved
yet. Don't silently fix or dismiss these without discussing first — they're
recorded here specifically so they aren't lost or re-litigated from scratch.

- **Lane capture is a fixed vertical rectangle, with no smiling/curvature or
  bleed-over handling — actively being investigated, not yet fixed.**
  `Lane.crop` uses one `(x_start, x_end)` column range applied uniformly
  across the entire image height; `detect_lanes` similarly collapses the
  whole height into one column-sum profile before any lane boundary exists.
  This doesn't account for "gel smiling" (edge lanes migrating faster/slower
  than center lanes, curving bands across the gel — confirmed present, at
  least as a curved/slanted *physical gel edge*, on a real image), doesn't
  guard against bleed-over from a neighboring lane when bands are wide/
  diffuse (real merged-blob band widths over 100px observed), and doesn't
  exclude loading-well/aggregate smear at the top of a lane (still just a
  fixed 5% top-margin crop, never validated). **Two fix attempts tried and
  reverted 2026-07-13 — full detail, including a regression found on our one
  ground-truth-validated image (HpyCH4IV), in Implementation Status's "Lane
  over-segmentation investigation" entry.** Read that before re-attempting a
  fix — it records which approach is a confirmed dead end (row-banding/
  majority-vote) and which is promising but has an unresolved regression to
  root-cause first (explicit gel-edge trimming).
- **Dilution-detectability threshold skews purity at high dilution —
  confirmed real, not yet mitigated.** As dilution increases, faint
  contaminant bands drop below the detection floor before the target band
  does, inflating apparent purity (observed in both `heuristic` and
  `mw-matched` modes: 29% → 48% across one real dilution series). The user
  confirmed this is an expected, real phenomenon (a fundamental limit-of-
  detection effect, not primarily a tunable-parameter bug), so it shouldn't
  be "fixed" by further threshold tuning alone. Needs a design decision:
  e.g. flag low-total-signal lanes as lower-confidence, recommend/default to
  the most-concentrated ("Total") lane as the authoritative measurement
  rather than aggregating across the whole dilution series, or something
  else — tracked as a new question for end users in `QUESTIONS_FOR_USERS.md`.

## Open Questions

No open internal design/architecture questions remain as of this update
(2026-07-13). This section is for questions Jacob and Claude can resolve
through design discussion alone. Questions that need an answer from the
domain-expert end users (the project submitters, reviewers, etc.) instead
live in `QUESTIONS_FOR_USERS.md` — check there for the current accrued list before
assuming a piece of domain knowledge (e.g. "is this ladder the standard one")
rather than guessing.

## Data Inventory

- `data/daria_data/project.md` — original proposal text for sub-project 1.
- `data/daria_data/attachments/` — 4 example gel images (PNG/JPG) + 1 PDF of an
  email thread with additional per-protein context (molecular weights, ladder
  used, Benchling links). **Important (found 2026-07-13 during
  implementation): these PNGs are Benchling attachment-viewer screenshots
  (full UI chrome included), not clean gel photos** — see Implementation
  Status below. `data/decodeon_gel_images/Protein Purity/` has the clean
  originals for at least the HpyCH4IV one (`8.6.25 Protein Purity.tif`,
  confirmed by the filename visible in the screenshot).
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
  **This is the dataset all real-image pipeline testing/tuning has actually
  used** (lane detection, ladder calibration, the noise-threshold fix) —
  `daria_data`'s images were never fed into the pipeline itself (see below),
  only its email text for known MWs.
  - **Per-file protein identity, confirmed by directly viewing each image
    (2026-07-13)** — only `8.6.25 Protein Purity.tif` (HpyCH4IV) has both a
    clean testable file *and* an independently confirmed MW (29,267 Da, from
    the submitter's email); the rest have a visible identity label but **no
    confirmed MW yet** — tracked as a new question for end users in
    `QUESTIONS_FOR_USERS.md`:
    - `2.4.25 PDEV981 Protein Purity.jpg` — Esp3I (PID940/PDEV981),
      **confirmed MW 61,708.19 Da**
    - `9.20.24 PDEV829 Conc Stock.jpg` — IdeS Protease (PDEV829),
      **confirmed MW 36,825.91 Da**
    - `7.17.24 PDEV772 Conc Stock.jpg` — TelA (NEB3606, PDEV772),
      **confirmed MW 39,358.70 Da**
    - `10.31.25 PDEV1437.tif` / `251017_..._FusionProtein.tif` — same
      construct, two lots/dates: R-218, a TET3 fusion (PDEV1437 / PID1384),
      **confirmed MW 58,218.74 Da**. **Not** the FCE-T7 RNAP fusion from
      the submitter's email — an earlier guess assuming that was wrong (confirmed by
      testing: no band anywhere near 200,717 Da), corrected once actually
      checked.
    - `1.15.25 Concentrated Stock.jpg` — CL_ASR29 (PID926/PDEV946),
      **confirmed MW 44,599.87 Da**
    - `6.12.26 PDEV1718 Protein Purity.tif`, `260612_ProteinPurity.tif`,
      `260407_protein_purity.tif`, `4.16.26 Protein Purity.tif` — **no
      legible protein label found at all**; identity unknown, not just MW.
  - **Important scope note on `daria_data`'s 4 confirmed MWs** (HpyCH4IV
    29,267 Da; FCE-T7 RNAP fusion 200,717 Da; EcoRI-HF 31,027 Da; BtgZI
    94,198 Da, all from the email thread): only HpyCH4IV has a matching
    clean `decodeon_gel_images` file we can actually run through the tool.
    The other 3 known MWs have no corresponding clean, pipeline-testable
    image at all — they only exist as `daria_data` screenshots/QC-report
    images, which aren't valid pipeline input (see Sub-project 1 above).
    **Net result: full end-to-end purity-accuracy validation currently has
    exactly one confirmed real test case (HpyCH4IV)** — every other
    successful calibration in this batch only confirms the calibration
    *machinery* works, not that the reported purity % is correct.
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
need an answer from the domain-expert end users (the project submitters,
reviewers, etc.) rather than something resolvable through design discussion
alone — the intent is to accrue these and ask them in a batch rather than
one at a time.
Add to it as new questions surface; don't resolve them by guessing.

## Working Agreements

- **No git actions without explicit consent.** Never run `git commit`,
  `git push`, or any other git state-changing command unless the user
  explicitly asks for it in that moment. Read-only git commands (status, log,
  diff) are fine.
- **No unilateral design assumptions.** This is a from-scratch project; decide
  architecture, libraries, algorithms, and scope iteratively and explicitly
  with the user rather than inferring intent. When in doubt, ask.
- **Current phase: purity workflow implemented and tested; activity workflow
  not started.** See Implementation Status above. Don't start building the
  activity workflow until that's explicitly requested — finishing purity
  doesn't imply a green light to move on automatically.
- **Document thoroughly enough to explain the whole system to non-implementing
  stakeholders later.** Jacob needs to be able to walk the project submitters
  and reviewers through what was built and why at the end, not just hand them
  working code. Every Design Decision entry should carry its rationale (the
  "why"), not just the decision itself — this applies to future edits too,
  not only what's already written.
- **Robust testing is required, not optional polish.** See the "Modular,
  swappable architecture" and "Robust testing" entries in Design Decisions —
  every pipeline stage needs unit tests, plus integration tests against the
  real example gel images, plus the dilution-series self-consistency check
  encoded as an actual automated test.

## Architecture Diagram

`diagrams/program-flow.mmd` (raw Mermaid source) and `diagrams/program-flow.png`
(rendered) capture how the program works. The purity side now reflects a
working, tested implementation (see Implementation Status); the activity side
is still a conceptual sketch, since that workflow hasn't been built. Render
with: `mmdc -i diagrams/program-flow.mmd -o diagrams/program-flow.png -b white -s 2`.

**Planned second diagram (requested 2026-07-13, not yet built):** the user
wants a *separate* mermaid diagram capturing actual program flow — real
class/method calls through the implemented pipeline — alongside (not
replacing) the conceptual diagram above. No filename, scope, or format
decided yet; don't build until asked, and discuss scope first rather than
assuming what "class/method calls" should mean here (e.g. a UML-style class
diagram vs. a sequence diagram for one CLI invocation).

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
- Language/framework/dependency tooling is now decided and implemented — see
  the "Tech stack" entry in Design Decisions (Python 3.11+,
  numpy/scipy/scikit-image, argparse, pyproject.toml + uv, pytest) and
  "Implementation Status" for the actual package layout (`src/gel_extractor/`,
  `tests/`, `pyproject.toml`, `uv.lock`).
