# AGENTS.md

Working notes for AI-assisted development on `neband` (renamed from
`gel-image-extractor` 2026-07-23, see Implementation Status). This document
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
  Planned layout (`activity/` not yet created -- only `core/` and `purity/`
  exist on disk today):
  ```
  src/neband/
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
  **Reconfirmed 2026-07-14** for the purity workflow's own lane-detection
  problem too, prompted by the user asking directly whether an ML approach
  could work here instead of classical CV: same conclusion, for the same
  reason — ~17 real images, only 7 with a confirmed purity number, and *zero*
  with pixel/lane-boundary ground truth (the `--debug` boxes are the
  algorithm's own guesses, not training labels). Not enough data to train a
  segmentation model without a real data-collection effort first; a
  from-scratch model would very likely overfit and underperform the existing
  heuristic pipeline. A pretrained general-purpose segmentation model
  (e.g. Segment Anything) used as a component is a more realistic *future*
  angle than training from scratch, but still needs more ground truth to
  validate against and wasn't pursued given the MVP timeline — see the
  curve-tracing prototype entry in Implementation Status for what *was*
  pursued instead.
- **Interface: hosted inside `ebase`, single image upload — decided
  2026-07-16.** Not a standalone CLI executable distributed to end users.
  Instead hosted internally inside `ebase` (an existing internal app with a
  UI); a user uploads one gel image and picks settings in the UI, which the
  backend translates into CLI flags before calling the same underlying
  pipeline. Core logic stays decoupled from CLI-specific concerns
  specifically so this translation stays thin. This resolves a live
  dependency-weight question head-on: a standalone-executable path would
  have forced bundling `sam-zeroshot`'s real dependency footprint (measured
  2026-07-16: torch alone is 501MB installed, vs. a 169MB base pipeline
  venv without it — the checkpoint itself is only 39MB, torch dwarfs it) onto
  every end-user machine, including real offline/checkpoint-download risk on
  locked-down lab computers. Hosting server-side makes that a one-time
  server-side cost instead of a per-user tax, removing the concern — see the
  lane-detection-alternatives entry in Implementation Status for where
  `sam-zeroshot` came from.
- **SAM backend: no persistent warm model — decided 2026-07-16, revisit if
  usage grows.** Given expected usage of only ~1-2 analyses/week, keeping
  `sam-zeroshot`'s ~600MB dependency (torch + MobileSAM checkpoint) resident
  in memory continuously to save latency on that few requests isn't worth
  the standing memory cost. Measured directly what the trade-off actually
  costs: a cold process pays ~3.7s fixed overhead before any real work
  (import torch 1.3s + import `mobile_sam`/`core.sam_lanes` 0.85s + load
  model+checkpoint from disk 1.5s), plus ~0.4s for the one-time-per-image
  encoder pass (a second warm call is barely faster — 0.36s vs. 0.43s — so
  the fixed cost is import/load, not model warm-up per se). Full request
  lands around 5-8s total including per-lane inference, vs. a couple
  seconds warm. Judged an acceptable one-time tax for infrequent use, not a
  standing resource cost — reversible without an architecture rewrite if
  usage patterns change later. Only affects `sam-zeroshot`; the rectangle
  and Viterbi methods have no model to load and aren't affected by this
  decision either way. **Caveat to confirm at implementation time**: whether
  this cold-start cost is paid on literally every request depends on how the
  job actually runs — a genuinely fresh process/container per request pays
  it every time, whereas a long-lived worker (e.g. a Delayed Job worker that
  stays running between jobs) would only pay it on that worker's first SAM
  job, not every one. Don't assume either way until the Delayed Job wiring
  below is actually decided.
- **Queueing (Delayed Job) deferred pending real latency data — decided
  2026-07-16.** NEB has Delayed Job available internally; whether gel
  analysis (particularly the `sam-zeroshot` method, given the cold-start
  cost above) needs to run on a queue rather than inline in the
  request/response cycle will be decided from real measured latency in the
  actual `ebase`-hosted environment once it exists, not guessed at now.
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
    known expected MW, within a tolerance (**20%**, moved up from an initial
    17.5% midpoint once real testing showed a true target band sitting ~19%
    off the calibrated MW — see Implementation Status).
  - **Ladder calibration is best-effort subset matching, not an exact-match
    requirement.** Requiring every known ladder band to be individually
    detected is a much higher precision bar than real practice needs — a
    bench scientist anchors off whichever 1-2 nearby rungs are visible, not a
    full curve through every rung. It also failed on every real image tried
    for two reasons: some high-MW bands genuinely merge (SDS-PAGE's
    log-linear migration, a known compression artifact, not randomness), and
    some images over-detect noise as extra "bands." Current rule: try every
    contiguous subset of the known ladder sizes that matches the detected
    band count, fit each, keep whichever fits best (real testing found the
    "assume missing bands are the highest-MW ones" shortcut is itself wrong
    on some images — don't assume a direction, always search). Guardrails:
    requires ≥3 matched bands and rejects the best-fitting alignment if its
    R² is below 0.85.
  - **If the ladder still can't be calibrated**, the tool refuses to process
    by default (hard error) rather than silently degrading.
  - **`--allow-heuristic` is an explicit, non-default escape hatch.** It never
    triggers automatically. When passed, it permits falling back to a
    largest/darkest-band heuristic so the user can still get *some* number out
    when calibration isn't possible, but the result must be clearly flagged as
    lower-confidence in the output (`confidence: heuristic` vs.
    `confidence: mw-matched`) — never presented as equivalent to an
    MW-matched result.
  - Embedded purity-standard lanes (50/75/88/94/97/98/99%, when present in a
    gel like the EcoRI-HF/BtgZI QC reports) are an optional secondary
    validation check against the computed number, not required for and not
    part of the core calibration — exact mechanism for using them is a future
    detail, not blocking for MVP. **Confirmed 2026-07-14 as a non-issue for
    now**: the user's team doesn't typically produce gels in this format —
    plain ladder + dilution series is the real-world norm.
  - **Superseded 2026-07-17: default flipped to largest-band selection,
    MW-matching kept as an opt-in.** Empirical testing (see Implementation
    Status) found largest-band selection lands closer to confirmed
    ground-truth purity than MW-matching on 2 of 4 registered
    lane-geometry methods, a wash on the other 2. New `--band-selection
    {largest,mw-strict}` flag (default `largest`) — `mw-strict` reproduces
    this original 2026-07-13 decision's exact behavior, byte-for-byte,
    unchanged. In `largest` mode the ladder is still calibrated when
    possible, but purely to *verify* the selected band against
    `--target-mw` and flag a mismatch (`confidence: mw-mismatch`, a new 4th
    value alongside `mw-matched`/`heuristic`/`not-found`) — never to gate
    selection. `--allow-heuristic` keeps its original meaning for the "zero
    calibration info at all" case in both modes; a calibrated-but-mismatched
    band is always reported (with the flag), regardless of
    `--allow-heuristic`, since real information exists then, just
    disagreeing information. Not a reversal of the reasoning above (an
    external MW check is still valuable) — a response to real evidence that
    MW-matching's *selection* role was net-negative on this project's real
    images, while its *verification* role remains worth keeping.
- **CLI usability requirement: flags must be clearly self-documented in
  `--help`.** Since end users aren't CLI-comfortable (per discussion), every
  flag needs a clear, complete description in the tool's own help output, not
  just in external docs. This applies from the first CLI implementation
  onward, not as a later polish pass.
- **Purity input is CLI-flag-driven and fully self-contained (confirmed
  2026-07-13).** No external lookups (e.g. a live Benchling API call) — the
  user supplies `--target-mw` and `--ladder` (or `--ladder-bands` for an
  unrecognized ladder) directly on the command line. **Answered 2026-07-14,
  closing the "maybe default `--ladder`" idea originally floated**: ladder
  choice genuinely varies by team/scientist (at least 2 in use within the
  user's own group) — there's no single de facto standard to default to. The
  user's team is open to standardizing internally, but which ladder is a
  separate, still-pending decision. Keep `--ladder`/`--ladder-bands` explicit
  rather than adding a silent default.
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
  workflow's 96-well grid/circle detection later, not a purity concern);
  `argparse` for the CLI (Jacob's existing familiarity outweighs `typer`'s
  nicer auto-`--help`, with a deliberate commitment to writing complete
  `help=` text for every flag to still meet the CLI-usability requirement
  above); `pyproject.toml` + `uv` for packaging (our stack is fully available
  as PyPI wheels, no need for conda's binary management); `pytest` for
  testing.
- **One CLI entry point with subcommands (confirmed 2026-07-13)**, not
  separate binaries per workflow — e.g. `neband purity analyze gel.tif`,
  reflecting the actual shared-core/separate-workflows architecture. Command
  name finalized as **`neband`** during implementation; renamed to **`neband`**
  2026-07-23 along with the whole package/repo (see the dedicated rename
  entry in Implementation Status) — no functional change, same command.
- **Output formats (decided 2026-07-13): table (default) + optional CSV/JSON,
  additive not mutually exclusive.** Human-readable table always prints to
  stdout by default. `--csv [PATH]`: if a path is given, writes a CSV file
  there (table still also prints to stdout); if no path is given, prints CSV
  to stdout *instead of* the table, so it stays pipeable. `--json [PATH]`
  works the same way. Both flags can be combined; using both bare (no path)
  at once is the one disallowed combination, since they'd both want stdout.
  Shared column/field set: `lane`, `purity_percent`, `confidence`
  (`mw-matched` / `heuristic` / `not-found`, plus `mw-mismatch` added
  2026-07-17 — see Implementation Status), `target_mw_expected`,
  `matched_band_mw`, `low_signal` (added 2026-07-14, see Implementation
  Status), `method`/`maturity` (added 2026-07-16, see Implementation
  Status).
- **Target-band edge cases resolved (2026-07-13):**
  - **Doublets/multiple bands within MW tolerance:** sum all bands that fall
    within the tolerance window as the target signal, rather than picking
    only the single nearest band — more scientifically defensible, and
    directly motivated by an observed real doublet. **Superseded 2026-07-17
    for the default `--band-selection largest` mode**: doublet-summing is
    now `mw-strict`-only (an explicit opt-in scope decision, not an
    oversight) — `largest` mode selects a single band regardless of MW, see
    the "Superseded 2026-07-17" Design Decision entry above.
  - **Which detected lane is the ladder:** default to the leftmost detected
    lane (true in every example seen so far), with an override flag for the
    rare exception.
  - **Lane vertical bounds:** crop starts just below the loading well through
    the dye front — implemented as an adaptive (not fixed-fraction) crop, see
    Implementation Status.
- **Validation strategy (decided 2026-07-13): no external numeric ground
  truth exists** for most example gels (the closest early on was "EcoRI-HF is
  &gt;95% pure," a threshold, not an exact value). Primary correctness signal:
  **a dilution series of the same sample should yield roughly the same
  purity % across all dilution lanes**, since diluting shouldn't change
  purity, only total signal — encoded as an automated test. (Real confirmed
  ground-truth purity numbers were later obtained for 6 more images on
  2026-07-14 — see Data Inventory's `pptx_tet3_gels` entry — but this
  dilution-consistency check remains the primary automated signal.)
- **Modular, swappable architecture (decided 2026-07-13) — explicit
  requirement, since several decisions above were expected to change once
  real output was in hand.**
  - **Pipeline of discrete stages**, not one monolithic function: image →
    intensity profile → baseline-corrected profile → detected bands →
    identified target band(s) → purity % → formatted output.
  - **Pluggable algorithms behind a common interface** for pieces expected to
    be revised: baseline correction, band/peak detection, ladder-lane
    identification.
  - **A structured internal result object** (`LaneResult`) rather than raw
    dicts/tuples. Table/CSV/JSON output are independent formatters that all
    read from the same result object.
  - **Centralized, named configuration for tunable values**, not magic
    numbers scattered through the code.
- **Robust testing is a project requirement, not optional polish (decided
  2026-07-13).** Every pipeline stage has unit tests, plus integration tests
  against real example gel images, plus the dilution-series self-consistency
  check as an actual automated test — the main correctness signal given the
  lack of external ground truth for most images.

## Implementation Status

**Current state (2026-07-21): purity workflow implemented and tested, 100
tests passing.** 4 lane-geometry methods registered (`--method
rectangle/viterbi/ridge/snake/all`, default `rectangle`) and 2 band-
selection strategies (`--band-selection largest/mw-strict`, default
`largest`, largest-band selection since 2026-07-17 — see this section's
dated entries below for the full history). Package layout:
`src/neband/{core,purity}/`
(src-layout), entry point `neband` (`neband purity analyze <image> --target-mw
KDA [...]`). Run via `uv run neband ...`; tests via `uv run pytest`. Test suite
composition: unit tests per `core` module (synthetic, deterministic data),
end-to-end pipeline tests via synthetic gel images, CLI tests (table/CSV/
JSON/error paths), and integration tests against real example gel images
including the dilution-series self-consistency check.

Below is a condensed history of what was built, found, and fixed, newest
first-in-each-topic. Older entries have been trimmed of superseded
blow-by-blow numeric detail (exact intermediate test counts, per-attempt
percentages) — the full detail is in git history if ever needed again; what's
kept here is every decision, root cause, and "don't retry X" warning.

- **Ladders seeded and verified**: `KNOWN_LADDERS["P7719"]` (`core/ladder.py`)
  — `[250, 180, 130, 95, 72, 55, 43, 34, 26, 17, 10]` kDa, verified against
  NEB's own labeled P7719 product image. `KNOWN_LADDERS["P7717"]` (2026-07-14)
  — `[200, 150, 100, 85, 70, 60, 50, 40, 30, 25, 20, 15, 10]` kDa, same way.
  Real validation: `260612_ProteinPurity.tif` (confirmed P7717, target 202.49
  kDa) calibrates on all 4 sample lanes, matched MWs land 167-180 kDa —
  inside the 20% tolerance, a real accuracy data point, not a clean win.
- **Real findings from running this against real data** (non-obvious,
  expensive to rediscover):
  - `data/daria_data/attachments/*.png` are Benchling attachment-viewer
    *screenshots* (UI chrome included), not clean gel photos — confirmed by
    matching one to its clean original in `data/decodeon_gel_images/Protein
    Purity/`. Use the `decodeon_gel_images` versions for any real pipeline
    testing.
  - Real gel photos aren't on a white background — initial lane detection
    (threshold against the column-sum profile's absolute peak) failed by
    finding one giant "lane" spanning the whole gel rectangle. Fixed by
    baseline-correcting the column profile first, reusing the same
    rolling-baseline function already built for band detection.
  - Tuned starting values (still placeholders, now concrete in code):
    lane-detection threshold fraction 0.03, baseline rolling-window 51px,
    MW tolerance 20%, ladder-calibration minimum 3 matched bands / R²≥0.85,
    band-detection noise floor 10× estimated point-to-point noise (5× for
    the ladder lane specifically — a stricter floor there wrongly filtered
    out 3/11 real low-contrast-but-real ladder lanes; sample-lane detection
    keeps the stricter default since it has no downstream guardrail against
    a false positive).
  - A per-lane `not-found` state was added for when the ladder calibrates
    fine but one specific lane's target band isn't found within tolerance
    (`confidence: not-found`, `purity_percent: null`, doesn't abort the rest
    of the run) — an extension of the original design, not a contradiction.
  - The `--allow-heuristic` fallback has a real, understood bias: on a real
    dilution series, fainter lanes lose their faint contaminant bands below
    the detection threshold first, inflating apparent purity as dilution
    increases. This is why the dilution-series self-consistency test uses a
    loose bound, and is the same root phenomenon as the dilution-
    detectability limitation below.
  - Ladder calibration against real P7719 data (HpyCH4IV) initially found two
    separate real bugs, both fixed: (1) a noisy sample lane produced 98
    "detected bands" — fixed via the noise-floor gate above; (2) no band
    matched the target MW at all — root-caused as the calibration
    direction-assumption problem (fixed by trying every contiguous subset
    rather than assuming which bands are "missing") plus widening MW
    tolerance to 20%. After both fixes: 8/10 real sample lanes matched
    consistently (34.6-34.9 kDa).
  - Broader sweep across all 11 `decodeon_gel_images/Protein Purity/` images
    found 3 more genuinely-real-but-low-contrast ladder lanes being wrongly
    rejected by the noise floor — fixed with the more lenient 5× ladder-lane
    floor above. Result: 10/11 images calibrate (R² 0.89-0.98). **Important
    scope caveat**: this only validates calibration *machinery* — at the
    time, only HpyCH4IV had both a clean file and an independently confirmed
    target MW to validate full accuracy against (this has since improved —
    see the `pptx_tet3_gels` entry in Data Inventory, 7 images now have
    confirmed ground truth).
  - **Dilution-detectability threshold confirmed real** (not just suspected):
    matched purity trends upward with dilution (29%→48% across one series).
    User confirmed this is an expected, fundamental limit-of-detection
    effect (faint contaminant bands become undetectable before the target
    band does), not a tunable-parameter bug — see the dedicated Known
    Limitations entry and the `low_signal` flag below for how it was
    addressed.
  - **Real end-to-end accuracy test against 5 newly-confirmed MWs
    (2026-07-13, Esp3I/IdeS Protease/TelA/TET3-R-218/CL_ASR29 — see Data
    Inventory) found real failures, not a validation win**: TelA's ladder
    fails to calibrate at all (genuinely low-contrast scan); the fusion-
    protein image (`251017_..._FusionProtein.tif`) returns "not-found" for
    every lane despite a good ladder R², root-caused to lane over-detection
    (13 lanes found where the image visually has 9); Esp3I/IdeS/CL_ASR29 all
    calibrate but show purity swinging inconsistently across a dilution
    series, a different failure mode than the smooth dilution-detectability
    drift, not root-caused; only `10.31.25 PDEV1437.tif` (the other TET3/
    R-218 lot) gave a clean, consistent result. Net: real per-image accuracy
    is worse than the single-image (HpyCH4IV) validation suggested — this
    motivated the lane over-segmentation investigation below.

- **Lane over-segmentation — the project's longest-running open problem,
  multiple attempts across 2026-07-13/14, partially resolved.** Root
  mechanism: `detect_lanes` collapses the *entire image height* into one
  column-sum profile before any lane boundary exists, with no row-by-row
  awareness — so it can't handle gel smiling (lanes curving as they migrate)
  or bleed-over between adjacent overloaded wells, and (until fixed) badly
  fragmented single fading lanes into multiple pieces.
  - **Attempt 1 (2026-07-13), row-banding + majority vote — confirmed dead
    end, do not retry.** Assumed a real lane runs the gel's full height so
    should get majority support across sub-bands, while a localized artifact
    wouldn't. Backwards: the suspect region turned out to be the gel slab's
    own physical edge (which, being a literal boundary, spans the *entire*
    height), while a real lane's band was vertically localized and lost the
    vote. Reverted.
  - **Attempt 2 (2026-07-13), explicit gel-edge trimming — promising
    direction, unresolved regression, never re-attempted.** Classified each
    column as background vs. interior gel via nearest-centroid distance from
    a border-sampled reference (direction-agnostic, verified against an
    inverted-contrast activity-gel image too). Fixed calibration on 10/11
    images (including TelA) and reduced lane over-counts substantially, but
    caused a real regression on HpyCH4IV (purity dropped from a healthy
    29-48% range to 1-6%, lane count 11→8) that was never root-caused before
    the session moved on. Reverted (`core/lanes.py`, `purity/analysis.py`).
    **If resuming**: root-cause the HpyCH4IV regression specifically before
    re-validating broadly — do not retry Attempt 1's row-banding idea, it's
    a confirmed dead end for a different, incompatible reason.
  - **Attempt 3 (2026-07-14), lane fragmentation specifically — implemented
    and validated, a real partial fix.** Narrower than Attempts 1-2: targets
    only a single fading lane splitting into fragments as its column-sum
    noise flickers above/below the detection threshold (confirmed
    fragment-to-fragment gaps, 26-40px, were *smaller* than genuine
    inter-lane gaps elsewhere on the same image, 65-183px — ruling out any
    single gap-size threshold). `_merge_fragmented_runs` in `core/lanes.py`
    merges a run into a group only if the *candidate run's own width* is
    under 30% of the image's reference lane width (`DEFAULT_FRAGMENT_
    NARROW_FRACTION`) — not just "would the combined result look
    reasonable." That distinction mattered: a first design (combined-span-
    only cap) caused a real regression, caught by validating against every
    real image before committing — it merged the ladder into the first
    sample lane on one image, and merged two distinct real lanes together on
    another, because one unusually wide run elsewhere inflated the
    percentile-derived reference. Deliberately does not use the physical
    comb or any assumed lane pitch as a reference (per the user: the gel is
    a flexible medium no longer geometrically locked to the comb once
    removed and run) — only this image's own already-detected run widths,
    at runtime. **Validated against all 17 real images**: no image's lane
    count increased; HpyCH4IV's only change was a sensible merge of two
    fragments into a believable blended reading; `PDEV1580`'s already-good
    matches were untouched. **Explicitly partial**: `PDEV1452`'s worst
    3-fragment cluster only merges 2 of 3 — chose conservative parameters
    over fully closing this case, given the demonstrated over-merge risk.
    Gel smiling/curvature and bleed-over remain completely unaddressed.
  - **Curve-tracing prototype (2026-07-14) — explored as a potential
    replacement architecture, real mixed results, not adopted.** The user
    directly questioned whether the rectangle-lane assumption is fundamentally
    the wrong model, given visible real curvature — a fair question, since
    every over-segmentation bug above is arguably a symptom of collapsing a
    curved 2D reality into straight 1D projections. ML was considered and
    ruled out first (see Design Decisions) for lack of any pixel/lane-level
    ground truth. A background agent then prototyped per-strip lane
    detection + simple centroid tracking (deliberately skipping the harder
    lane-split/merge problem) on branch `curve-tracing-lane-detection`
    (`src/neband/core/curve_lanes.py`, not merged, not wired into the
    real pipeline). **Result: real, visible improvement on a well-behaved
    gel** (HpyCH4IV — curves follow the comb-tooth angle better than
    rectangles) **but did not fix the motivating hard case**: on `PDEV1452`,
    curve-tracing produced *more* fragments than the rectangle approach
    (~27 vs. ~14 real lanes) — independent per-strip detection is noisier
    than one whole-image column projection, and the no-merge tracker
    amplified that noise rather than resolving it. Visual comparisons saved
    to `data/curve_tracing_prototype_comparisons/` (gitignored, see Data
    Inventory). **Recommended next step if resumed**: don't re-detect lane
    candidates independently per strip — run `detect_lanes` once on the
    whole image to fix lane count/identity first, then only trace each
    already-identified lane's local curvature within a narrow window around
    its known position.
  - **Follow-up on the curve-tracing branch (2026-07-15/16, not merged
    here)**: `curve-tracing-lane-detection` was carried further than this
    prototype (an anchored v2/v3 redesign, wired into the real pipeline) and
    separately validated against the full confirmed-ground-truth set — full
    detail lives only in that branch's own AGENTS.md, not duplicated here,
    since it hasn't been merged. Headline finding, worth recording on `main`
    regardless of that branch's fate: curve tracing turned out **not** to
    measurably improve purity/MW accuracy over this branch's straight-
    rectangle approach on any of the 15 real images with confirmed protein
    identity — the real accuracy ceiling turned out to be lane
    identification (which lane in a multi-lane dilution series corresponds
    to a slide's single confirmed value), not lane-geometry curvature, on
    that ground-truth set.
- **Six alternative lane-detection approaches prototyped in parallel via a
  Claude Code Workflow (2026-07-16) — no clean winner, but two directions
  worth continuing.** Explored after the curve-tracing follow-up above
  found no accuracy win, directly questioning whether "trace a
  straight-then-curved rectangle" is itself the wrong frame, not just
  under-tuned. Six genuinely different mechanisms, each built by an
  isolated agent on its own branch off this branch (`main`), each a
  standalone prototype module reusing the existing, working
  `core/bands.py` (which also holds baseline correction) / `core/ladder.py` /
  `purity/analysis.py`'s `_analyze_lane_detailed` unchanged — so results
  are attributable only to the lane-geometry/profile-extraction
  difference, not incidental reimplementation differences. Each
  self-validated against the same 15-image confirmed-ground-truth set
  previously used to compare `main` against `curve-tracing-lane-detection`
  (that detailed comparison lives only on the curve-tracing branch's own
  AGENTS.md — see the note above — but the baseline numbers it produced
  are quoted inline below), scored against those recorded baseline numbers.
  - **Branches** (all rooted on `main`, unmerged, one new module each,
    nothing else touched): `band-first-graph-lanes`
    (`core/blob_lane_graph.py`, bottom-up contour/connected-component
    blob-chaining, no geometry assumed at all), `viterbi-lane-tracing`
    (`core/viterbi_lanes.py`, globally-optimal DP/Viterbi path-finding
    through the 2D intensity image), `ridge-vesselness-lane-detection`
    (`core/ridge_lanes.py`, Frangi/Meijering ridge filtering),
    `snake-active-contour-lanes` (`core/snake_lanes.py`, deformable active
    contours), `dilution-shared-row-fitting` (`core/shared_row_lanes.py`,
    joint shared-row fitting across one dilution series),
    `sam-lane-segmentation-prototype` (`core/sam_lanes.py`, zero-shot
    MobileSAM segmentation, no training).
  - **The clearest result**: on the three images already flagged (via the
    curve-tracing follow-up) as known, unsolved MW-accuracy gaps
    (FusionProtein's lane over-detection, CoZyCap Njord, KasI/`PDEV1718`),
    zero-shot SAM segmentation (prompted per well) closed nearly the entire
    gap on all three — FusionProtein -11.9%/-16.8% (main/curve) →
    **+0.1%**; CoZyCap Njord -11.2%/-11.2% → **-0.2%**; KasI -9.7%/-5.3% →
    **-0.8%** — purely from better within-lane profile shape, without
    changing lane count at all (`detect_lanes` reused unchanged).
  - **But not a clean win**: SAM broke ladder calibration outright on 2
    images that previously worked (HpyCH4IV, `R-236_PDEV1452`), regressed
    one previously-good image sharply (`PDEV1437`, ~0% → -16.6%), and
    purity got worse on 4 of the 6 confirmed-purity images — including the
    one case (`R-236_PID1502_PDEV1580`) both `main` and curve-tracing
    already nailed (98% → 83%). Suspected cause: its lane box is
    deliberately widened toward the neighbor midpoint, letting real
    neighboring-lane signal dilute purity — the same class of bug the
    curve-tracing branch's own v3 fix addressed, recurring in a new form.
  - **Second most promising**: `viterbi-lane-tracing` (replaces a greedy
    anchor-window walk — the mechanism curve-tracing itself used, and
    which caused its own neighbor-bleed bug — with a globally-optimal
    path) beat or tied both baselines on 9 of 15 images, including large
    wins on FusionProtein (-11.9%/-16.8% → +4.1%) and KasI (-9.7%/-5.3% →
    -1.5%), with no new failure modes — still clearly worse on 4/15, and
    by design doesn't touch lane-count over-segmentation since it reuses
    `detect_lanes`'s count unchanged.
  - **The other four, weaker signal**: `band-graph` under-segments badly
    (2-7 chains vs. 6-14 rectangle lanes) with one outright crash (TelA, 0
    chains) — a different failure mode than the rectangle approach's
    over-segmentation, not a win, though it independently found 2 of the
    same 3 hard-case improvements (FusionProtein +0.7%, CoZyCap Njord
    -2.2%). `frangi-vesselness` and `active-contours` are each mixed with
    one standout win apiece (frangi: FusionProtein -4.0%; snakes: CoZyCap
    Njord -4.2%) and multiple regressions elsewhere. `joint-dilution-fit`
    (exploiting the fact that a dilution series is the same sample, so
    bands should share a row across lanes) ran cleanly everywhere but
    produces near-identical purity numbers to both existing baselines on
    all 6 confirmed-purity images — it does not touch the actual open
    problem (which lane maps to a slide's single confirmed value) at all.
  - **Caution on purity near-hits**: a few results (frangi/snakes landing
    at 91-100% against a confirmed 91-91.6% on the R-236 lots) come from
    the `heuristic` fallback tier, not a verified MW match, and one is
    explicitly flagged `low_signal` — plausibly the same
    dilution-detectability inflation artifact already documented (see
    Known Limitations), not a genuine improvement. Not corroborated as
    real signal.
  - **Not wired into the CLI or `--debug`**: each branch is a standalone
    module plus an ad hoc comparison script (some gitignored/uncommitted
    per-branch), not touching `purity/analysis.py` or `cli.py` — by
    design, prototype/go-no-go scope only, matching the curve-tracing
    branch's own v1/v2 discipline before it earned a v3 wiring-in.
  - **Process note**: run via a single Claude Code Workflow (`pipeline()`
    of implement-then-validate stages, `isolation: 'worktree'` per branch)
    — 12 agents, ~1.12M subagent tokens, ~25 min wall-clock, zero agent
    errors. Worth recording partly as a real data point on Workflow's own
    cost/reliability for this kind of exploratory fan-out, independent of
    the lane-detection result itself.
  - **If resuming**: `sam-lane-segmentation-prototype` and
    `viterbi-lane-tracing` are the two worth continuing — SAM for its
    geometry-accuracy ceiling (needs its purity-dilution and 2 new
    calibration regressions fixed first), Viterbi for consistency (no new
    failure modes, but doesn't address lane-count over-segmentation). The
    other four are single-data-point curiosities at this scope, not
    directions to keep pushing as currently built.

- **Individual names removed from all docs, tests, and git history
  (2026-07-13).** Replaced accumulated submitter/reviewer names and email
  addresses with role-based references, then retroactively scrubbed prior
  commits via `git filter-branch --tree-filter` (safe — nothing had been
  pushed beyond an initial, name-free commit). See Working Agreements for
  the standing rule. **Side effect discovered 2026-07-14**: this is *why*
  local and `origin` briefly became unrelated git histories — filter-branch
  can't preserve a GPG signature on a rewritten commit (even one with
  nothing to scrub), which changed the root commit's hash and cascaded to
  every descendant. Resolved via `git push --force-with-lease` after
  confirming the stale `origin/main` had nothing of value on it.
- **`--debug [PATH]` visualization implemented (2026-07-14).** Writes an
  annotated copy of the input image: lane boxes (blue=ladder, amber=sample),
  band boxes (green=target/matched, red=other/contaminant), a per-lane
  purity/MW label, and the ladder's calibrated MW at each fitted band
  position. Built as one feature for both debugging and end-user use, not a
  separate dev-only tool. Immediately useful: running it against the stuck
  fusion-protein image visually confirmed over-segmentation at a glance and
  surfaced a second, previously-invisible bug (see next entry).
- **Adaptive top/bottom vertical crop implemented (2026-07-14) — root-caused
  and fixed a real ladder-miscalibration bug found via `--debug`.** Two real
  artifacts, present on every real image checked, that a fixed 5%
  top-margin crop didn't handle: (1) the loading-well "comb" leaves a
  scalloped fringe with real staining smear, varying 11-22% of image height
  lane to lane; (2) a dark horizontal cassette/tape-edge artifact near the
  very bottom of every image, in nearly the same row across every lane,
  whose outsized area always won a slot in the "keep the k most prominent
  bands" step — corrupting the whole ladder position-to-MW mapping, not just
  the bottom of it. **Fix**: `detect_comb_fringe_end` (per-lane, since
  fringe depth varies lane to lane — uses each row's own-width standard
  deviation, since a comb tooth's converging edges create real side-to-side
  contrast that a real band doesn't) and `detect_bottom_edge_artifact_start`
  (once per image across all lanes combined, using an IQR-style spread
  threshold rather than a ratio-to-median, which breaks down on a mostly-
  blank lane). Both restrict their search to a bounded window near their
  edge, so a real band shared across a whole dilution series can't be
  mistaken for the artifact. **A second, independent bug found while
  validating this**: each lane's own adaptive top crop meant sample-lane
  band positions were no longer in the same coordinate frame as the
  ladder's own crop, silently producing wrong MWs for every sample lane.
  Fixed via a `position_offset` re-expressing each sample lane's band
  positions in the ladder's frame before calibrating. **Net result,
  re-validated across 5 confirmed-MW proteins + HpyCH4IV**: matched MWs
  landed much closer to true targets almost everywhere (e.g. IdeS Protease
  hit an exact 36.8 vs. 36.826 confirmed), and purity returned to healthy
  ranges consistent with a purified prep instead of the coordinate-frame
  bug's depressed single digits.
- **Zero-detected-bands bug fixed (2026-07-14)**: a lane with no bands at
  all was silently reported as a confident "0% purity" (from `_safe_percent
  (0, 0)`) instead of the honest `not-found` the MW-mismatch path already
  used — found via a test regression (`test_dilution_series_purity_is_
  self_consistent`, traced to a spurious lane detection with zero real
  content). Fixed by returning `not-found` immediately when zero bands are
  detected, before MW-matching or the heuristic fallback. General fix, not
  image-specific — a batch debug-image script (`scripts/generate_debug_
  images.py`, gitignored under `scripts/`) confirmed the identical pattern
  on a second image.
- **`LaneResult.low_signal` flag added (2026-07-14)**, implementing the
  dilution-detectability decision: `analyze_image` flags a lane whose total
  detected band area is under `DEFAULT_LOW_SIGNAL_FRACTION` (20%,
  unvalidated placeholder) of the most-concentrated lane in the same image
  — a whole-image, cross-lane comparison only `analyze_image` can make
  (`analyze_lane()` alone always leaves it `False`). Surfaced as a "Flag"
  table column, a `low_signal` CSV/JSON field, and a `low-sig` `--debug`
  label suffix. Only applies when `purity_percent` isn't already `None`.
  Validated against HpyCH4IV: flagged exactly the higher, likely-inflated
  readings, left the lower/less-dilute ones alone.
- **Real end-to-end comparison against 6 newly-confirmed-purity images
  (2026-07-14, from a user-provided PowerPoint — see Data Inventory's
  `pptx_tet3_gels` entry) — the richest ground truth yet, not a clean
  validation win.** Two distinct problems found: (1) lane over/under-
  segmentation on all 6 images, confirmed by direct visual inspection, not
  just lane counts (one image sliced a single continuous band into 7 fake
  "lanes" with essentially random purity numbers) — motivated the
  fragmentation fix and curve-tracing prototype above; (2) a separate,
  unresolved MW-migration discrepancy on the R-236 lots (`PDEV1452`,
  `PDEV1495`): the dominant, clearly-real band consistently calibrates
  ~40-50 kDa higher than the confirmed MW, too large a gap to be noise.
  Candidate explanations, not distinguished between: wrong calibration
  window, genuinely anomalous SDS-PAGE migration, or the confirmed MW
  referring to a cleaved form while the gel's dominant band is uncleaved (by
  analogy to the confirmed R-217/R-218 cleaved-product relationship — see
  Data Inventory). Deliberately deferred, tracked as a new question in
  `QUESTIONS_FOR_USERS.md` rather than guessed at; marked out of MVP scope
  per user decision 2026-07-14. Not uniformly bad: `PDEV1580`'s correctly-
  detected real lanes landed within a few points of its confirmed 98.5%.
- **CLI fails cleanly on bad input (2026-07-14)** — a missing/unreadable
  image file or an unwritable `--debug` output path used to dump a raw
  Python traceback; now caught (`OSError`, alongside the existing
  `LadderNotCalibratedError`/`ValueError` handling) and printed as a single
  `error: ...` line, matching how every other CLI failure mode already
  behaved. MVP polish, not a design change.
- **Multi-method lane detection, Phase A shipped (2026-07-16, branch
  `multi-method-lane-detection`, not yet merged to `main`).** Following the
  6-approach Workflow exploration above, Jacob decided to integrate all 6
  into the real pipeline behind a method-selection mechanism rather than
  pick one winner from thin data — real `ebase` usage is meant to generate
  the comparative data this project's own 15-image validation can't. Every
  output must very clearly label each method's confidence/maturity tier
  (firm product requirement), and every method needs a rescue block since
  several have documented crash modes.
  - **New `purity/methods.py`**: a `MethodInfo`/`MethodOutcome` registry +
    per-method adapter pattern. Each adapter calls its prototype's own real
    entry point inside a try/except covering documented failure modes,
    normalizing into one `MethodOutcome` shape (`results`/`ladder_lane_
    index`/`debug_info` on success, `error` on failure — never a raised
    exception past the adapter boundary). Prototype modules themselves stay
    unmodified; only the small adapter functions are new code.
  - **`purity/analysis.py` refactored, behavior-preserving**: `analyze_image`
    now accepts an optional `crop_lane` callable (`(signal, lane,
    bottom_bound) -> (profile, top_bound, centerline)`), defaulting to
    `_default_crop_lane` (today's exact straight-rectangle behavior — every
    existing caller unaffected, all 51 pre-existing tests pass unchanged).
    A new `_analyze_signal` holds the real control flow (parametrized by
    `crop_lane`), letting an adapter that already has `signal` loaded (e.g.
    to trace a curve) skip loading/decoding the image a second time. This
    was a deliberate deviation from the original plan's literal wording
    (which described adapters as calling each prototype's own standalone
    entry point without touching `analyze_image`) — `viterbi_lanes.py` has
    no such standalone wrapper (only lower-level trace/extract functions,
    unlike `ridge_lanes.py`/`shared_row_lanes.py`, which do and should call
    those directly in later phases), so duplicating `analyze_image`'s
    ~100-line control flow per adapter was rejected as a maintenance smell
    in favor of this one pluggable seam.
  - **New `Centerline` type** (`analysis.py`): `rows`/`xs` arrays +
    `x_at_row(row)` via `np.interp` — the one shape every alternative
    method's adapter normalizes its own native curve representation into
    (a full-image-row array, a crop-relative-row array, a raw scattered-point
    path, ...), so `debug_viz.py` never needs to import any prototype
    module. `LaneDebugInfo` gained two optional fields, both `None` by
    default: `centerline` (draws an orange curve overlay) and `annotation`
    (short text for geometry that isn't a curve, e.g. a future per-lane row
    shift from `shared_row_lanes`).
  - **`debug_viz.py`**: generalizes the (separate, unmerged)
    curve-tracing branch's own curve-drawing precedent (commit `b69c320`,
    `LaneDebugInfo.track` + `_draw_traced_curve`) into the method-agnostic
    `centerline`/`annotation` fields above, and adds a full-width banner
    across the top of every debug image naming the method and its maturity
    tier, colored per tier (`MATURITY_BANNER_COLOR`) — confidence must be
    visible on the image itself, not just in a filename.
  - **CLI**: `--method {rectangle,viterbi,all}` (default `rectangle`, zero
    behavior change for today's only real usage). `--method all` runs
    every registered method, printing one table block per method (never
    interleaving lane numbers across methods — see the `lane_numbering_
    caveat` field, needed once a non-`detect_lanes`-anchored method like
    `blob_graph` is added in a later phase), one `--debug` image per method
    (`<stem>_debug_<method>.png`), and a combined JSON (`{"methods": [...]}`,
    each entry either a normal single-method payload or `{"method",
    "maturity", "error"}`). Every CSV/JSON row everywhere (including
    today's plain single-method default) now carries `method`/`maturity`
    columns — a confirmed decision, not an assumption. Exit code is 1 only
    if *every* method fails (Unix-style "true failure" semantics); a
    partial success (some methods worked, one didn't) still exits 0,
    matching the "one bad method never aborts the others" design.
  - **Methods registered, Phase A**: `rectangle` (`maturity=stable`,
    unchanged behavior) and `viterbi` (`maturity=promising`, DP/Viterbi
    curved tracing — see the lane-detection-alternatives entry above for
    its own validation history). Re-verified end to end on real images
    during this phase, including the project's standing hard case
    (`R-236_PDEV1452`): still shows the same known 13-boxes-on-~9-real-lanes
    over-segmentation (expected — this phase only reshapes per-lane
    profiles, doesn't touch lane count), with the viterbi curve visibly
    tracking real drift within several lanes in the rendered debug image.
  - **Methods registered, Phase B (same day)**: `ridge` and `snake`, both
    `maturity=experimental`. Neither module's own standalone wrapper
    (`analyze_image_ridge`, and `snake_lanes` never had one) preserves
    per-lane `Band` lists — both discard them, keeping only final
    `LaneResult`s — so both adapters instead build a `crop_lane` from each
    module's lower-level primitives (`compute_ridge_response`/
    `trace_centerline` for ridge; `trace_and_extract_profile` for snake),
    the same pattern `viterbi`'s adapter already used, needed here
    specifically so `--debug` still shows real band boxes, not just a bare
    curve line. `snake`'s adapter additionally catches a single lane's
    trace failure *inside* `crop_lane` itself, falling back to that one
    lane's plain rectangle crop rather than failing the whole image —
    matching the plan's per-lane (not just per-method) rescue requirement.
    Verified end to end on real images: both ran cleanly on `R-236_PDEV1452`
    (13 lanes, no crashes, same known over-segmentation, as expected).
    `snake` produced visibly lower purity numbers than `rectangle` on
    `8.6.25 Protein Purity.tif` — checked this wasn't a bug by inspecting
    the ladder lane's raw traced centerline directly (stayed at x=354-391,
    well inside its own x_start/x_end=341-441 lane box) rather than trusting
    a visual first impression of the rendered curve, which looked more
    dramatic than the underlying numbers actually were.
  - **Tests**: `tests/test_purity_methods.py` (registry contents, one
    success test per method, rescue-path coverage for a ladder-calibration
    failure and a missing file — both loops now iterate `METHOD_REGISTRY`
    directly so they don't need editing every phase — `run_all_methods`
    isolation) plus `test_purity_cli.py` (`--method`/`--method all`,
    including the all-methods-failed exit-code case, also registry-driven
    now instead of hardcoding method names) and `test_purity_debug_viz.py`
    (banner rendering colored by maturity, centerline/annotation rendering
    without error) — 71 tests passing on this branch at this point in Phase
    B (51 pre-existing + 20 new across both phases; now 79, see the
    Implementation Status header and the later Band-selection-redesign
    entry), all matching existing conventions (no
    pixel-perfect assertions beyond the banner's own solid fill color,
    dimension/mode checks for rendering, stdout/exit-code/Traceback-absence
    checks for the CLI).
  - **Not yet done, later phases per the implementation plan**:
    `shared_row`/`blob_graph` (Phase C — the highest-friction checkpoint,
    since `blob_graph` doesn't anchor lane count to `detect_lanes` at all,
    needing the `lane_numbering_caveat` plumbing already built but not yet
    exercised); `sam` (Phase D, gated on fixing its 2 confirmed bugs first,
    already decided separately). A Workflow (parallel multi-agent) approach
    was explicitly considered and rejected for this integration work —
    unlike the original 6-way exploration (genuinely independent,
    disposable prototypes), this is one evolving, tightly-coupled system
    where parallel agents would collide on the same shared files (the
    registry, the CLI, debug_viz) with nothing independent yet to hand
    them; a parallel adversarial code-review pass once all phases are
    built, before merging to `main`, was identified as the right place for
    that pattern instead.
- **Empirical test (2026-07-17): biggest-band target selection vs.
  MW-matching, on the 6 confirmed-purity images.** Prompted by the user
  questioning whether the whole multi-method effort is solving the right
  problem, given every geometry method so far lands in roughly the same
  place against confirmed ground truth (see above). Forced every lane
  through the largest-band heuristic unconditionally (call `run_method`
  with no `ladder`/`ladder_bands`, `allow_heuristic=True` — with no known
  MWs, `calibration` stays `None` and `_analyze_lane_detailed` skips
  MW-matching entirely for every lane, not just as a fallback), instead of
  today's default (MW-match first, heuristic only when that fails), and
  compared each method's best-matching-lane purity against the confirmed
  value the same way as the earlier ground-truth comparisons. **Result: a
  real, verified (not a search-space-size artifact — confirmed identical
  candidate-lane counts between the two modes on a spot check) improvement
  for `rectangle` (15.4pp -> 8.9pp average |purity - confirmed| across the
  6 images) and `viterbi` (14.2pp -> 10.4pp); essentially a wash for
  `ridge` (2.4pp -> 2.6pp) and `snake` (5.4pp -> 5.7pp), both already close
  either way.** Mechanism, confirmed by inspecting individual lanes (e.g.
  `R-217_PDEV1405`): MW-matching and biggest-band selection genuinely pick
  *different* bands as "the target" in several lanes, and biggest-band
  happens to land closer to the confirmed value on this image set —
  plausibly related to the same real MW-migration-discrepancy phenomenon
  already documented for the R-236 lots, since a mis-calibrated or
  higher-than-expected migration position would cause MW-matching to grab
  the wrong (small, low-purity) band while the true dominant band sits
  outside the tolerance window entirely. **Caveats, not yet resolved**: n=6
  images; "best-matching lane" is the same generous best-case proxy used
  throughout this project's validation (see the ground-truth-comparison
  entry above) — real accuracy under either rule could be worse than these
  numbers suggest; whether this generalizes to the 9 confirmed-MW-only
  images (no purity ground truth to check against, only MW) is untested.
  **Not yet decided or built**: whether to make biggest-band selection the
  new default, an opt-in flag (e.g. `--band-selection largest`, orthogonal
  to `--method`, since target-band identification and lane geometry are
  independent axes reused by all 4 registered methods identically), or
  left as today's fallback-only behavior — see Open Questions.
- **Band-selection redesign shipped (2026-07-17): `--band-selection
  {largest,mw-strict}`, default flipped to `largest`.** Acts on the
  empirical finding directly above. `largest` (new default): the biggest
  detected band in a lane always wins, regardless of MW — single band, no
  doublet-summing (a deliberate v1 simplification, see below). The ladder
  is still calibrated when possible, but purely to *verify* the selected
  band's MW against `--target-mw`, never to gate selection — a mismatch is
  flagged as a new 4th confidence value, `mw-mismatch` (with a real
  `purity_percent` *and* the mismatched `matched_band_mw` populated, unlike
  every other non-`mw-matched` tier). `mw-strict`: byte-for-byte the
  original 2026-07-13 behavior, verified identical on real output before
  and after the refactor, not just by code inspection. `--allow-heuristic`
  keeps its original role for the "ladder never calibrated at all" case in
  both modes; a calibrated-but-mismatched band is always reported
  regardless of it, since real (if disagreeing) information exists then.
  - **Architecture**: `_analyze_lane_detailed` (`purity/analysis.py`,
    reused identically by all 4 registered lane-geometry methods) split
    into two independent branches rather than interleaving a new condition
    through the existing logic — the `mw-strict` branch is untouched
    original code; a `_mw_within_tolerance` helper was factored out of
    `_match_target_band` so the same tolerance math serves both selection
    (`mw-strict`) and verification (`largest`) without semantic confusion
    at the new call site. `band_selection` threads through the *entire*
    existing `tolerance_percent`/`allow_heuristic` pass-through chain,
    including the public `analyze_lane` API (easy to miss — caught in
    design review, not by the first draft).
  - **Real gaps caught by design review before implementation, not
    after**: an earlier draft assumed only the `"mw-matched"`-asserting
    tests needed auditing for the default-behavior change; actual risk was
    broader — any existing `"not-found"` test where calibration succeeds
    but the band is out of tolerance silently becomes `"mw-mismatch"`
    under the new default instead. Two test sites needed
    `band_selection="mw-strict"` added for this reason, a third
    (`test_purity_debug_viz.py`'s not-found case) turned out to already be
    safe for an unrelated reason — its synthetic band was being cropped
    out entirely by the bottom-edge-artifact detection before band
    detection ever ran, confirmed by direct inspection rather than
    assumed.
  - **Debug rendering**: new gold/yellow `MISMATCH_BAND_COLOR` for the
    selected band's box; per-lane label shows both the calibrated and
    expected MW directly (e.g. "vs 58.2kDa expected) MISMATCH") so the
    flag is legible without relying on color alone. Confirmed via real
    rendering, not just code review — labels get visually cramped in
    narrow lanes now that they're longer, a pre-existing cosmetic
    limitation of the fixed-width per-lane label box, not something this
    change attempted to fix.
  - **Not done in this pass, deliberate v1 scope**: `largest` mode never
    sums a doublet (only `mw-strict` still does) — a confirmed, accepted
    tradeoff, not a bug, revisit only if it turns out to matter on real
    images; whether this generalizes beyond the 6 confirmed-purity images
    is still untested on the 9 confirmed-MW-only images.
- **`--target-mw` made optional; new `largest-unverified` confidence tier
  (2026-07-20).** Motivated directly by the Formulation & Purification
  Discovery batch below: every prior entry point (`analyze_image`/
  `_analyze_signal`/`analyze_lane`/`_analyze_lane_detailed`, and
  `purity/methods.py`'s `run_method`/`run_all_methods`/all 4 adapters)
  required `target_mw: float`, which breaks down for a batch spanning many
  different proteins with no per-image expected MW available. `target_mw`
  is now `float | None` everywhere; `None` is only valid with the default
  `--band-selection largest` (the largest band is still selected, and its
  real calibrated MW is still reported via `matched_band_mw` when the
  ladder calibrates — `confidence` becomes `"largest-unverified"` instead
  of `"mw-matched"`/`"mw-mismatch"`, since there's nothing to verify
  against). `--band-selection mw-strict` still needs `target_mw` to select
  a band at all — passing neither now raises `ValueError` from the library
  API and prints a clean `error: --target-mw is required with
  --band-selection mw-strict` (exit 2) from the CLI, rather than silently
  producing a nonsense result. `LaneResult.target_mw_expected` is now
  `float | None` to match. 5 new tests (`test_analyze_lane_largest_mode_
  no_target_mw_with_calibration_is_unverified` and 4 others spanning the
  library and CLI layers); all 79 pre-existing tests pass unchanged (84
  total). No debug-image color change needed — `largest-unverified` renders
  with the same green target-band color as `mw-matched`, since it's a real
  selected band, just unverified; the per-lane label's existing fallback
  format already omits any MW-comparison text when there's nothing to
  compare against.
- **Formulation & Purification Discovery batch: 1,321 real gel images
  copied and blind-analyzed (2026-07-20)** — the first real-scale test of
  this pipeline beyond the ~17-image curated set, using NEB's own
  Production image store rather than the smaller example sets used so far.
  - **Source and copy**: `/Volumes/Production/digital images/Formulation &
    Purification Discovery` (a network mount, ~200 protein/construct
    folders, 14,173 total `.jpg`/`.tif` files) — read-only for every
    operation (`os.walk`/`shutil.copy2`, never a rename/delete/write against
    the source). Per the end user's own filename-labeling-scheme note,
    candidate purity-gel images are those whose filename **or any parent
    folder name** (relative to the source root) contains `purity`, `PDEV`,
    `final`, `concentrated stock`, or standalone `CS` (case-insensitive) —
    matching on the full relative path, not just the bare filename, was a
    deliberate correction after an initial filename-only pass silently
    missed 64 real matches sitting in a keyword-named parent folder (e.g.
    `.../PDEV757 Stability Testing/ADD Screens/8.1.24 ... .jpg`) whose own
    filename didn't repeat the keyword. **1,321 files (~1.8 GB)** copied
    into `data/formulation_purification_discovery/` (mirroring each file's
    source-relative subfolder path), with a full source→dest manifest at
    `data/formulation_purification_discovery_manifest.csv`. Both the copy
    script (`scripts/copy_formulation_purification_gels.py`) and the data
    itself live under paths that are **gitignored** (`scripts/`, `data/`) —
    reproducing this from a fresh clone means re-running the script against
    the mount, not `git pull`; the manifest/JSONL results described below
    are the durable record of what this pass actually found, kept on local
    disk only.
  - **Known adjacent noise, not a bug**: the keyword rule also catches
    non-purity images incidentally sitting in a matching folder (Titer,
    Bradford, ADD-stability-screen shots) — confirmed directly, not just
    suspected: 2 images that crashed every method turned out to be Bradford
    assay photos (a colorimetric concentration assay, no lanes at all),
    swept in only because "CS" appeared in the filename; `detect_lanes`
    correctly found zero lanes and the adapter reported a clean error
    rather than fabricating a result. **A real employee name appeared in
    the source folder structure** (a personal-name-containing subfolder
    under the mount) — never write that name into this file or any other
    committed content (see Working Agreements); it's fine as-is on local
    disk since `data/` is gitignored, but future work touching this same
    data source should expect more instances of the same pattern.
  - **No per-image target MW or confirmed ladder identity exists for this
    batch** (~200 different proteins) — deliberately not pursued via a
    per-protein MW lookup; Jacob judged that a "significant amount of
    work"/wild-goose-chase relative to what a first blind pass actually
    needed, so this run uses `target_mw=None` (see the entry above) and a
    **per-image ladder guess** instead of assuming one globally: the cheap
    `rectangle` method (~0.01s/call, negligible next to the ~2-11s/image
    the 4-method pass costs) is tried against both `P7719` and `P7717`;
    whichever actually calibrates (or has the higher R² if both do) is used
    for the real pass, `None` if neither does. **Jacob separately confirmed
    with the end user (2026-07-20) that P7719/P7717 are the *only* two
    ladders in use here** — not just "at least 2" as the 2026-07-14
    `QUESTIONS_FOR_USERS.md` answer had left open — so a `None` guess-result
    is real information (neither known ladder fits this image), not a
    symptom of guessing from an incomplete list.
  - **Script**: `scripts/batch_analyze_formulation_purification.py` (also
    gitignored). Runs the ladder guess then `run_all_methods` (all 4
    methods, `allow_heuristic=True`, `band_selection="largest"`) per image,
    writing one JSON record per image to
    `data/formulation_purification_discovery_analysis.jsonl` (append-as-it-
    goes, so a killed run loses nothing already written; resumable — a
    rerun skips images already present). Per image, also computes flags:
    `not_found`/`low_signal` per lane/method, `method_disagreement` when
    two methods' purity % for the same lane differ by more than 25 points
    (only meaningful because all 4 methods share `detect_lanes`'s lane
    count/identity unchanged — true for all 4 registered methods, not yet
    true for a future `blob_graph`-style method, see the
    `lane_numbering_caveat` field), `lane_count_mismatch`, and
    `method_crashed`. Full run: 1,321 images, **zero fatal errors**, ~2.5
    hours wall-clock (dominated by `ridge`/`snake`'s per-lane filtering
    cost on real image sizes).
  - **Results — the real news is prevalence, not novelty of the failure
    mode.** Ladder calibrated on 1,171/1,321 images (88.6%: 807 as P7717,
    364 as P7719); 150 (11.4%) matched neither. **952 images (72.1%) hit a
    genuine structural red flag** (`method_disagreement`, `not_found`,
    `no_ladder_calibrated`, or `method_crashed` — excluding `low_signal`,
    which is expected dilution-detectability behavior, not itself a
    problem); only 69 (5.2%) were fully clean. **Method disagreement alone
    hit 55.8% of images** (>25pp purity swing between methods on the same
    lane) and **not-found hit 34.4%** (a lane with literally zero
    detectable bands in at least one method). Broken down by which keyword
    matched: **images with `purity` literally in the filename had the
    *highest* structural-flag rate (80.3%)** — higher than `cs` (70.5%),
    `final` (60.9%), or `pdev` (49.5%). This is the opposite of what a
    "loose keyword swept in bad matches" explanation would predict, and is
    the single most important finding of this pass: **cross-method
    lane-geometry disagreement is pervasive on real, unambiguously-labeled
    purity gels at scale**, not an artifact of the original ~17-image
    curated set's hard cases. This is a much larger, better-evidenced
    version of the open question already logged in `AGENTS.md`/
    `QUESTIONS_FOR_USERS.md` (band/lane *identification*, not lane shape,
    as the real accuracy ceiling) — it doesn't resolve that question, but
    it substantially raises confidence that it's real and general.
  - **Debug-image triage sample generated and visually reviewed (same
    day)**: 105 of the 952 flagged images selected (`scripts/generate_
    triage_debug_images.py`, gitignored) — top-40 most-severe from `purity`
    (largest + worst category), top-15 each from `cs`/`final`/`pdev`, 10
    `no_ladder_calibrated` examples, 10 clean-baseline images for contrast.
    412 debug PNGs rendered under `data/formulation_purification_discovery_
    debug/` (gitignored) plus an index CSV; 1 image failed on a TIFF
    compression codec (`CCITTFAX4`, needs the optional `imagecodecs`
    package) — a second Bradford-assay non-gel photo, same root cause as
    the 2 method-crash cases above, not worth a new dependency to fix for
    one non-gel image.
  - **Visual inspection of the sample found 3 distinct root causes, 2
    already known and 1 new** — see `data/formulation_purification_
    discovery_review.md` (and its companion `.pptx`, both gitignored, built
    for distribution) for the full writeup with annotated debug-image
    examples:
    1. **Continuous-smear fragmentation (the dominant driver)** — a wide,
       diffuse sample smear (an overloaded/degraded sample, or several
       near-identical fractions loaded side by side) repeatedly gets sliced
       by `detect_lanes` into many thin fake lanes, each reporting a
       fabricated-looking purity % read off background noise. Confirmed
       `viterbi`'s curved tracing doesn't help here — with no real vertical
       structure in a blank region its path runs straight and it still
       reports noise-driven bands. This is a lane-*count* problem none of
       the 4 registered methods can fix, since all 4 reuse `detect_lanes`'s
       count unchanged — the existing 2026-07-14 fragmentation fix
       (`_merge_fragmented_runs`) evidently doesn't generalize to this
       pattern at real scale.
    2. **Loading-well/comb-fringe leakage on non-standard well geometry**
       (a new instance of a known problem) — on at least one real image,
       `detect_comb_fringe_end`'s adaptive top-crop leaves dark well/
       loading-point marks visible well inside several lane boxes rather
       than cropping them out, confusing band detection near the top of
       the lane. The adaptive crop was validated against the original
       example set's comb/well geometry, which apparently doesn't cover
       every real physical comb/well style in production use.
    3. **Burned-in text/number annotations interfering with lane
       detection — corrected 2026-07-21, not actually new.** Originally
       written up as a brand-new failure mode; a docs cleanup pass found
       it isn't. `251017_..._FusionProtein.tif` (Data Inventory) was
       already noted 2026-07-13 as having "dilution-fold labels burned
       in," and that same image's lane over-detection (13 lanes found vs.
       ~9 real ones) was root-caused only to generic "lane over-detection"
       at the time — the mechanism was never identified. This batch's much
       more heavily-text-covered example makes a strong case that
       burned-in text was the previously-unexplained cause all along,
       **retroactively resolving a real open item from 2026-07-13**, not
       introducing a new one. Several production images have concentration
       labels or protein names burned directly into the photo pixels (not
       a separate caption); `detect_lanes`'s column-profile approach picks
       up the text's strokes/gaps as if they were real lane content,
       producing implausibly narrow spurious lanes at character
       boundaries — on the inspected example, this also coincided with the
       ladder failing to calibrate against either known ladder. Any fix
       would need to detect and mask burned-in text before column-profile-
       based lane detection runs, not just adjust an existing threshold.
    - **Scope note**: this is a structural/consistency check, not an
      accuracy check — no confirmed ground truth exists for this batch, so
      "method disagreement" tells us the methods disagree, not which one
      (if any) is right. **No design decisions were made from this
      review** — it's reporting only; candidate directions (masking
      burned-in text, a more general well-fringe detector, flagging
      "smear vs. distinct bands" before over-segmenting) are listed in the
      writeup but not evaluated against each other or decided on.
- **Human-in-the-loop band-selection prototype, Phase 1 (algorithm only,
  2026-07-21, branch `human-in-the-loop-band-selection`) — built and
  tested, but the evaluation is an inconclusive null test, not a negative
  result.** Followed directly from the reassessment above: rather than
  building the throwaway UI first, an approved plan (see
  `/Users/jmiller/.claude/plans/zesty-wandering-bentley.md`) tested the
  algorithmic core — does propagating a human-identified target band to
  the rest of a dilution series by row position actually help — with zero
  UI code, against all 15 confirmed ground-truth images, deferring the UI
  to a Phase 2 gated on Phase 1 showing real signal.
  - **Built**: `core/lanes.py` gained `apply_lane_corrections` (one atomic
    merge/drop transaction against `detect_lanes`'s original output, not a
    queue of incremental ops — avoids index-drift ambiguity). New
    `core/band_propagation.py`: `absolute_row` (reuses the exact
    `position_offset`/`top_bound` convention `purity/analysis.py` and
    `purity/debug_viz.py` already use, not a third independent copy),
    `find_nearest_band` (tolerance derived from the image's own resolving
    height, never a fixed pixel constant; an ambiguity gate returns `None`
    rather than a confident wrong pick when two candidates are nearly
    equidistant; also serves as the reference-click-snapping logic),
    `propagate_target_band` (explicit `series_lanes` selection, since real
    images in this project mix dilution series with non-series lanes like
    embedded standards). 16 new unit tests, 100/100 passing overall.
    Standalone experimental module, not wired into `METHOD_REGISTRY`.
  - **Correction records authored systematically, not freehand** (see
    `scripts/author_hitl_correction_records.py`): a mechanical policy —
    drop lanes wider than 1.8x the image's own median lane width or with
    zero bands; reference lane = largest single band among lanes whose
    comb-fringe crop looks trustworthy; series lanes = everything else
    surviving. Deliberately blind to confirmed purity/MW values (uses only
    structural signals), which matters for the bias caveat the approved
    plan flagged — a real human or a model would still need to be tested
    separately, but this specific pass isn't reverse-engineered from the
    answer.
  - **The evaluation result is a null test, not evidence against the
    hypothesis — important to state precisely.** The policy's "reference
    band" choice is mechanically identical to today's automatic `largest`
    rule, so on the one lane per image with real confirmed ground truth,
    human-assisted and automatic produced *identical* numbers in all 6
    purity images (mean error 16.2pp, both). This never tested whether an
    independent human pick beats the heuristic — it tested whether
    reimplementing the same heuristic differently changes anything, which
    it structurally cannot. On the *other* lanes in each series (where
    propagation does something automatic doesn't), there is no per-lane
    ground truth at all — only one confirmed value per image — so
    propagated results could only be compared *against automatic's own
    guess*, not against truth. They often differ substantially (e.g.
    R-217 lane 6: auto 40% vs. propagated 11%), but which is more correct
    is genuinely unknown from this data.
  - **A real secondary finding, arguably more actionable than the original
    question**: authoring the correction records surfaced that the
    comb-fringe-crop failure (`detect_comb_fringe_end` falling back to the
    trivial ~2% margin instead of finding the real fringe) is far more
    widespread across this 15-image ground-truth set than the single
    example in the Formulation & Purification batch review suggested —
    often affecting most lanes in an image, with several showing
    suspiciously uniform high "matched MW" values (180-300 kDa) across
    *different* proteins/images, consistent with all of them independently
    hitting the same mis-cropped well artifact rather than distinct real
    high-MW contaminants. Not root-caused or fixed; flagged here since it
    has concrete evidence of causing bad results independent of the
    human-in-the-loop question.
  - **Also found**: propagation's honest-miss rate is high (roughly a third
    to two-thirds of series lanes per image return `None` rather than a
    match) — consistent with a Plan-agent-flagged risk that a
    fixed-fraction-of-resolving-height tolerance may not survive real gel
    curvature/smiling, not yet evidence either way on the core hypothesis.
  - **Phase 2 (the throwaway Flask/canvas UI) was NOT built as a next
    step from Phase 1's signal** — instead, Jacob proposed the direct fix
    once he understood what Phase 1 had actually tested (a mechanical
    stand-in, not a real human): build a real interface so a genuine,
    independent human judgment call replaces the stand-in. Built the same
    day, see the entry immediately below.
- **Human-in-the-loop review UI, Phase 2 (2026-07-21, same branch) —
  built and verified end-to-end, real usage not yet done.** A real
  interactive tool, not another algorithm test: a person draws/deletes
  lane boxes and marks the target band themselves; the server snaps that
  click to a real detected band and propagates it across the series using
  the exact same tested Phase 1 code (`band_propagation.py`,
  unmodified). See `/Users/jmiller/.claude/plans/zesty-wandering-bentley.md`
  for the full plan and [[human_in_the_loop_prototype_phase1]] for the
  Phase 1 context this responds to.
  - **`scripts/hitl_ui_server.py`** (Flask, `uv run --with flask` — ad hoc
    dependency matching the `python-pptx` precedent, not added to
    `pyproject.toml`) + **`scripts/hitl_ui_static/review.html`** (vanilla
    JS/canvas, no framework). `GET /?image=<name>` serves any number of
    images passed at startup with prev/next-style switching (no restart
    needed between images); `POST /analyze` runs the human's corrected
    lanes through `purity/analysis.py`'s `_default_crop_lane` (private,
    already cross-module-imported elsewhere in this codebase, e.g. by
    `purity/methods.py` — not a new pattern), `core.bands.detect_bands`,
    `core.ladder.calibrate_ladder`, and the tested `band_propagation`
    module, then writes a correction-record JSON to `data/hitl_correction_
    records/` in the exact schema `scripts/evaluate_human_assisted_
    propagation.py` already reads — that script is reused **unmodified**
    for the real comparison once real sessions exist.
  - **Never accepts or displays a confirmed target MW or confirmed
    purity** — deliberate, to avoid repeating Phase 1's bias trap; any
    comparison against a known answer happens separately, after using the
    tool.
  - **Anti-bias constraints built in, not left as polish**: no detected-
    band hints, counts, or areas shown before the person clicks Analyze
    (that's exactly the signal the `largest` heuristic runs on); no
    pre-selected reference lane; a second contrast preset (percentile-clip
    "boost faint signal," alongside the default global min/max stretch
    `debug_viz.py` already uses) so a lane `detect_bands` can see via local
    baseline correction isn't invisible to the human under one fixed
    global stretch (removed 2026-07-22 as not useful in practice, replaced
    by an "invert (negative)" preset -- see the Phase 2.1 entry below).
  - **Explicit no-match reasons, not blank cells**: a `None` propagation
    result (the median case for several lanes per image, per Phase 1)
    reports *why* — "no band within tolerance," "ambiguous -- two
    candidates too close to call," or "no bands detected in this lane" —
    verified working end-to-end against a real image (see below).
  - **Verified end-to-end** against `8.6.25 Protein Purity.tif` (a
    known-good case): page loads, all 10 auto-detected lanes render,
    marking a real reference band and submitting correctly reproduced a
    sane result on the reference lane (58%, 27.9 kDa vs. the pipeline's
    own earlier 58%/28.75 kDa on the same image) and produced all three
    distinct miss-reason types across the rest of the series; a
    deliberately-wrong click (row 20, no real band nearby) correctly
    returned a 400 error rather than a fabricated result; a 2-image
    startup confirmed switching images via `?image=` works without a
    restart. **What wasn't and can't be verified by me**: whether a real
    person's independent judgment through this tool actually beats the
    automatic heuristic — that requires Jacob (or someone else) to use it
    with genuine visual judgment, which hasn't happened yet.
  - **Deliberately out of scope for this pass**: vertical crop bounds
    still come from the existing adaptive comb-fringe/bottom-edge
    detection, not human-editable — a real, known limitation given Phase
    1 found comb-fringe-crop failure is a large, confirmed error source in
    this exact ground-truth set; per-lane edge-drag handles for precision
    nudging; multi-point centerline/curve tracing for smiling lanes.
  - **Usability follow-ups (same day, after Jacob's first real use)**: an
    on-page collapsible instructions panel, and every lane now shows a
    visible number on its box (ladder = "Ladder"; sample lanes 1, 2, 3...
    left to right, recomputed live) — replacing raw x-coordinate ranges
    everywhere (checkboxes, ladder dropdown, results table, reference
    readout). Also clarified: unchecking "include in series" keeps a lane
    visible/selectable but skips it during propagation, while deleting
    removes it entirely; exactly one band click is needed total, in one
    reference lane, never one per lane. **Known current limitation**: no
    way to manually override a single lane's propagated result without
    re-marking the whole image's reference — a real gap if it turns out to
    matter, not yet built. (Partially resolved 2026-07-22 by "Delete band,"
    see the Phase 2.1 entry below — a lane's result can now be corrected
    without re-marking the reference, but only by discarding a wrong band
    to "unmatched," not by picking a specific different one.)
  - **Committed 2026-07-21** (`3ca7b2f` on `human-in-the-loop-band-
    selection`) — only the tested algorithm (`core/band_propagation.py`,
    `core/lanes.py`'s `apply_lane_corrections`) plus docs/tests, verified
    clean of any Flask/server/networking code beforehand. The Flask
    server/UI itself stays gitignored under `scripts/`, matching this
    project's standing `scripts/`/`data/` convention. **Confirmed with
    Jacob**: this local prototype is explicitly disposable, for validating
    the interaction model only — if the human-in-the-loop direction pans
    out from real usage, the real integration path is `ebase` (see Design
    Decisions' hosting entry), not this Flask app.
- **Fixed, 2026-07-22: cross-lane crop-artifact corroboration for
  `band_selection="largest"`.** See Known Limitations' entry for the bug
  itself (a broad crop-boundary leftover winning "largest" on area alone,
  confirmed on `8.6.25 Protein Purity.tif` lanes 4-8, ~137-218 kDa instead
  of the real ~29 kDa) -- this entry is the fix and how it was validated.
  Two candidate fixes were tried and rejected first, calibrated against all
  15 real ground-truth images before either was trusted: (1) exclude any
  band whose width is a strong outlier vs. the *median* width of every
  other band in its own lane -- too blunt at every multiplier tested (2.0
  through 4.0): still failed to fix the target lanes at the threshold
  needed to avoid collateral damage, and broke multiple other real images
  along the way (worst case, `R-236_PID1502_PDEV1580_98.5pct...` dropped
  from 98% purity, matching its confirmed 98.5%, to 2%, on 3 lanes). (2) a
  narrower single-lane rule (exclude only a band that's simultaneously the
  widest AND closest to the crop boundary in its own lane) improved but
  still regressed several unrelated images and still badly damaged the
  same confirmed 98.5%-purity case. **What actually worked**: the same
  single-lane "widest AND closest to top" candidate from attempt (2), but
  only acted on when the *same* candidate band (by absolute row range, ±10
  px slack) recurs across at least half the image's sample lanes (minimum
  3) -- i.e. corroborated across the image, not judged from one lane in
  isolation. Calibrated result: touches only 2 of the 15 ground-truth
  images (the one with the confirmed bug, plus one other where it only
  ever *increases* purity% via a more accurate denominator, never changes
  which band is selected), and leaves the other 13 -- including the
  98.5%-confirmed case -- completely untouched. Re-verified in
  `band_selection="mw-strict"` mode too: every `matched_band_mw` across all
  15 images is byte-identical before/after; the fix only removes the
  artifact's bogus area from the purity% denominator, never changes which
  band gets MW-matched. Implementation: `_suspect_crop_artifact_band` (the
  per-lane candidate) and `_corroborated_crop_artifact_bands` (the
  cross-lane agreement check) in `purity/analysis.py`; `analyze_image` now
  runs lane cropping/band-detection in two passes (corroborate first, then
  finalize each lane) instead of one, so `_analyze_lane_detailed` (new
  `exclude_bands` param) can drop the corroborated band from both the
  purity% denominator and target-selection eligibility before either
  selection branch runs -- benefiting `mw-strict`'s purity% too, not just
  `largest`'s selection. 7 new tests (2 real-data regression + integration,
  5 unit on the new helpers); a full synthetic multi-lane reproduction was
  attempted and abandoned -- `make_synthetic_gel`'s simple gaussian bands
  either get absorbed by baseline correction (if smooth/wide) or produce
  nested duplicate peak detections a real photographed gel doesn't (if
  built from stacked narrower gaussians) -- the real-image regression test
  is the trustworthy check here, not a synthetic one. 107/107 tests pass.
- **HITL review UI, Phase 2.1 (2026-07-22, same day as Phase 2) --
  delete-band correction, bulk lane clearing, contrast invert.** Three
  small UI additions to `scripts/hitl_ui_server.py`/`scripts/hitl_ui_static/
  review.html`, prompted by real usage surfacing gaps Phase 2 didn't cover.
  - **Delete band** (new toolbar mode, enabled only after a successful
    Analyze): clicking any shown band box removes it from that lane's
    purity-percent denominator via a new tested `exclude_deleted_bands`
    helper in `core/band_propagation.py`. If the deleted band was the one
    selected as the target, the lane falls back to "unmatched" rather than
    silently promoting a different candidate -- a deliberate choice, not an
    oversight (a confidently-wrong auto-substitute defeats the point of a
    human-verified pick). Persisted into the correction-record JSON (new
    `deleted_bands` field) so `evaluate_human_assisted_propagation.py`'s
    replay honors it too, once real (not mechanically-authored) records
    carry it. Directly the fix referenced by Phase 2's "Known current
    limitation" note above.
  - **Delete all lanes** button: bulk version of the existing per-lane
    Delete-lane mode, for redrawing every lane by hand rather than one at a
    time.
  - **Contrast "invert (negative)"** button added; the "boost faint
    signal" percentile-clip preset from Phase 2 was removed the same day
    at Jacob's request as not useful in practice -- contrast options are
    now just default / invert, both applied only to the background gel
    image so lane/band overlay colors stay legible against either.
  - Separately, same day: `find_nearest_band`/`propagate_target_band` in
    `core/band_propagation.py` gained a `require_unambiguous` parameter --
    see "Ambiguity-gate tolerance" in Known Limitations for the full
    story (real usage found the ambiguity safeguard rejecting visually
    obvious correct matches on both the reference click and series-lane
    propagation).
  - Committed to git: only `core/band_propagation.py`'s
    `exclude_deleted_bands`/`require_unambiguous` changes plus tests/docs
    (commit `a2ae7a0`). The Flask server/UI changes themselves stay
    gitignored under `scripts/`, same as Phase 2.
  - Also this session: removed two pieces of genuinely dead code found
    during a full unused-code audit -- `core/snake_lanes.py`'s
    `SnakeTrace` dataclass (never constructed; the module's real pipeline
    entry point returns a raw tuple instead) and `purity/output.py`'s
    `format_json` (never called; `purity/cli.py` builds JSON output via
    `to_payload()` + `json.dumps()` directly). Confirmed via `ridge_lanes.py`'s
    similarly-low-usage `analyze_image_ridge` that NOT everything with few
    callers is dead -- that one is an intentional bypassed prototype path,
    documented as such in `_run_ridge`'s own comments, and was left alone.
  - **Two further real-usage fixes, 2026-07-22, same prototype:**
    (a) the "Show band pixels" debug-overlay checkbox could get stuck
    permanently on once "Delete band" mode was entered, because `redraw()`
    forced the overlay on for that mode instead of just defaulting the
    checkbox once as a convenience -- fixed by making the checkbox the sole
    authority in `redraw()`'s condition, with `setMode()` auto-checking it
    (not re-forcing it) only the first time Delete-band mode is entered;
    (b) after "Delete all lanes" (or manually deleting every lane down to
    zero), the first lane drawn back in didn't automatically become the
    ladder lane, forcing an annoying toggle-off/toggle-on of the ladder
    dropdown -- fixed so the first lane drawn from an empty lane list
    defaults to `is_ladder: true` (any lane drawn after that one never
    reassigns the ladder), and the dropdown's option labels were renamed
    from "Position #" to "Lane #" for clarity. Both fixes are
    `scripts/`-only, gitignored, same as the rest of Phase 2/2.1.
- **HITL productionization design opened as GH issue #1, 2026-07-23.**
  After enough real usage of the Phase 2/2.1 prototype to be confident the
  interaction model works, the next step is packaging it as an embeddable
  widget in `ebase` (the destination this section's Phase 2 entry already
  named) rather than continuing to extend the disposable Flask/canvas
  prototype. Investigated `plate-map` (the existing `ebase`-embedded
  well-set editor) as the reference precedent, plus `ebase`'s own
  `plate_color_extractor` integration (a sibling Python repo cloned by
  Capistrano for real image processing) as the closest existing shape for
  a Python-backed tool like this one. Full design -- why this doesn't get
  ported to JS, why the JS side ships as plain vendored ES modules with no
  bundler rather than an npm package, the productionized API's cache-
  isolation/concurrency/auth/versioning/idempotency requirements, and the
  remaining open questions (DB schema, process supervision) -- is tracked
  in **GH issue #1** (https://github.com/nebiolabs/neband/issues/1),
  not duplicated here. Nothing from that design is implemented yet.
- **Project renamed `gel-image-extractor` -> `neband`, 2026-07-23.** Once
  the packaging goal was clarified as "reusable/open-sourceable in any
  context, `ebase` is the first validated consumer, not the only one" (see
  GH issue #1), a real product name mattered more than it did for a
  disposable internal tool. Landed on **NEBand** (NEB + "band," the actual
  gel-band terminology) after a naming brainstorm; `neband` (lowercase) is
  the actual repo/package/CLI slug, matching npm/PyPI naming conventions
  and `plate-map`'s own lowercase-hyphenated precedent. Scope of the rename
  (deliberately chosen as "everything now" over "just the GitHub repo
  container," once asked which was wanted): GitHub repo
  (`nebiolabs/gel-image-extractor` -> `nebiolabs/neband`, GitHub
  auto-redirects the old URL), local git remote, `pyproject.toml`'s
  package name, the CLI command (`gelx` -> `neband`, same subcommands,
  no functional change), and the internal module
  (`src/gel_extractor/` -> `src/neband/`, every import updated). 112/112
  tests still pass post-rename. The repo is public on GitHub but has no
  LICENSE file yet — a real gap given the stated open-source goal, flagged
  but not yet resolved (license choice deferred, see Open Questions).

## Planned Features — Not Yet Built

Requested, agreed-on-in-principle, but not yet implemented — don't build
without confirming scope first (see Working Agreements).

- **Second (class/method call-flow) mermaid diagram** — see Architecture
  Diagram section below; tracked in memory
  ([[planned_call_flow_diagram]]), not yet built.
- **End-user-facing band-visualization debug view in `ebase`** — requested
  2026-07-22, after Jacob used the human-in-the-loop prototype's new
  debug toggle (target band bright green, every other detected band
  bright magenta, filled directly on the image) and confirmed it's
  independently valuable to end users, not just a developer debugging
  aid. Confirmed no git action needed right now, just that the capability
  must not be lost: `src/neband/purity/debug_viz.py` already does
  the outline-style version of this, git-tracked, wired to `--debug` — that
  is the durable home for the requirement when this reaches `ebase`, not
  the disposable HITL prototype. See memory `ebase_hosting`. Tracked
  alongside the broader productionization design in GH issue #1 (see
  Implementation Status above).

## Known Limitations — Flagged for Later

Real, open items surfaced during implementation that haven't been resolved
yet. Don't silently fix or dismiss these without discussing first — they're
recorded here specifically so they aren't lost or re-litigated from scratch.

- **Stale as of 2026-07-16, corrected 2026-07-21: curvature/smiling now has
  partial handling, bleed-over still doesn't.** (The vertical top/bottom
  bound problem — comb/well fringe and the bottom cassette/tape edge — is
  resolved; lane *fragmentation* got a validated partial fix 2026-07-14;
  both see Implementation Status.) This entry originally said lane capture
  had no curvature handling at all — no longer true since `viterbi`/
  `ridge`/`snake` (Implementation Status, "Multi-method lane detection")
  all attempt curved tracing. What's still real: none of the 3 measurably
  improves purity/MW accuracy over the straight-rectangle baseline on
  confirmed ground truth (see Implementation Status's curve-tracing and
  6-alternative-methods entries) — curvature was never the accuracy
  bottleneck, band/lane *identification* is (reinforced at much larger
  scale by the 2026-07-20 Formulation & Purification Discovery batch, see
  below). Bleed-over from a neighboring lane when bands are wide/diffuse
  remains completely unaddressed by any method tried. See Implementation
  Status's dedicated entries for full detail, including confirmed dead
  ends, before re-attempting anything here.
- **Dilution-detectability threshold skews purity at high dilution —
  confirmed real, partially mitigated, not fixable outright.** As dilution
  increases, faint contaminant bands drop below the detection floor before
  the target band does, inflating apparent purity. Not primarily a
  tunable-parameter bug — the user confirmed this is an expected,
  fundamental limit-of-detection effect. Flagged via `LaneResult.low_signal`
  rather than reported at face value (see Implementation Status). Still
  open: the flagging threshold (`DEFAULT_LOW_SIGNAL_FRACTION = 0.2`) is an
  unvalidated placeholder, and the alternative idea (most-concentrated lane
  as sole authoritative measurement) wasn't pursued.
- **R-236 lots' MW-migration discrepancy** (`PDEV1452`, `PDEV1495`) —
  confirmed real, ~40-50 kDa gap between calibrated position and confirmed
  MW, not root-caused. See Implementation Status's pptx-comparison entry for
  candidate explanations. Deferred, marked out of MVP scope 2026-07-14 —
  revisit post-MVP.
- **TelA's ladder lane fails to calibrate entirely** (`7.17.24 PDEV772 Conc
  Stock.jpg`) — confirmed genuinely low-contrast/low-band-count in this
  specific scan, not a detection-parameter issue.
- **Ground truth is still thin relative to the full example set — corrected
  count 2026-07-21**: 15 of the ~17 curated real images have a confirmed MW/
  protein identity (only 6 of those also have a confirmed purity %); the
  rest only confirm the calibration *machinery* runs, not that the reported
  purity % is correct. This is now a much smaller problem than the same
  issue at the scale the 2026-07-20 Formulation & Purification Discovery
  batch operates at: **zero of its 1,321 images have any confirmed purity/
  MW at all** (see Implementation Status) — that batch's findings are
  structural/consistency signals only, not accuracy validation.
- **Confirmed real correctness bug, 2026-07-22: `band_selection="largest"`
  (the default) can select a top-of-gel artifact as the target band,
  not a real protein.** Surfaced via the HITL prototype's new debug
  overlay on `8.6.25 Protein Purity.tif`: `detect_comb_fringe_end`
  correctly crops past the comb teeth themselves, but a separate, broad
  (~80-90 row), roughly uniform-intensity region immediately follows in
  every one of the 8 sample lanes at nearly identical extent regardless
  of dilution -- Jacob confirmed by eye he doesn't recognize it as a real
  physical feature (unlike the bottom cassette/tape-edge shadow, which he
  did confirm is real and physical). Running the actual automatic pipeline
  (`target_mw=None`, `band_selection="largest"`) on this image confirms
  the bug is live, not hypothetical: lanes 4-8 select this artifact as
  their "largest" band and report 137-218 kDa, while a real human click on
  lanes 1-3 (via the HITL tool) finds the true target at ~59-60 kDa. A
  width-outlier survey across all 15 ground-truth images (first-detected-
  band width vs. other-bands'-median-width per lane) showed this is real
  but *not* a fixed-position pattern: in several other images (e.g.
  `6.12.26 PDEV1718 Protein Purity.tif`) the first band after crop is
  narrow/legitimate and it's *later* bands that are the wide outliers
  instead -- ruling out a blanket "skip whatever's right after the comb"
  position-based rule. **Fixed 2026-07-22 for lanes 4-7 of this image**
  via cross-lane corroboration -- see Implementation Status's dated entry
  for the fix, the two rejected candidate approaches, and full validation.
  **Still open: lane 8 of this same image remains wrong** (still reports
  217.8 kDa) -- its version of the artifact never satisfied the
  corroboration rule's per-lane "widest AND closest to top" precondition,
  a separate, not-yet-understood case within the same broader problem.
  Affects every analysis using the `largest`/`largest-unverified` path --
  which includes the entire 2026-07-20 Formulation & Purification
  Discovery batch -- so this may partially explain that batch's 72%
  structural-flag rate rather than being a new, separate issue; the fix's
  effect on that batch specifically hasn't been re-measured.
- **Confirmed real, tabled 2026-07-22: burned-in caption text can be
  detected as a real band, not just confuse lane detection.** Surfaced
  visually via the HITL prototype's debug overlay on two real images
  (`8.6.25 Protein Purity.tif`, `10.31.25 PDEV1437.tif`) -- a caption like
  "HpyCH4IV PID1284" or "R-218 TET3 / PDEV1437", burned directly into the
  photo pixels in the lower-left corner, sits within one or more lanes'
  column range and gets picked up by `detect_bands` as if it were real
  signal. Distinct from (but related to) the already-documented
  lane-*detection* instance of burned-in text above -- this is
  band-*detection* picking up text that survives inside an otherwise
  correctly-detected lane. **Not the same bug the cross-lane
  corroboration fix (above) addresses** -- that fix only catches a band
  that's simultaneously widest and closest to the *top* crop boundary in
  its own lane; a caption low in the frame doesn't match that shape.
  Jacob confirmed: the caption's location is inconsistent enough (it can
  overlap the ladder lane in one image, sample lanes in another) that a
  fixed positional mask isn't reliable on its own. Three pixel-statistic
  heuristics were tried and rejected, calibrated against real caption
  regions across both images before any code was written: (1) cross-lane-
  width intensity variance (text strokes vs. smooth band) -- backwards on
  3 of 5 lanes tested; (2) signal bleeding into the empty gaps between
  lanes (real protein never does this) -- no clean separation, background
  intensity drift swamps the effect; (3) 2D gradient/edge density (sharp
  text strokes vs. smooth gaussian blob) -- inconsistent, because a
  caption isn't uniformly "text" across its width -- a lane's column-slice
  through it can land on a letter stroke or on blank space between
  letters, and averaging over the lane's width washes out the difference
  either way. **Tabled, not solved.** The real fix is believed to be
  genuine text detection (e.g. OCR via `pytesseract`) to locate and mask
  the actual text bounding box regardless of position, rather than
  inferring "text-ness" from band shape/statistics -- a real scope
  increase (new Python dependency, a new system-level `tesseract` binary,
  a new masking step before lane/band detection runs), not a quick
  follow-up. Worth factoring into the `ebase` hosting discussion
  ([[ebase_hosting]]) if pursued, the same way the SAM/torch dependency
  weight was.
- **Ambiguity-gate tolerance (`DEFAULT_ROW_TOLERANCE_FRACTION`): both the
  reference click and series-lane propagation now bypass it entirely,
  2026-07-22.** Jacob hit "ambiguous --
  two candidates too close to call" repeatedly clicking reference bands via
  the HITL tool, including a case (screenshotted) where the crosshair
  landed squarely on the correct band but a smaller adjacent band still
  tripped the check. Root cause, first diagnosed earlier the same day:
  `band_propagation.find_nearest_band`'s tolerance (`row_tolerance()`, a
  fixed 5% of a lane's resolving height by default) was controlling two
  opposing things from one constant: (a) how far a click can be from a real
  band and still count at all, and (b) -- via `tolerance / 2` -- how much
  separation two candidate bands need before one wins over the other. The
  first pass concluded the *constant itself* wasn't safely tunable without
  a full 15-image calibration pass (shrinking/growing it just traded one
  false-refusal mode for the other). Resolution reached here is narrower
  and doesn't touch the constant: `find_nearest_band` gained a
  `require_unambiguous: bool = True` parameter, and `hitl_ui_server.py`'s
  one direct-human-click call site (matching the reference lane's own
  click) now passes `require_unambiguous=False`, so it just snaps to the
  nearest band within tolerance and skips the ambiguity check outright.
  `propagate_target_band`'s per-lane series matching *also* now passes
  `require_unambiguous=False` (same-day follow-up, once Jacob noticed
  series lanes were still coming back blank/unmatched on the same kind of
  close-candidate case the reference click had just been fixed for): the
  original reasoning for treating the two call sites differently --
  propagated lanes have "no direct visual confirmation, unlike the click
  itself" -- doesn't actually hold, since every lane's result (reference or
  propagated) is shown via the same band overlay and correctable with the
  same "Delete band" action. So there's no real distinction left between
  "a human clicked this" and "this was propagated" for ambiguity-tolerance
  purposes, and both call sites now behave identically: snap to the
  nearest band within tolerance, full stop. The `nearest_distance >
  tolerance` cutoff (genuinely nothing near the expected row in that lane)
  is unchanged and still returns `None` everywhere -- only the "two
  candidates too close together" rejection was ever in question. **Change
  made and shipped** (see `find_nearest_band`/`propagate_target_band` in
  `core/band_propagation.py`); the `DEFAULT_ROW_TOLERANCE_FRACTION` value
  itself is still unretuned pending that calibration pass, should it ever
  be worth doing.

## Open Questions

This section is for questions Jacob and Claude can resolve through design
discussion alone. Questions that need an answer from the domain-expert end
users (the project submitters, reviewers, etc.) instead live in
`QUESTIONS_FOR_USERS.md` — check there for the current accrued list before
assuming a piece of domain knowledge (e.g. "is this ladder the standard one")
rather than guessing.

- **What license should `neband` use?** Raised 2026-07-23 alongside the
  rename — the repo is public on GitHub with no LICENSE file, so nothing
  is actually legally reusable despite being visible, a real gap given the
  stated open-source goal. NEB's own `plate-map` repo uses
  AGPL-3.0-only, which may reflect an actual NEB policy for open-source
  projects rather than a one-off choice — worth checking before assuming
  either way. Deferred, not decided.

- ~~Is the 3-method shortlist... confirmed, or still just a proposal?~~
  **RESOLVED 2026-07-16**: Jacob decided to develop **all 6** prototypes
  from the Workflow exploration (not just the 3-method shortlist), with
  `sam-zeroshot` specifically deferred until its 2 confirmed bugs are
  fixed — see the "Multi-method lane detection, Phase A shipped" entry in
  Implementation Status for the resulting architecture and rollout.
  `curve-tracing-lane-detection` is implicitly superseded by
  `viterbi-lane-tracing` (not formally decided, but nothing in this phase
  carries it forward) — still worth an explicit call if that branch is
  ever revisited, since it has its own real, if superseded, history.
- ~~Which direction(s) to pursue next — not yet decided.~~ **PARTIALLY
  RESOLVED 2026-07-17**: option 2 below chosen and shipped, with a
  refinement beyond what was originally floated — see the "Superseded
  2026-07-17" entry in Design Decisions and the "Band-selection redesign"
  entry in Implementation Status for the full mechanism (largest-band
  selection as the new default, MW-matching kept opt-in via `--band-
  selection mw-strict`, plus a new `mw-mismatch` confidence tier that
  wasn't part of the original 3-option framing — calibration still runs to
  *verify* the selection and flag a disagreement, rather than dropping the
  external check entirely). **Options 1 and 3 remain genuinely undecided**
  — original framing preserved below for that context.
  1. **Keep tuning the 4 existing geometry methods** (Viterbi's smoothing
     constant, Ridge's unresolved smoothing tension, Snake's rigidity
     params, finishing Phases C/D). Feasible, but the historical pattern
     (curve-tracing v1/v2/v3, then Viterbi/Ridge/Snake/band-graph/
     shared-row, all landing in the same mediocre place) argues this alone
     is unlikely to close the accuracy gap — it's polishing a part of the
     pipeline that isn't where the error has consistently traced back to.
  2. **Make biggest-band selection the default instead of a fallback.**
     Cheap to test (already done, see Implementation Status's empirical
     entry above — real improvement on 2/4 methods, wash on the other 2)
     and cheap to build if adopted (a change to the shared band-
     identification layer all 4 methods already reuse, e.g. an orthogonal
     `--band-selection` flag alongside `--method`, not a new geometry
     method). Real risk: MW-matching is checkable against an external
     physical fact even when wrong; biggest-band has no external check at
     all and would silently mis-identify a contaminant-dominated lane as
     "pure."
  3. **Interactive UI**: human marks the target band (click-drag), software
     extrapolates the identification to the rest of that dilution series'
     lanes. The only option that directly resolves the project's actual,
     repeatedly-confirmed bottleneck (which band/lane is the target) rather
     than refining a guess — but the largest lift by far: no UI exists
     today (pure CLI), hosting is inside `ebase` (frontend stack/ownership
     unknown), and the "extrapolate to other lanes" half is a real
     algorithm problem in its own right — plausibly a reframing of the
     already-built-but-lukewarm `shared_row_lanes` prototype (same premise:
     a dilution series is one sample, so the target band should share a
     row across lanes), anchored by a human-confirmed starting point
     instead of a purely statistical guess, though that would need real
     rework, not just wiring in as-is. Also reintroduces a manual step into
     a tool whose founding goal was replacing manual eyeballing — a real
     tension worth surfacing to end users (see `QUESTIONS_FOR_USERS.md`),
     not resolved by engineering alone.
  No decision made yet on which to pursue, or in what order. **Extended
  2026-07-21**: Jacob asked for a full first-principles reassessment of the
  whole problem, deliberately not assuming any prior work (including the
  framing of these 3 options) is correct — see
  `data/purity_solution_space_reassessment.md` (gitignored discussion doc,
  not yet a decision) for the resulting broader discussion, which reaches a
  similar place as option 3 above from a different angle (most real
  densitometry tools don't fully automate identification either) plus two
  new angles not in the original framing: whether MW/ladder is load-bearing
  at all, and using dilution-series redundancy to *identify* the target
  band, not just validate it after the fact.

## Data Inventory

- `data/daria_data/project.md` — original proposal text for sub-project 1.
- `data/daria_data/attachments/` — 4 example gel images (PNG/JPG) + 1 PDF of an
  email thread with additional per-protein context (molecular weights, ladder
  used, Benchling links). **Important: these PNGs are Benchling attachment-
  viewer screenshots** (full UI chrome included), not clean gel photos —
  `data/decodeon_gel_images/Protein Purity/` has the clean original for at
  least HpyCH4IV (`8.6.25 Protein Purity.tif`, confirmed by the filename
  visible in the screenshot).
- `data/gia_data/project.md` — original proposal text for sub-project 2.
- `data/gia_data/attachments/` — 22 raw 96-well gel scans (TIFF), 2 PDFs of the
  resulting heatmap plots, 2 `.txt` files with the target well-by-time
  activity-score matrices (SfiI enzyme).
- `data/gia_data/attachments/TfiI/` — added 2026-07-13. A much larger activity
  gel dataset for a different enzyme (TfiI), same 96-well-grid paradigm as
  SfiI. Adds real complexity beyond SfiI:
  - Four independent screen conditions (General, pH, ADD, CAPS Screen), each
    with its own Normalization baseline + multi-timepoint time course.
  - A `Validation/` subfolder that is **structurally different** — dose-
    response by formulation/lot (4 quadrants × 2-fold dilution series), not a
    96-well time-course grid. Whether this needs its own parsing logic or
    explicit scoping-out is not yet decided (see `QUESTIONS_FOR_USERS.md`).
  - Every timepoint has a plain + `_unlabeled` image pair — the unlabeled
    version is likely preferable for automated segmentation.
  - **Ladder confirmed 2026-07-14: N0550**, always, across activity/titer
    work. This is a **DNA fragment-size ladder (kb, not kDa)** — a different
    unit and gel type (agarose, not SDS-PAGE) from the purity workflow's
    protein MW ladders, so it does *not* belong in `core/ladder.py`'s
    `KNOWN_LADDERS`. Full band list, recorded for whenever activity/titer is
    built:
    ```
    10.0 kb / 40 ng    2.0 kb / 40 ng     0.9 kb / 34 ng    0.4 kb / 49 ng
    8.0 kb / 40 ng     1.5 kb / 57 ng     0.8 kb / 31 ng    0.3 kb / 37 ng
    6.0 kb / 48 ng     1.2 kb / 45 ng     0.7 kb / 27 ng    0.2 kb / 32 ng
    5.0 kb / 40 ng     1.0 kb / 122 ng*   0.6 kb / 23 ng    0.1 kb / 61 ng
    4.0 kb / 32 ng
    3.0 kb / 120 ng*                      0.5 kb / 124 ng*
    ```
    (`*` = mass-reference bands, per the labeled product image.)
  - Per-image `.inf` sidecar files (AlphaImager instrument-export XML) —
    potentially useful for cross-image normalization, though samples checked
    so far show identical default-looking values (see `QUESTIONS_FOR_USERS.md`).
  - Data-quality anomalies noted (not fixed): 2 files with an unexpected
    `.ory` extension, a stray `Thumbs.db`, an orphaned `Validation/a.tif`/
    `a.inf` pair, several `_unlabeled` filename typos, one oddly-sized TIFF.
- `data/decodeon_gel_images/Protein Purity/` — added 2026-07-13. 11 more
  example purity gels (TIFF/JPG), same ladder + dilution-series layout as
  `daria_data`, all the "no embedded standards" case. **This is the dataset
  all real-image pipeline testing/tuning has actually used** — `daria_data`'s
  images were never fed into the pipeline itself (screenshots aren't valid
  input), only its email text for known MWs. Two images
  (`260407_protein_purity.tif`, `4.16.26 Protein Purity.tif`) are notably
  low-contrast — **root cause confirmed 2026-07-14**: an old stain the
  user's team discontinued using ~2025-12, nothing to fix in the tool. One
  (`251017_..._FusionProtein.tif`) shows a doublet band with dilution-fold
  labels burned in.
  - **Per-file protein identity, confirmed by directly viewing each image**
    — only `8.6.25 Protein Purity.tif` (HpyCH4IV) has both a clean testable
    file *and* an independently confirmed MW (29,267 Da):
    - `2.4.25 PDEV981 Protein Purity.jpg` — Esp3I (PID940/PDEV981),
      **confirmed MW 61,708.19 Da**
    - `9.20.24 PDEV829 Conc Stock.jpg` — IdeS Protease (PDEV829),
      **confirmed MW 36,825.91 Da**
    - `7.17.24 PDEV772 Conc Stock.jpg` — TelA (NEB3606, PDEV772),
      **confirmed MW 39,358.70 Da** (ladder fails to calibrate — see Known
      Limitations)
    - `10.31.25 PDEV1437.tif` / `251017_..._FusionProtein.tif` — same
      construct, two lots/dates: R-218, a TET3 fusion (PDEV1437 / PID1384),
      **confirmed MW 58,218.74 Da**
    - `1.15.25 Concentrated Stock.jpg` — CL_ASR29 (PID926/PDEV946),
      **confirmed MW 44,599.87 Da**
    - `260612_ProteinPurity.tif` — CoZyCap Njord, **confirmed MW 202,491.88
      Da**, ladder **confirmed P7717**
    - `6.12.26 PDEV1718 Protein Purity.tif` — KasI, **confirmed MW 32,314.44
      Da**, ladder **inferred P7719** (11 bands, matching P7719's count,
      plus majority-usage in this batch — the user's reasoned judgment
      call, not independently verified against a labeled product image the
      way P7719/P7717 themselves were)
    - `260407_protein_purity.tif`, `4.16.26 Protein Purity.tif` — still **no
      legible label and no ladder identity**; remains open.
  - **Scope note**: `daria_data`'s other 3 confirmed MWs (FCE-T7 RNAP fusion,
    EcoRI-HF, BtgZI) have no corresponding clean, pipeline-testable image —
    only HpyCH4IV does. Full end-to-end purity-accuracy validation across
    this batch has exactly one confirmed real test case; every other
    successful calibration only confirms the calibration *machinery* works.
- `data/pptx_tet3_gels/` — added 2026-07-14, extracted from a PowerPoint the
  user provided (`~/Downloads/7.14.26 TET3 Protein Gels & LabChip Purity.pptx`,
  not itself committed anywhere). 6 real gel images, each with a **confirmed
  ground-truth purity % AND confirmed MW written directly on the slide** —
  the first time this project has had more than one confirmed-purity real
  test case. Ladder confirmed by the user as **P7719** for all 6. Per-file
  ground truth:
  ```
  R-217_PDEV1405_80pct_58.21874kDa.png       -- R-217 TET3, 80% pure, 58,218.74 Da
  R-218_PID1385_PDEV1411_69pct_58.21874kDa.png -- R-218, cleaved TET3, 69% pure, 58,218.74 Da
  R-236_PDEV1452_91pct_53.53519kDa.png       -- R-236, 91% pure, 53,535.19 Da
  R-236_PDEV1495_91.6pct_53.53519kDa.png     -- R-236 TET3, 91.6% pure, 53,535.19 Da
  R-244_PDEV1526_87.3pct_57.48889kDa.png     -- R-244 TET3, 87.3% pure, 57,488.89 Da
  R-236_PID1502_PDEV1580_98.5pct_53.53519kDa.png -- R-236 TET3, 98.5% pure, 53,535.19 Da
  ```
  **R-217 and R-218 confirmed by the user to be the same construct family**:
  R-218 is the cleaved product of R-217, sharing the same confirmed MW — not
  a labeling error. See Implementation Status for the real end-to-end
  comparison run against this dataset.
- `data/curve_tracing_prototype_comparisons/` — added 2026-07-14. 3 rendered
  PNGs (+ a `README.md`) comparing the curve-tracing prototype's traced lane
  boundaries against the existing straight-rectangle approach on real
  images, generated from the `curve-tracing-lane-detection` branch. See
  Implementation Status's curve-tracing entry for the verdict.
- `data/decodeon_gel_images/Titers/` — added 2026-07-13. 8 images that are a
  **structurally distinct third category**, not a clean fit for either
  existing workflow: inverted-contrast agarose gels showing a 2-fold enzyme
  dilution series plus `+`/`-` control lanes, used to read a potency/dilution
  endpoint rather than a purity % or a per-well active/partial/dead state.
  Whether this becomes a third workflow is an open scope question — see
  `QUESTIONS_FOR_USERS.md`. One file (`4.16.26 Concentrated Stock
  Titers.tif`) contains two stacked gel images in a single file.
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
- **No individual names in committed content, except Jacob's (2026-07-13).**
  Refer to domain experts, submitters, and reviewers by role rather than by
  name or email address, in every file that gets committed — docs, code
  comments, tests, commit messages. Jacob Miller's own name is fine. Apply
  this proactively going forward rather than writing a name and fixing it
  later.
- **Never commit anything secret or proprietary (2026-07-13, scope resolved
  2026-07-14).** Covers, at minimum: passwords, API keys/tokens, credentials,
  connection strings, login usernames/handles; and **sequence data, buffer
  composition, and fermentation/formulation conditions for any protein or
  construct.** **Resolved 2026-07-14: lot codes (`PDEV####`/`PID####`) and
  construct/protein/enzyme names (e.g. TET3, R-218, CL_ASR29, KasI) do NOT
  need to be obfuscated and are fine to commit** — confirmed MW numbers are
  also fine (physical properties, not formulation secrets). The real,
  standing red line is sequence/buffer/fermentation/formulation detail
  specifically — if any of that ever needs to be recorded, stop and ask
  before committing it rather than assuming it's fine. (A full-repo sweep
  before pushing to GitHub, plus a second targeted sweep of everything added
  since, both came back clean — see git history for the audits.)
- **Current phase: purity workflow implemented and tested; activity workflow
  not started.** Don't start building the activity workflow until that's
  explicitly requested.
- **Document thoroughly enough to explain the whole system to non-implementing
  stakeholders later.** Every Design Decision entry should carry its
  rationale (the "why"), not just the decision itself.
- **Robust testing is required, not optional polish.** Every pipeline stage
  needs unit tests, plus integration tests against real example gel images,
  plus the dilution-series self-consistency check as an actual automated
  test.
- **Keep this document itself lean.** Once a topic's *current* state is fully
  captured by a later entry, trim earlier narrative detail (exact
  intermediate numbers, reverted-code specifics) rather than layering a new
  entry on top indefinitely — git history preserves the full detail if ever
  needed again. Never trim a decision's rationale or a "don't retry X"
  warning, only the superseded blow-by-blow around it.

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
don't let them drift out of sync. **Also use this as a prompt to trim cruft**
(see Working Agreements' "keep this document lean" entry) — stale interim
numbers and superseded narrative detail, not decisions or warnings.

## Repo Infrastructure Notes

- `.gitignore` was cleaned up from the GitHub-generated Python default down
  to what's actually relevant: Python build/cache artifacts, virtual envs,
  `.idea/` (JetBrains), standard macOS junk files, the `data/` directory,
  `scripts/` (local dev tooling), and `.claude/` (Claude Code's own local
  settings/worktree state — added 2026-07-14 after it briefly showed up as
  untracked).
- Language/framework/dependency tooling: Python 3.11+, numpy/scipy/scikit-
  image, argparse, pyproject.toml + uv, pytest — see Design Decisions'
  "Tech stack" entry and Implementation Status for the actual layout.
- **`scripts/` is gitignored** — local dev/debug tooling only, not shipped
  with the package. `scripts/generate_debug_images.py` runs every real
  example image (with its confirmed target MW from Data Inventory where
  known, `--allow-heuristic` otherwise) through `neband purity analyze
  --debug`, writing annotated images to `data/debug_images/` — a quick way
  to eyeball lane/band detection across the whole example set after a
  pipeline change, not a substitute for the automated test suite.
- **Experimental work happens on separate branches, not `main`.** The
  curve-tracing prototype (2026-07-14) lives entirely on branch
  `curve-tracing-lane-detection`, built via an isolated git worktree so it
  could proceed in parallel with `main`-branch work without any risk of
  collision. Not merged — see Implementation Status for why.
