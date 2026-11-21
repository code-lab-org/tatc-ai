# Eval source-library fixture

These documents back the `conical_amsr2_claims` eval case, whose
configuration cites its source as `source_claims`
(`conical-configuration-v2`) instead of inline evidence. The eval runner
points `src.source_library.SOURCES_DIR` at this directory for the run, so
claim resolution reads these records and never the production
`data/sources` library, which stays empty.

`eval-amsr2-claims-source.json` is a deliberately small synthetic
document: one excerpt whose geometry values match the shared registry
configuration `amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json`,
so the claim-form case drives the same preparation flow as the complete
case at a prompt size that fits the scoped-eval request budget. It is an
evaluation fixture with reused registry geometry values, not a new
source review and not a registry entry.

`eval/cases.py` builds the case's claim from this document and fails
loudly if the fixture no longer matches. Keep the document and the case
in step.
