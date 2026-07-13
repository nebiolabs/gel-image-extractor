# Questions for End Users

A running list of functionality questions that need an answer from the actual
domain experts (the submitter, the submitter, a team member, a reviewer, etc.)
rather than something Jacob/Claude can resolve through design discussion alone.
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
- [ ] **Which ladder(s) do these assays actually use — is it always the same
  one, or does it vary by team/assay/scientist?** Broadened 2026-07-13 from
  the narrower P7719-band-values question above (now resolved). Still open:
  - *Purity:* is P7719 the de facto standard for most protein purity gels at
    NEB? If so, we could default `--ladder` to it and only require the flag
    when a different ladder was actually used, reducing the common-case CLI
    input down to just `--target-mw`. Several purity examples we have use
    P7719, but we don't know if that's representative or coincidental.
  - *Activity/titer:* the SfiI dataset documents "1kb+" as the DNA ladder
    used; TfiI and the Titers dataset don't have a recorded ladder identity
    that we've confirmed — is it the same ladder across all restriction/
    activity work, or does it vary by assay?
  *(Raised 2026-07-13, broadened same day while starting purity workflow
  implementation.)*
- [ ] **How should we handle low-contrast/washed-out source gel scans**
  (e.g. `260407_protein_purity.tif`, `4.16.26 Protein Purity.tif` in
  `decodeon_gel_images/Protein Purity/`)? Is that scan quality typical/
  acceptable in practice, or should we expect (and design for) better source
  image quality? *(Raised 2026-07-13.)*
- [ ] **Are the embedded purity-standard-ladder gels still commonly produced**
  (the EcoRI-HF/BtgZI QC-report format with 50/75/88/94/97/98/99% reference
  lanes), or is the plain ladder + dilution-series format — seen in all 11
  of the newly added example images, with zero examples of the standards
  format — the more realistic default going forward? *(Raised 2026-07-13.)*

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

*Last updated: 2026-07-13 (P7719 band-size question resolved this update; see
`AGENTS.md` "Known Limitations" for newer open engineering items that don't
need end-user input). See `AGENTS.md` for full design context behind these
questions.*
