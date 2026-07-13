# gel-image-extractor

A tool for extracting quantitative and categorical information from gel
electrophoresis images, replacing manual/eyeballed operator judgment with a
standardized, reproducible pipeline.

## Status

**Purity workflow: implemented and tested** (27 tests passing). **Activity
workflow: not started.** See `AGENTS.md` for full project scope, data
inventory, working agreements, design decisions, and implementation notes
(including real findings from running this against real gel images), and
`diagrams/program-flow.png` (or the `.mmd` source) for the current
architecture sketch.

```
uv sync
uv run gelx purity analyze "data/decodeon_gel_images/Protein Purity/8.6.25 Protein Purity.tif" \
  --target-mw 29.267 --allow-heuristic
uv run pytest
```

No verified ladder band sizes exist yet (see `QUESTIONS_FOR_USERS.md`), so
real runs currently need `--allow-heuristic` or an explicit `--ladder-bands`
list rather than `--ladder <name>`.

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
  currently has 27 passing tests covering all of this.
- Running this against real images surfaced some non-obvious findings (a
  data file that's actually a screenshot with UI chrome, real gel photos not
  being on a white background, a known bias in the heuristic fallback) —
  see `AGENTS.md`'s "Implementation Status" section for the full detail.
- No git actions (commit/push) happen without explicit user consent — see
  `AGENTS.md`'s "Working Agreements".
- Open questions that need a domain expert's input (not just an engineering
  call) are tracked in `QUESTIONS_FOR_USERS.md`, to be asked in a batch rather
  than piecemeal.
