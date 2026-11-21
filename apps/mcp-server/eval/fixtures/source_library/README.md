# Eval source-library fixture

These documents back the `conical_amsr2_claims` eval case, whose
configuration cites its sources as `source_claims`
(`conical-configuration-v2`) instead of inline evidence. The eval runner
points `src.source_library.SOURCES_DIR` at this directory for the run, so
claim resolution reads these records and never the production
`data/sources` library, which stays empty.

Each document groups the inline evidence of the reviewed registry
configuration `amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json`
that shares one source identity (authority, title, URL, document
revision, retrieval date); each excerpt keeps that evidence item's
locator and text. Resolving the case's claims therefore yields the same
evidence records the inline form carries. Regenerate the documents from
that configuration if its evidence changes; `eval/cases.py` fails loudly
when a claim no longer matches a fixture excerpt.

The documents are eval fixtures with reused registry metadata, not new
source reviews.
