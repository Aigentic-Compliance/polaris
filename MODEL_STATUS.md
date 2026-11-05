# Model status -- current / experimental / planned / conditional

Written in response to the ambiguity Henkjan flagged in
`ProtoFocus_AI_Model_Architecture.docx`: several components in that
document are described in a way that doesn't distinguish "this exists and
runs" from "this is the intended architecture once experiments justify
it." This file gives that classification explicitly, scoped to what this
repo touches (WP1/WP2-folded/WP6/WP7). It does not speak for Policy Cards
or Policreator, which this repo doesn't build.

| Architecture doc component | Status here | What actually exists in this repo |
|---|---|---|
| Azure OpenAI GPT-4o -- "Labeller / Meta-Data Extractor" | **CURRENT** | `polaris/classifier/llm_classifier.py::AzureLETClassifier`. Real, runs today, unmodified third-party model via prompt engineering -- exactly as the doc describes it. **Gap**: the doc's description includes extracting thresholds/roles/timing conditions as structured fields; the current call only returns LET label + confidence + rationale, not those parameters. See "open question" below. |
| Document-intelligence / OCR service | **PLANNED, not started** | `pdf_segmenter.py` has no OCR path (known gap, see README section 6 / framework C6.7). Confirmed by this doc as a third-party API call, not a model to build -- consistent with the framework's provider-approval requirement (C12.3, W6, still open). |
| **Policron Semantic Equivalence Model** (fine-tuned cross-encoder/NLI) | **CONDITIONAL / not built** | Does not exist anywhere in this repo or, as far as I can tell, anywhere real. `polaris/recommender/comparator.py::_compare_pair`'s plain dict-equality fallback (-> DIFFERENT on any non-numeric mismatch) is the current stand-in. Whether this model is actually needed is exactly the kind of question the WP1 framework's R&D philosophy (§7, U1-U4) says should be answered by evidence, not assumed -- the comparator's crude fallback is doing that evidencing job right now: every DIFFERENT verdict on a non-numeric parameter is a data point on whether plain-equality comparison is good enough or a smarter equivalence check is actually justified. |
| **Confidence Calibration Model** (trained on expert-review labels) | **CONDITIONAL / not built** | Does not exist. `polaris/evaluator/metrics.py::_confidence_calibration` exists specifically to produce the evidence that would justify or rule out building this: if cascade.py's raw LLM confidence is already well-calibrated against gold data (0.9-confidence predictions right ~90% of the time), no calibration model is needed. That evaluation hasn't been run against real gold data yet -- see README section 7. |
| **Structured Extraction Model (v2)** (fine-tuned XLM-RoBERTa) | **CONDITIONAL / not built** | Does not exist. This is the "trained transformer" alternative explicitly discussed and deferred when the cascade design was agreed -- see `llm_classifier.py` module docstring: "a deliberate phase-2 optimization once the LLM cascade has generated enough of its own labelled volume to train on," not a component that runs today. |
| Multilingual sentence-embedding model (retrieval layer) | **Out of scope for this repo** | Belongs to Policreator/FAQ generation, not WP1/WP6/WP7. Not referenced anywhere here. |

## Open question this raises for the repo (not resolved here)

The architecture doc's "Labeller / Meta-Data Extractor" description bundles
LET classification with structured parameter extraction (thresholds,
roles, gift categories, timing conditions) as if it's one step. In this
repo, those are two different pipeline stages by design: `Segment.label`
(WP2, `polaris/classifier/`) versus `PolicyObject.parameters` (WP3, not
built in this repo -- see README section 1). If the real intended
architecture is one GPT-4o call doing both, `cascade.py`'s classifier
should be extended to return parameters alongside the label, which changes
the WP0 contract boundary between WP2 and WP3. That's an architecture
decision, not a documentation fix -- flagging it for Mada rather than
silently folding WP3 into WP2's contract.
