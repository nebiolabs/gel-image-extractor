# Questions for End Users

A running list of functionality questions that need an answer from the actual
domain experts (the project submitters, reviewers, etc. — see AGENTS.md's
Project Overview for their roles) rather than something Jacob/Claude can
resolve through design discussion alone.
The intent is to accrue these and ask them in a batch, rather than trickling
requests one at a time.

**How to use this file:** add a question here as soon as it surfaces, with the
date and the context that prompted it. Once answered, don't delete the item —
check it off and record the answer/resolution inline, so there's a durable
record of what was asked and decided. This is distinct from AGENTS.md's "Open
Questions" section, which tracks internal architecture/design questions Jacob
and Claude need to resolve themselves — this file is for questions that need
outside domain knowledge or a business/scope call.

## Scope

- [ ] **Is the `Titers/` dataset in scope as a third workflow, or deferred/out
  of scope for now (like `activity` currently is)?** It's structurally
  distinct from both existing workflows — inverted-contrast agarose gels
  showing a 2-fold enzyme dilution series with `+`/`-` control lanes, used to
  read off a potency/dilution endpoint rather than a purity % or a per-well
  active/partial/dead state. *(Raised 2026-07-13, after reviewing
  `decodeon_gel_images/Titers/`.)*
- [ ] **Is the `Validation/` subfolder within the TfiI activity data (a
  formulation/lot dose-response comparison layout, not a 96-well time-course
  grid) a separate future requirement, or supplementary QC data outside
  current scope?** *(Raised 2026-07-13.)*

## Purity workflow

- [x] **P7719's exact per-band kDa values** — **RESOLVED 2026-07-13.** User
  provided NEB's own labeled product gel image for P7719, giving the
  complete band list: 250, 180, 130, 95, 72, 55, 43, 34, 26, 17, 10 kDa
  (matches everything independently found via web research: 11 bands total,
  orange reference at 72 kDa, green at 26 kDa, range 10-250 kDa — text
  sources alone couldn't confirm the individual list). Now seeded in
  `core.ladder.KNOWN_LADDERS["P7719"]`; `--ladder P7719` works as a real
  option.
- [x] **Which ladder(s) do these assays actually use — is it always the same
  one, or does it vary by team/assay/scientist?** Broadened 2026-07-13 from
  the narrower P7719-band-values question above (now resolved). **ANSWERED
  2026-07-14:**
  - *Purity:* varies by team/scientist — at least 2 different ladders are in
    use within the user's own group. They're willing to standardize within
    their team, but which ladder to standardize on is a separate decision
    still pending internally (not necessarily P7719). Practical implication:
    `--ladder`/`--ladder-bands` needs to keep supporting more than one real
    ladder, not just default to P7719 as originally floated. A second real
    ladder, **P7717**, was confirmed and seeded the same day (see below) —
    13 bands: 200, 150, 100, 85, 70, 60, 50, 40, 30, 25, 20, 15, 10 kDa.
  - *Activity/titer:* always **N0550** (a DNA fragment-size ladder, kb not
    kDa — distinct from the protein MW ladders above; not yet wired into
    any code since the activity/titer workflow isn't built). Not confirmed
    whether other teams also use N0550. Full 19-band list (kb / mass ng):
    10.0/40, 8.0/40, 6.0/48, 5.0/40, 4.0/32, 3.0/120, 2.0/40, 1.5/57,
    1.2/45, 1.0/122, 0.9/34, 0.8/31, 0.7/27, 0.6/23, 0.5/124, 0.4/49,
    0.3/37, 0.2/32, 0.1/61 (bold-in-source bands are the mass reference
    bands: 3.0/1.0/0.5 kb).
- [x] **How should we handle low-contrast/washed-out source gel scans**
  (e.g. `260407_protein_purity.tif`, `4.16.26 Protein Purity.tif` in
  `decodeon_gel_images/Protein Purity/`)? **ANSWERED 2026-07-14:** caused by
  an old Coomassie-type stain the user's team discontinued using ~2025-12 —
  scans from that point forward should look better. Not a scan-technique or
  tool-design issue to solve for; just expect the older images in this
  dataset to skew low-contrast.
- [x] **Are the embedded purity-standard-ladder gels still commonly produced**
  (the EcoRI-HF/BtgZI QC-report format with 50/75/88/94/97/98/99% reference
  lanes), or is the plain ladder + dilution-series format — seen in all 11
  of the newly added example images, with zero examples of the standards
  format — the more realistic default going forward? **ANSWERED 2026-07-14:**
  not typically produced — ladder + dilution series is the real-world norm.
  The user's team could add embedded reference lanes to future gels if the
  tool ever needed them, but there's no current design reason to require it.
- [x] **How should the tool handle the dilution-detectability limit?**
  Confirmed 2026-07-13 (user validated this as a real, expected concern, not
  just a detection-parameter artifact): at some dilution level, faint
  contaminant bands become undetectable before the target band does, which
  systematically inflates apparent purity in the more-dilute lanes of a
  series (observed 29% → 48% across one real dilution series). **DECIDED
  2026-07-14:** flag low-total-signal lanes as lower-confidence rather than
  silently reporting them at face value (option (a) from the original list;
  (b)/(c)/(d) not pursued). **Implemented same day**: `LaneResult.low_signal`
  is `True` when a lane's total detected band area is under 20% (placeholder,
  unvalidated threshold — `DEFAULT_LOW_SIGNAL_FRACTION`) of the most-
  concentrated lane in the same image; surfaced as a "Flag" column in the
  table, a `low_signal` field in CSV/JSON, and a "low-sig" suffix in
  `--debug` labels. This doesn't fix the underlying limit-of-detection
  effect (there isn't a fix), it just stops an inflated reading from being
  presented with the same confidence as a well-loaded lane. See `AGENTS.md`
  Implementation Status for detail.
- [x] **Need confirmed molecular weights for proteins identified (by visible
  in-image label) in `decodeon_gel_images/Protein Purity/`** — **RESOLVED
  2026-07-13.** User provided confirmed MWs:
  ```
  Esp3I - 61708.19 Da (61.708 kDa)
  IdeS Protease - 36825.91 Da (36.826 kDa)
  TelA - 39358.70 Da (39.359 kDa)
  TET3 (R-218) - 58218.74 Da (58.219 kDa)
  CL-ASR29 - 44599.87 Da (44.600 kDa)
  ```
  Now seeded in `AGENTS.md` Data Inventory. Ran all 5 through `gelx purity
  analyze` against their matching real images (see Implementation Status) —
  results are mixed, not a clean validation win: TelA's ladder lane fails to
  calibrate at all (genuinely low-contrast/low-band-count in that scan), the
  `251017_..._FusionProtein.tif` TET3 lot returns "not-found" for every
  lane despite the ladder reporting a good R², and Esp3I/IdeS/CL-ASR29 show
  purity swinging inconsistently across what should be a self-consistent
  dilution series. Only `10.31.25 PDEV1437.tif` (the other TET3 lot) lands
  on a consistent, correct-looking matched MW. This looks like it's
  surfacing the still-unvalidated lane-capture/over-segmentation concern
  (see Known Limitations) rather than a target-MW problem — under
  investigation.

  Separately, 4 images had **no legible protein label at all** — identity
  unknown, not just MW: `6.12.26 PDEV1718 Protein Purity.tif`,
  `260612_ProteinPurity.tif`, `260407_protein_purity.tif`,
  `4.16.26 Protein Purity.tif`. *(Raised 2026-07-13.)* **2 of the 4 resolved
  2026-07-14:**
  - `260612_ProteinPurity.tif` — **CoZyCap Njord, 202,491.88 Da (202.492
    kDa)**, ladder **P7717** (independently confirmed by the user, band
    list now seeded — see the ladder question above).
  - `6.12.26 PDEV1718 Protein Purity.tif` — **KasI, 32,314.44 Da (32.314
    kDa)**, ladder inferred as **P7719** by the user (11 bands visible in
    the image, matching P7719's known band count, plus "most other gels in
    this batch use P7719") — a reasoned judgment call, not independently
    confirmed the way P7719/P7717's own band lists were verified against
    NEB product images. Flagging that distinction so it isn't mistaken for
    the same certainty later.

  `260407_protein_purity.tif` and `4.16.26 Protein Purity.tif` still have
  **no legible label and no ladder identity** — remains open.
- [ ] **Why does the R-236 lots' dominant gel band calibrate ~40-50 kDa
  higher than their confirmed MW?** Raised 2026-07-14, from running
  `data/pptx_tet3_gels/` against the tool (see `AGENTS.md` Implementation
  Status): on `PDEV1452` and `PDEV1495`, the clearly-real, visually dominant
  protein band consistently lands at ~95-100 kDa against the ladder, but the
  confirmed MW for both is 53,535.19 Da — too large a gap to be measurement
  noise. Possible explanations, not distinguished between: (a) our ladder
  calibration is picking a wrong fit window on these specific images, (b)
  this construct genuinely runs anomalously on SDS-PAGE relative to its true
  MW (not unusual for some fusion proteins), or (c) the confirmed MW refers
  to a cleaved form while the dominant gel band is uncleaved (by analogy to
  the confirmed R-217/R-218 cleaved-product relationship above — is
  something similar happening here?). Deliberately not guessed at further;
  deferred until after the lane over-segmentation fix in case that's a
  contributing factor, but flagging now in case a domain expert already
  knows the answer.

## Activity workflow

- [ ] **Does the baseline-relative approach (decided 2026-07-13: compare each
  well's band pattern against that same well's own Normalization-image
  baseline, rather than requiring absolute substrate/fragment-size knowledge)
  actually match how a domain expert judges active/partial/dead?** Or are
  there real cases — e.g. star activity / off-target cutting — where that
  comparison alone would miss something that only absolute expected-fragment
  knowledge (the reviewer's original NEBcutter tie-in idea) would catch?
  *(Raised 2026-07-13.)*
- [ ] **What precisely distinguishes "partial" from "active" or "dead"** in
  terms of band pattern/intensity — is there an existing rule of thumb to
  encode, or is it currently more of a judgment call made by eye? *(Raised
  2026-07-13.)*
- [ ] **Do the TfiI `.inf` metadata fields (exposure, gain, etc.) actually vary
  meaningfully across real production imaging**, or are they typically left
  at instrument defaults? Relevant to whether we can lean on them for
  cross-image normalization. *(Raised 2026-07-13 — the samples checked so far
  all had identical default-looking values.)*

---

*Last updated: 2026-07-14 (ladder-standardization, low-contrast-scan-cause,
embedded-standards, and dilution-detectability-handling questions all
answered/decided; P7717 and N0550 ladder band lists obtained; 2 of the 4
unlabeled images identified; R-217/R-218 relationship confirmed; new
question added on the R-236 lots' MW-migration discrepancy. See `AGENTS.md`
"Known Limitations" for open engineering items that don't need end-user
input). See `AGENTS.md` for full design context behind these questions.*
