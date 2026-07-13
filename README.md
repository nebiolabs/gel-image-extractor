# gel-image-extractor

A tool for extracting quantitative and categorical information from gel
electrophoresis images, replacing manual/eyeballed operator judgment with a
standardized, reproducible pipeline.

## Status

Design phase — architecture and MVP scope have been decided, but no
implementation exists yet. See `AGENTS.md` for full project scope, data
inventory, working agreements, and design decisions, and
`diagrams/program-flow.png` (or the `.mmd` source) for the current
architecture sketch.

## What this project does (planned)

This merges two related internal NEB use cases that share the same underlying
problem — image in, calibrated quantitative/categorical result out:

- **Protein purity quantification** — convert an SDS-PAGE gel lane image into
  a quantitative purity %, without the manual baseline-selection fiddliness
  of tools like ImageJ.
- **Activity gel extraction** — classify each well of a 96-well restriction
  digest stability assay gel into active / partial / dead per time point, to
  drive a well-position × time heatmap instead of manual scoring.

Both share a common image-processing core (lane/grid segmentation, ladder
detection & calibration, band/peak detection); each has its own workflow and
output on top of that core. Purity is being built first. See `AGENTS.md`'s
"Design Decisions" section for the full reasoning.

## Development

- No language/dependency tooling has been chosen yet beyond Python.
- Interface: CLI first, structured so a UI can be layered on later.
- No git actions (commit/push) happen without explicit user consent — see
  `AGENTS.md`'s "Working Agreements".
