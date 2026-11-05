# Synthetic gold fixture -- NOT REAL DATA

`gold_fixture.json` in this folder is hand-written by this build, invented
for plumbing tests only. It does not come from any real company policy,
the BUAS repo, or Aigentic's own Gifts pack. Its only purpose is to let
`polaris.evaluator.run_evaluation` run end-to-end today, before Mada's real
gold file arrives, so we can prove the harness (loader -> classifier ->
metrics -> report) actually works.

Do not use any number produced against this file in a conversation with
Claudia, in a decision log, or anywhere that implies it measures real
classification quality. Replace it the moment the real file arrives.
