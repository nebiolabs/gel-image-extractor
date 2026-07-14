# gel-image-extractor

A tool for extracting quantitative and categorical information from gel
electrophoresis images, replacing manual/eyeballed operator judgment with a
standardized, reproducible pipeline.

## Status

**Purity workflow: implemented and tested** (49 tests, all passing).
**Activity workflow: not started.** See `AGENTS.md` for full project scope, data
inventory, working agreements, design decisions, implementation notes
(including real findings from running this against real gel images), and a
"Known Limitations" section tracking open issues that shouldn't be silently
fixed or forgotten. `diagrams/program-flow.png` (or the `.mmd` source) has
the current architecture sketch.

### MVP scope — read this before trusting a number

- **This release covers the purity workflow only.** Activity/titer gel
  classification is a separate, not-yet-built workflow — out of scope here.
- **Treat every purity % as a first-pass estimate a human should verify, not
  an authoritative result.** Lane detection assumes each lane is a straight
  vertical rectangle; real gels can curve ("smiling") or have adjacent lanes
  bleed together near the loading wells, and the current pipeline doesn't
  correct for either. Real accuracy checks against confirmed ground truth
  found some images matching within a few points and others off by a large
  margin (see `AGENTS.md` Implementation Status) — the gap is driven by
  lane-detection error, not the purity math itself.
- **Always run with `--debug [PATH]` and look at the annotated image before
  reporting a number.** It draws every detected lane and band box directly
  on the gel photo — if a lane box looks wrong (split, merged, or offset
  from the real band), don't trust that lane's purity % without a manual
  recheck. This takes under a minute per image and is the single best
  safeguard this tool currently has.
- Results are also flagged automatically where the tool itself has lower
  confidence: `not-found` (no usable signal), `heuristic` (no MW match, best
  guess only), and `low_signal` (likely high-dilution, purity may be
  inflated) — treat all three as needing extra scrutiny, not just ignoring
  them.

```
uv sync
uv run gelx purity analyze "data/decodeon_gel_images/Protein Purity/8.6.25 Protein Purity.tif" \
  --target-mw 29.267 --ladder P7719
uv run pytest
```

`--ladder P7719` and `--ladder P7717` are now real, verified options (see
`AGENTS.md`) — ladder choice genuinely varies by team/scientist, so there's
no single default. Ladder calibration is deliberately lenient — it works
from however many bands are confidently detected, empirically picking
whichever plausible size alignment fits best, rather than requiring every
known band to resolve or assuming a fixed direction for missing ones. Real
MW-matching now produces consistent results across most lanes of the one
real gel tested end to end, but there's a confirmed, structural limitation:
at high dilution, faint contaminant bands become undetectable before the
target band does, which inflates apparent purity. This can't be fixed
outright (the information isn't in the image below some dilution level),
but affected lanes are flagged `low_signal` in the output (a "Flag" column,
or a `low_signal` field in `--csv`/`--json`) rather than reported at face
value — see AGENTS.md's Known Limitations. Treat results as directional for
now, not exact.

## What this project does (planned)

This merges two related internal NEB use cases that share the same underlying
problem — image in, calibrated quantitative/categorical result out:

- **Protein purity quantification** — convert an SDS-PAGE gel lane image into
  a quantitative purity %, without the manual baseline-selection fiddliness
  of tools like ImageJ.
- **Activity gel extraction** — classify each well of a 96-well restriction
  digest stability assay gel into active / partial / dead per time point, to
  drive a well-position × time heatmap instead of manual scoring.

A possible **third category** — enzyme titer/potency assays (dilution-series
agarose gels reading a potency endpoint) — was identified in newer example
data but isn't scoped in yet; see `QUESTIONS_FOR_USERS.md`.

Both existing workflows share a common image-processing core (lane/grid
segmentation, ladder detection & calibration, band/peak detection); each has
its own workflow and output on top of that core. Purity was built first, and
is implemented; activity hasn't been started. See `AGENTS.md`'s "Design
Decisions" section for the full reasoning.

For purity specifically, target-band identification is MW-based by default
(ladder calibrated via a known lookup table or a `--ladder-bands` override);
if the ladder can't be calibrated, the tool refuses to guess unless
`--allow-heuristic` is explicitly passed. For activity, the plan is to
classify each well against its own baseline (Normalization) image rather than
requiring substrate/digest-condition data up front — both choices keep the
tool fully self-contained and CLI-driven, with no external lookups required.
See `AGENTS.md` for the full rationale.

## Development

- **Stack:** Python 3.11+; `numpy` + `scipy` + `scikit-image` for image
  processing; `argparse` for the CLI; `pyproject.toml` + `uv` for packaging/
  dependencies; `pytest` for testing.
- **Interface:** one CLI entry point with subcommands (`gelx purity analyze
  gel.tif ...`), not separate binaries per workflow. Structured so a UI can
  be layered on later. Every flag (`--lane`,
  `--ladder-bands`, `--allow-heuristic`, etc.) must be clearly documented in
  the CLI's own `--help` output, not just in external docs — end users of
  this tool aren't expected to be CLI-comfortable.
- **Output:** human-readable table to stdout by default; `--csv [PATH]` and
  `--json [PATH]` are additive, optional, and can be combined. See
  `AGENTS.md`'s "Design Decisions" for the exact column schema.
- **Architecture is deliberately modular/swappable**: a pipeline of discrete
  stages, pluggable algorithms behind common interfaces (baseline correction,
  band detection, ladder-lane identification), and a structured result object
  decoupled from output formatting — several early decisions (baseline
  method, MW tolerance, the leftmost-lane heuristic) are expected to change
  once real output is in hand, so swapping any one of them should be a
  localized change, not a rewrite.
- **Testing is required, not optional polish** — every pipeline stage needs
  unit tests, plus integration tests against the real example gel images in
  `data/`, plus the dilution-series self-consistency check (same sample,
  same purity % across dilutions — our main correctness signal, since no
  external ground truth exists) encoded as an actual automated test. Purity
  currently has 49 tests, all passing.
- **Reporting precision:** `purity_percent` rounds to the nearest whole
  percent (not 1 decimal) — deliberately, given the pipeline's known
  real-world imprecision.
- **`--debug [PATH]` writes an annotated debug image** showing detected lane
  boxes (blue = ladder, amber = sample), band boxes (green = target/matched,
  red = other/contaminant), and per-lane purity/MW labels — built for both
  debugging the pipeline and helping an end user see how a result was
  reached, not a separate internal-only tool. See `AGENTS.md`'s
  Implementation Status for a real example of what it surfaced.
- **Lane vertical bounds are now adaptive, not a fixed top-margin crop** —
  the loading-well "comb" fringe (which varies lane to lane) and a bottom
  cassette/tape-edge artifact (consistent across every lane) are both
  detected per-image rather than assumed. This also fixed a real
  cross-lane coordinate-frame bug in MW calibration found while validating
  it — see `AGENTS.md`'s Implementation Status for the full story.
- Running this against real images surfaced some non-obvious findings (a
  data file that's actually a screenshot with UI chrome, real gel photos not
  being on a white background, a band-detection noise-robustness gap on
  faint lanes — now fixed, a ladder-calibration approach that had to stop
  assuming a fixed direction for missing bands — now fixed, a ladder-lane
  noise threshold that was too strict for several genuinely faint-but-real
  scans — now fixed, bringing successful calibration from 6/11 to 10/11 real
  example images, and a confirmed limitation where high dilution inflates
  apparent purity by making contaminant bands undetectable before the target
  band — partially mitigated via a `low_signal` flag, not fixable outright)
  — see `AGENTS.md`'s "Implementation Status" and "Known Limitations"
  sections for the full detail.
- **Lane detection fragmentation (a real lane fading toward background
  splitting into several fake "lanes" as its signal dips below the
  detection threshold) has a validated partial fix** — see `AGENTS.md`'s
  Implementation Status for the full story, including a first design that
  caused a real regression (caught before landing) and how it was fixed.
  Horizontal gel smiling/curvature and bleed-over between wide/diffuse
  bands remain unaddressed — see Known Limitations.
- **7 real images now have a confirmed ground-truth purity % *and* MW**
  (HpyCH4IV, plus 6 more from `data/pptx_tet3_gels/`) — up from just one
  (HpyCH4IV, MW only, no confirmed purity) earlier in the project. Every
  other successful calibration on other images still only confirms the
  calibration *machinery* works, not that the reported purity % is correct.
  Getting more confirmed MWs is tracked in `QUESTIONS_FOR_USERS.md`.
- **Purity calculation is confirmed to stay a direct single-lane
  densitometric ratio** (target band area / total lane area, calibrated
  against one ladder), not a comparison/bracketing against reference lanes —
  even though that's closer to current manual practice per end-user input.
  Deliberate choice, see `AGENTS.md`.
- No git actions (commit/push) happen without explicit user consent — see
  `AGENTS.md`'s "Working Agreements".
- Open questions that need a domain expert's input (not just an engineering
  call) are tracked in `QUESTIONS_FOR_USERS.md`, to be asked in a batch rather
  than piecemeal.
