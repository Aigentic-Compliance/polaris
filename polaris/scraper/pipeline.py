"""End-to-end WP1 orchestration.

Two entry points, sharing one candidate-processing core:

  run_for_organisation  -- seeded-site discovery on one known organisation's
                            domain (C2.1). Works today, no external
                            dependency beyond the target site itself.
  run_topic_search       -- web-wide search discovery across organisations
                            not already known (C2.2). Requires a real
                            SearchProvider (see search_provider.py) --
                            this is the piece that makes "find policies of
                            this type across the internet" possible, as
                            opposed to "check this one company's site."

discover -> download -> hash/dedupe -> validate -> register, for both.

This is intentionally a thin orchestrator with no business logic of its
own -- every decision (is this URL allowed, is this a duplicate, is this
document a policy) lives in its own module and is independently testable.
That separation is the direct fix for the BUAS audit's "2,688-line god
class" maintainability finding.

The per-candidate processing steps used to be duplicated inline inside
run_for_organisation only; _process_candidate() below is that logic
extracted into one shared function so run_topic_search doesn't reimplement
it and risk drifting out of sync -- exactly the kind of duplication that
produced BUG-06 and BUG-09 elsewhere in this repo (see AUDIT.md).

Failure modes: _download raises DownloadError (never returns None) on any
network failure or size-cap breach. Both entry points catch it
per-candidate -- logged at WARNING, counted in *.download_failed -- so one
dead URL never aborts the whole run and never silently vanishes from the
run's statistics either. KNOWN GAP, stated rather than hidden: a failed
download cannot currently be written to the SourceRegistry, because
SourceDocument.file_hash is mandatory and there is no hash without
downloaded bytes. This is the concrete reason the registry needs the
append-only state-machine rebuild flagged as a pending architecture
decision -- until that lands, a download failure is visible in the run's
log and result object, but not yet in the durable registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from polaris.classifier.cascade import BatchClassificationResult
from polaris.common.exceptions import DocumentParsingError, DownloadError, RawStoreError, SearchProviderError
from polaris.common.logging_config import get_logger
from polaris.common.models import SourceDocument
from polaris.scraper.discovery import Candidate, discover_for_organisation, discover_via_search
from polaris.scraper.document_validator import validate_document
from polaris.scraper.hashing import sha256_of_bytes
from polaris.scraper.http_session import build_session
from polaris.scraper.raw_store import DEFAULT_RAW_STORE_DIR, save_raw
from polaris.scraper.robots import RobotsGate
from polaris.scraper.source_registry import SourceRegistry
from polaris.scraper.url_safety import UnsafeURLError, safe_get

logger = get_logger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB safety cap

# UNCALIBRATED heuristic threshold, same honesty standard as REVIEW_THRESHOLD
# and LLM_QUALIFIER_CONFIDENCE_THRESHOLD elsewhere in this repo: a page whose
# extracted body text (after chrome-stripping) is shorter than this is
# suspicious enough to be worth a real browser render, not a measured
# cutoff. See page_fetcher.py's PlaywrightPageFetcher and the fallback
# logic in _process_candidate.
MIN_BODY_CHARS_BEFORE_RENDER_FALLBACK = 200

UNCONFIRMED_ORG_PREFIX = "unconfirmed:"  # see run_topic_search docstring


@dataclass
class RunResult:
    organisation: str
    candidates_found: int
    downloaded: int
    download_failed: int
    duplicates_skipped: int
    accepted: int
    rejected: int
    needs_review: int
    classified_documents: dict[str, BatchClassificationResult] = field(default_factory=dict)


@dataclass
class TopicSearchRunResult:
    topic: str
    queries_run: int
    candidates_found: int
    downloaded: int
    download_failed: int
    duplicates_skipped: int
    accepted: int
    rejected: int
    needs_review: int
    distinct_domains_found: int
    classified_documents: dict[str, BatchClassificationResult] = field(default_factory=dict)


def _download(session: requests.Session, url: str, timeout=(5.0, 20.0)) -> bytes:
    """Raises DownloadError -- never returns None -- so a failed download
    can never be mistaken for "checked, nothing there". Uses safe_get()
    (url_safety.py), which validates the URL AND every redirect hop along
    the way -- a plain requests.Session.get() call follows redirects
    automatically with no re-validation, which is the actual, proven way
    an SSRF check gets bypassed in practice: the original URL passes, the
    server 302s somewhere else entirely, and that target is never checked.
    """
    try:
        resp = safe_get(session, url, timeout=timeout, stream=True)
        resp.raise_for_status()
    except UnsafeURLError as exc:
        logger.warning("refusing to fetch %s: %s", url, exc)
        raise DownloadError(f"Refused to fetch {url}: {exc}") from exc
    except requests.RequestException as exc:
        raise DownloadError(f"GET {url} failed: {exc}") from exc

    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            raise DownloadError(f"GET {url} exceeded {MAX_DOWNLOAD_BYTES} byte cap -- aborted mid-stream")
        chunks.append(chunk)
    return b"".join(chunks)


def _process_candidate(
    candidate: Candidate,
    organisation: str,
    registry: SourceRegistry,
    topic: str,
    session: requests.Session,
    text_extractor,
    scan_detector,
    keywords: list[str] | None = None,
    classify_accepted: bool = False,
    raw_store_dir: str = DEFAULT_RAW_STORE_DIR,
    llm_qualifier=None,
    page_fetcher=None,
    render_fallback_fetcher=None,
) -> tuple[str, str | None, BatchClassificationResult | None]:
    """Returns (outcome, classification) where outcome is one of:
    "download_failed", "duplicate", "rejected", "needs_review", "accepted".
    classification is None unless classify_accepted=True AND outcome ==
    "accepted" -- classification never runs on a rejected, needs-review,
    duplicate, or failed candidate; there is nothing useful to classify in
    those cases and doing so anyway would just be spending real API calls
    on documents already known not to matter.

    classify_accepted defaults to False deliberately -- see the
    conversation this was built from: turning this on unconditionally
    would mean every accepted document during a search silently costs
    real Azure OpenAI calls (one per paragraph-sized segment) and adds
    real latency, which is a bad default for something as casual as
    typing into the search box. Opt in explicitly when you want it.

    classification results are returned in memory, not persisted --
    there is still no database table for Segments/PolicyObjects (only
    SourceDocument has one). This is a deliberate, documented interim
    choice, not an oversight: building the durable version before there's
    a proven usage pattern for it would be the premature-complexity trap.
    Nothing here blocks building that table later; nothing here assumes
    it won't exist.

    page_fetcher: an optional PageFetcher (see page_fetcher.py). Defaults
    to None, meaning the existing plain-requests _download() below is used
    -- fully backward compatible. Pass a PlaywrightPageFetcher to render
    JavaScript-heavy pages instead. Whichever fetcher is used, robots.txt
    has already been checked at discovery time (discover_for_organisation /
    discover_via_search) -- this function never re-checks it and must
    never be handed a URL that hasn't already cleared that gate.

    render_fallback_fetcher: an optional PageFetcher, ignored if
    page_fetcher is already forcing every candidate through one fetcher.
    Unlike page_fetcher (all-or-nothing), this is a heuristic: plain HTTP
    is tried first for every candidate, and only if the extracted HTML
    body looks suspiciously thin (MIN_BODY_CHARS_BEFORE_RENDER_FALLBACK)
    is this fetcher tried as a second attempt on the same URL -- and even
    then, only kept if it genuinely found more content, not just because
    it ran. This is what makes Playwright practical to actually turn on:
    the alternative was rendering every single page with a full browser,
    which is real overhead paid on every candidate whether or not it
    needed it.

    document_type is now determined by text_extractor's own return value
    (see document_dispatch.extract_preview_any), not hardcoded to "pdf" --
    fixing a real gap: discovery.py's discover_common_paths already
    returns a company's own HTML pages as candidates, not just linked
    PDFs, but this function used to force every candidate through the
    PDF-only extractor and record document_type="pdf" unconditionally.
    A policy published as plain HTML (no PDF at all) was downloaded
    successfully and then rejected with a misleading "Could not parse as
    PDF" reason -- the real text was there, it was just never looked at
    with the right parser. scan_detector (OCR/scanned-page detection) is
    only meaningful for PDF and is skipped for HTML content, since "scanned,
    no extractable text" has no HTML equivalent.
    """
    try:
        content = page_fetcher.fetch(candidate.url) if page_fetcher is not None else _download(session, candidate.url)
    except DownloadError as exc:
        logger.warning("download failed for %s: %s", candidate.url, exc)
        return "download_failed", None, None

    file_hash = sha256_of_bytes(content)
    if registry.exists_by_hash(file_hash):
        return "duplicate", None, None

    # raw_uri now points at the actual stored bytes, not the original web
    # URL -- fixing the placeholder gap this repo has documented since its
    # first version. origin_url (set below, unchanged) keeps the web URL
    # for reference; raw_uri is what a later, separate classification step
    # actually reads from -- the entire reason this needs to exist at all.
    try:
        raw_uri = save_raw(content, file_hash, base_dir=raw_store_dir)
    except RawStoreError as exc:
        # A full disk or permissions problem must not silently block every
        # other candidate in the run, and must not be hidden as if the
        # document simply wasn't accepted -- it's a distinct, visible
        # infrastructure failure, logged loudly, with a sentinel raw_uri
        # so nobody mistakes this row for one with real stored bytes.
        logger.error("could not save raw bytes for %s: %s", candidate.url, exc)
        raw_uri = f"STORAGE_FAILED:{exc}"

    try:
        title_text, body_sample, page_count, document_type = text_extractor(content)
    except DocumentParsingError as exc:
        # A downloaded file that is neither a valid PDF nor recognizable
        # HTML (an error page served with a misleading extension, a
        # truncated download) must be recorded as a distinct, explicit
        # rejection reason -- not silently treated as "empty content,
        # reject for no title/body signal," which would look identical
        # to a real document that legitimately isn't a policy.
        logger.warning("could not extract text from %s: %s", candidate.url, exc)
        doc = SourceDocument(
            raw_uri=raw_uri, file_hash=file_hash, retrieved_at=datetime.now(timezone.utc),
            origin_url=candidate.url, organisation=organisation, topic_candidate=topic,
            document_type="pdf",  # unknown content, but the schema requires a value -- pdf|html|docx
            robots_allowed=True, accepted=False, acceptance_reason="n/a",
            rejection_reason=f"Could not parse as a document: {exc}", byte_size=len(content),
        )
        registry.register(doc)
        return "rejected", doc.source_id, None

    if (
        render_fallback_fetcher is not None
        and page_fetcher is None  # if page_fetcher was already forced, there's nothing to "fall back" from
        and document_type == "html"
        and len(body_sample.strip()) < MIN_BODY_CHARS_BEFORE_RENDER_FALLBACK
    ):
        # The plain-HTTP fetch parsed fine as HTML but yielded suspiciously
        # thin content -- exactly what a JavaScript-rendered page looks
        # like before its script runs (see page_fetcher.py). Try a real
        # render, but only keep it if it genuinely found more content --
        # a short page can also just be a short real page, and this must
        # not force every thin document through an expensive browser
        # render with nothing to show for it.
        try:
            rendered_content = render_fallback_fetcher.fetch(candidate.url)
        except DownloadError as exc:
            logger.info("render fallback fetch failed for %s, keeping plain-HTTP result: %s", candidate.url, exc)
        else:
            try:
                r_title, r_body, r_page_count, r_doc_type = text_extractor(rendered_content)
            except DocumentParsingError as exc:
                logger.info("render fallback content for %s could not be parsed, keeping plain-HTTP result: %s",
                            candidate.url, exc)
            else:
                if len(r_body.strip()) > len(body_sample.strip()):
                    # Genuinely different bytes -- re-hash and re-check
                    # duplicate/raw-store for THIS content specifically,
                    # rather than assuming the plain-HTTP version's hash
                    # still applies. The original plain-HTTP bytes stay
                    # saved in the raw store too; nothing is deleted, only
                    # the SourceDocument row points at whichever version
                    # was actually used downstream.
                    rendered_hash = sha256_of_bytes(rendered_content)
                    if registry.exists_by_hash(rendered_hash):
                        return "duplicate", None, None
                    try:
                        raw_uri = save_raw(rendered_content, rendered_hash, base_dir=raw_store_dir)
                    except RawStoreError as exc:
                        logger.error("could not save rendered raw bytes for %s: %s", candidate.url, exc)
                        raw_uri = f"STORAGE_FAILED:{exc}"
                    logger.info(
                        "render fallback found more content for %s (%d -> %d chars after stripping)",
                        candidate.url, len(body_sample.strip()), len(r_body.strip()),
                    )
                    content, file_hash = rendered_content, rendered_hash
                    title_text, body_sample, page_count, document_type = r_title, r_body, r_page_count, r_doc_type

    if document_type == "pdf":
        scan_info = scan_detector(content)
        if scan_info and scan_info.get("likely_scanned"):
            doc = SourceDocument(
                raw_uri=raw_uri,
                file_hash=file_hash,
                retrieved_at=datetime.now(timezone.utc),
                origin_url=candidate.url,
                organisation=organisation,
                topic_candidate=topic,
                document_type=document_type,
                robots_allowed=True,
                accepted=False,
                acceptance_reason="n/a",
                rejection_reason=(
                    f"Detected as likely scanned/image-only "
                    f"({len(scan_info['suspect_scanned_pages'])}/{scan_info['page_count']} pages with "
                    f"no extractable text and an embedded image) -- not routed through OCR by this "
                    f"pipeline. Held for human review or a future OCR step, not silently rejected "
                    f"as 'no content'."
                ),
                requires_ocr=True, byte_size=len(content),
            )
            registry.register(doc)
            return "needs_review", doc.source_id, None

    result = validate_document(
        title_or_first_page_text=title_text, full_text_sample=body_sample,
        page_count=page_count, topic=topic, keywords=keywords, llm_qualifier=llm_qualifier,
    )
    doc = SourceDocument(
        raw_uri=raw_uri,
        file_hash=file_hash, retrieved_at=datetime.now(timezone.utc),
        origin_url=candidate.url, organisation=organisation, topic_candidate=topic,
        document_type=document_type, robots_allowed=True,  # discovery already filtered on robots
        accepted=result.accepted,
        acceptance_reason=result.reason if result.accepted else "n/a",
        rejection_reason=None if result.accepted else result.reason,
        byte_size=len(content),
        document_class=result.document_class,
    )
    registry.register(doc)

    if result.needs_review:
        return "needs_review", doc.source_id, None
    if not result.accepted:
        return "rejected", doc.source_id, None

    classification = None
    if classify_accepted:
        from polaris.classifier.cascade import classify_document
        from polaris.classifier.document_dispatch import segment_any

        try:
            raw_segments = segment_any(content, doc.source_id, document_type)
        except DocumentParsingError as exc:
            # segment_any can fail even though extract_preview_any already
            # succeeded on the same bytes -- they exercise different code
            # paths inside PyMuPDF/BeautifulSoup, so this is a real,
            # if rare, possibility, not paranoia. The document IS already
            # accepted and registered regardless of whether classification
            # can run on it -- this must not turn an accept into a failure.
            # classify_document() itself never raises ClassificationError
            # (it catches per-segment failures internally into
            # BatchClassificationResult.failures), so segmentation is the
            # only real failure boundary here.
            logger.error("segmentation failed for accepted document %s (%s): %s",
                         doc.source_id, candidate.url, exc)
        else:
            classification = classify_document(raw_segments, source_id=doc.source_id)

    return "accepted", doc.source_id, classification


def _default_extractors(text_extractor, scan_detector):
    """Shared by both entry points -- see run_for_organisation's docstring
    for BUG-08, the reason these must default to the real implementations,
    never to None. text_extractor now defaults to extract_preview_any
    (PDF-or-HTML dispatch), not the PDF-only extract_preview -- see
    document_dispatch.py and _process_candidate's docstring."""
    if text_extractor is None:
        from polaris.classifier.document_dispatch import extract_preview_any
        text_extractor = extract_preview_any
    if scan_detector is None:
        from polaris.classifier.pdf_segmenter import detect_text_extractability
        scan_detector = detect_text_extractability
    return text_extractor, scan_detector


def run_for_organisation(
    organisation: str,
    base_url: str,
    registry: SourceRegistry,
    topic: str = "GFT",
    text_extractor=None,
    scan_detector=None,
    classify_accepted: bool = False,
    raw_store_dir: str = DEFAULT_RAW_STORE_DIR,
    llm_qualifier=None,
    page_fetcher=None,
    render_fallback_fetcher=None,
) -> RunResult:
    """text_extractor: callable(bytes) -> (title_or_first_page_text, full_text_sample, page_count).
    Defaults to polaris.classifier.pdf_segmenter.extract_preview -- the
    real implementation -- not a no-op. Override only for tests that need
    to run without PyMuPDF or real PDF bytes.

    scan_detector: callable(bytes) -> dict, matching
    polaris.classifier.pdf_segmenter.detect_text_extractability's return
    shape. Also defaults to the real implementation for the same reason.

    classify_accepted: when True, every accepted document is also
    segmented and classified (paragraph-level LET labels), not just
    accepted/rejected at the document level. Defaults to False --
    deliberately opt-in, not automatic, since it means one real Azure
    OpenAI call per paragraph-sized segment of every accepted document.
    Results are returned in RunResult.classified_documents (keyed by
    source_id), in memory only -- there is still no database table for
    Segments/PolicyObjects (see README "known gaps"), so nothing here
    persists across runs yet. That's a deliberate, separate decision, not
    an oversight.

    BUG FIXED HERE, found during audit: both parameters used to default to
    None, and a None text_extractor silently fell back to title_text="",
    body_sample="", page_count=None -- which document_validator.py always
    rejects ("no title or body signal"). The function ran without error,
    downloaded real candidates, and rejected every single one of them,
    because nothing anywhere in this codebase (including this function's
    own test) ever passed a real text_extractor. Fixed by making the
    convenient default the CORRECT default, with dependency injection
    preserved for tests that genuinely want to skip PDF parsing.
    """
    text_extractor, scan_detector = _default_extractors(text_extractor, scan_detector)

    session = build_session()
    robots = RobotsGate(session=session)
    candidates: list[Candidate] = discover_for_organisation(base_url, session, robots, topic=topic)

    counts = {"download_failed": 0, "duplicate": 0, "rejected": 0, "needs_review": 0, "accepted": 0}
    downloaded = 0
    classified_documents: dict[str, BatchClassificationResult] = {}
    for c in candidates:
        outcome, source_id, classification = _process_candidate(
            c, organisation, registry, topic, session, text_extractor, scan_detector,
            classify_accepted=classify_accepted, raw_store_dir=raw_store_dir, llm_qualifier=llm_qualifier,
            page_fetcher=page_fetcher, render_fallback_fetcher=render_fallback_fetcher,
        )
        counts[outcome] += 1
        if outcome != "download_failed":
            downloaded += 1
        if classification is not None:
            classified_documents[source_id] = classification

    return RunResult(
        organisation=organisation,
        candidates_found=len(candidates),
        downloaded=downloaded,
        download_failed=counts["download_failed"],
        duplicates_skipped=counts["duplicate"],
        accepted=counts["accepted"],
        rejected=counts["rejected"],
        needs_review=counts["needs_review"],
        classified_documents=classified_documents,
    )


def run_topic_search(
    topic: str,
    search_provider,
    registry: SourceRegistry,
    text_extractor=None,
    scan_detector=None,
    num_results_per_query: int = 20,
    keywords: list[str] | None = None,
    classify_accepted: bool = False,
    raw_store_dir: str = DEFAULT_RAW_STORE_DIR,
    llm_qualifier=None,
    page_fetcher=None,
    render_fallback_fetcher=None,
) -> TopicSearchRunResult:
    """The capability explicitly asked for: search the open web for a
    policy type across organisations not already known, rather than
    checking one known organisation's site. Requires a real, configured
    SearchProvider -- see search_provider.py's get_default_search_provider(),
    which raises SearchProviderError rather than silently returning nothing
    if no provider is configured (W10 is still an open decision; there is
    no meaningful fallback for "search the web" the way there is for LLM
    classification).

    keywords, when given, overrides the TOPIC_KEYWORDS lookup for `topic`
    -- this is what lets a person type a free-text phrase ("data privacy
    policy") and have it drive both the search queries (discover_via_search)
    and the accept/reject decision (document_validator), without needing a
    pre-registered topic code for every phrase anyone might type. See
    run_free_text_search() below, and the web UI in webapp.py, for the
    actual free-text entry point -- this function still takes `topic` as
    a required label (used for the registry's topic_candidate field and
    log messages) even when keywords overrides the lookup.

    classify_accepted: see run_for_organisation's docstring -- same
    opt-in behaviour, same in-memory-only result location
    (TopicSearchRunResult.classified_documents), same reasoning (real
    per-call cost, no durable Segments table yet).

    Organisation identity is explicitly NOT resolved here. Each accepted
    or rejected document is registered with organisation =
    "unconfirmed:<domain>" -- a visible, explicit placeholder, never a
    silent guess -- because confirming which real-world organisation a
    domain belongs to is deferred to a human-confirmed table (C7), not
    something this function invents. A caller reviewing results should
    treat every organisation field starting with "unconfirmed:" as
    needing that confirmation before being counted in any benchmark
    population.
    """
    text_extractor, scan_detector = _default_extractors(text_extractor, scan_detector)

    session = build_session()
    robots = RobotsGate(session=session)

    try:
        candidates: list[Candidate] = discover_via_search(
            topic, search_provider, robots, num_results_per_query, keywords=keywords,
        )
    except SearchProviderError:
        # Unlike a single query failing inside discover_via_search (which
        # is caught there and logged, so other queries still run), a
        # provider-level failure here (e.g. missing credentials) means
        # nothing can be searched at all -- this must propagate, not
        # return an empty, misleadingly-clean result.
        raise

    from polaris.scraper.discovery import TOPIC_KEYWORDS
    queries_run = len(keywords) if keywords is not None else len(TOPIC_KEYWORDS.get(topic, []))
    distinct_domains = {urlparse(c.url).netloc for c in candidates}

    counts = {"download_failed": 0, "duplicate": 0, "rejected": 0, "needs_review": 0, "accepted": 0}
    downloaded = 0
    classified_documents: dict[str, BatchClassificationResult] = {}
    for c in candidates:
        domain = urlparse(c.url).netloc
        organisation = f"{UNCONFIRMED_ORG_PREFIX}{domain}"
        outcome, source_id, classification = _process_candidate(
            c, organisation, registry, topic, session, text_extractor, scan_detector,
            keywords=keywords, classify_accepted=classify_accepted, raw_store_dir=raw_store_dir,
            llm_qualifier=llm_qualifier, page_fetcher=page_fetcher, render_fallback_fetcher=render_fallback_fetcher,
        )
        counts[outcome] += 1
        if outcome != "download_failed":
            downloaded += 1
        if classification is not None:
            classified_documents[source_id] = classification

    return TopicSearchRunResult(
        topic=topic,
        queries_run=queries_run,
        candidates_found=len(candidates),
        downloaded=downloaded,
        download_failed=counts["download_failed"],
        duplicates_skipped=counts["duplicate"],
        accepted=counts["accepted"],
        rejected=counts["rejected"],
        needs_review=counts["needs_review"],
        distinct_domains_found=len(distinct_domains),
        classified_documents=classified_documents,
    )


def run_free_text_search(
    query_phrase: str,
    search_provider,
    registry: SourceRegistry,
    text_extractor=None,
    scan_detector=None,
    num_results_per_query: int = 20,
    classify_accepted: bool = False,
    raw_store_dir: str = DEFAULT_RAW_STORE_DIR,
    llm_qualifier=None,
    page_fetcher=None,
    render_fallback_fetcher=None,
) -> TopicSearchRunResult:
    """The actual entry point for "type what kind of policy you're
    looking for" -- wraps run_topic_search with keywords derived directly
    from the typed phrase, rather than requiring a pre-registered topic
    code like "GFT". This is what webapp.py's search box calls.

    query_phrase is used, lowercased and stripped, as the ONE keyword
    driving both search queries and document_validator's title/body
    matching -- not split into individual words, since "data privacy
    policy" as one phrase is a much more precise signal than "data",
    "privacy", and "policy" matched independently (the latter would match
    almost any corporate document). topic_candidate in the registry is
    set to a normalised slug of the phrase, so results from different
    free-text searches remain distinguishable from each other and from
    registered topic codes like "GFT".

    classify_accepted and llm_qualifier both default to their "off" state
    here too, same reasoning for both: the web UI passes them explicitly
    when wanted, rather than this function silently upgrading the
    cost/latency profile of every search someone runs out of curiosity.
    """
    cleaned = query_phrase.strip().lower()
    if not cleaned:
        raise ValueError("query_phrase must not be empty")
    topic_label = "FREE:" + "-".join(cleaned.split())[:60]
    return run_topic_search(
        topic_label, search_provider, registry,
        text_extractor=text_extractor, scan_detector=scan_detector,
        num_results_per_query=num_results_per_query, keywords=[cleaned],
        classify_accepted=classify_accepted, raw_store_dir=raw_store_dir, llm_qualifier=llm_qualifier,
        page_fetcher=page_fetcher, render_fallback_fetcher=render_fallback_fetcher,
    )
