"""A very simple web interface: one search box, type the kind of policy
you're looking for, get back what was found on the internet.

This is the actual entry point for the capability requested in this
conversation -- everything else (search_provider.py, discover_via_search,
run_free_text_search) is the plumbing this thin UI sits on top of.

Run it:
    export BRAVE_API_KEY=your-key-here
    python -m polaris.webapp.app
    # then open http://127.0.0.1:5000

This is a local development tool, not a hosted product -- Flask's
built-in dev server, no auth, no deployment configuration. Runs on your
own machine against your own database file (polaris_registry.db, same
one every other script in this repo uses).

Failure modes: a search with no BRAVE_API_KEY configured shows a clear
message in the page, not a stack trace or a silently empty result list --
get_default_search_provider() already raises SearchProviderError for
this, and the route below catches it and renders it as a normal page
message. A malformed/empty query is rejected with a form validation
message before any search runs. Any other unexpected error is caught at
the route level and shown as a plain-language message with the real
exception text, rather than Flask's default debug traceback page, since
this may run with debug=False in normal use.
"""

from __future__ import annotations

from flask import Flask, render_template_string, request

from polaris.common.exceptions import SearchProviderError
from polaris.common.logging_config import configure, get_logger
from polaris.scraper.pipeline import run_free_text_search
from polaris.scraper.search_provider import get_default_search_provider
from polaris.scraper.source_registry import SourceRegistry

logger = get_logger(__name__)

app = Flask(__name__)

PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Policy Search</title>
<style>
  body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; max-width: 720px; margin: 48px auto; padding: 0 16px; color: #1a1a1a; }
  h1 { font-size: 1.4rem; margin-bottom: 4px; }
  p.sub { color: #666; margin-top: 0; }
  form { display: flex; gap: 8px; margin: 24px 0; }
  input[type=text] { flex: 1; padding: 10px 12px; font-size: 1rem; border: 1px solid #ccc; border-radius: 6px; }
  button { padding: 10px 18px; font-size: 1rem; background: #1a1a1a; color: white; border: none; border-radius: 6px; cursor: pointer; }
  button:hover { background: #333; }
  .error { background: #fdecea; border: 1px solid #f5c2c0; color: #9c1c15; padding: 12px 16px; border-radius: 6px; margin: 16px 0; }
  .summary { color: #444; margin: 16px 0; }
  table { width: 100%; border-collapse: collapse; margin-top: 16px; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #eee; font-size: 0.92rem; vertical-align: top; }
  th { color: #666; font-weight: 600; }
  .accepted { color: #1a7a1a; font-weight: 600; }
  .rejected { color: #999; }
  .needs_review { color: #b8860b; font-weight: 600; }
  a.url { word-break: break-all; }
  .unconfirmed { color: #999; font-style: italic; }
  .options { display: flex; align-items: center; gap: 6px; margin: -12px 0 20px; font-size: 0.88rem; color: #555; }
  .segments { margin: 4px 0 0; padding: 8px 10px; background: #fafaf8; border-radius: 6px; font-size: 0.85rem; }
  .segments div { padding: 3px 0; }
  .let-label { display: inline-block; min-width: 32px; font-weight: 600; color: #444; }
</style>
</head>
<body>
  <h1>Policy Search</h1>
  <p class="sub">Type the kind of policy you're looking for. This searches the open web, downloads what it finds, and shows what was accepted, rejected, or flagged for review.</p>

  <form method="post">
    <input type="text" name="query" placeholder="e.g. data privacy policy" value="{{ query or '' }}" autofocus required>
    <button type="submit">Search</button>
  </form>
  <label class="options">
    <input type="checkbox" name="classify" value="1" {{ 'checked' if classify else '' }}>
    Also read and classify accepted documents (costs one AI call per paragraph -- slower, and only useful once a search provider is configured)
  </label>

  {% if error %}
    <div class="error">{{ error }}</div>
  {% endif %}

  {% if result %}
    <div class="summary">
      <strong>{{ result.accepted }}</strong> accepted &middot;
      <strong>{{ result.needs_review }}</strong> needs review &middot;
      <strong>{{ result.rejected }}</strong> rejected &middot;
      <strong>{{ result.download_failed }}</strong> failed to download
      &mdash; across <strong>{{ result.distinct_domains_found }}</strong> distinct domain(s),
      {{ result.queries_run }} quer{{ 'y' if result.queries_run == 1 else 'ies' }} run.
    </div>
  {% endif %}

  {% if rows %}
    <table>
      <tr><th>Status</th><th>Organisation (unconfirmed)</th><th>URL</th><th>Reason</th></tr>
      {% for row in rows %}
      <tr>
        <td class="{{ row.status }}">{{ row.status.replace('_', ' ') }}</td>
        <td class="unconfirmed">{{ row.organisation }}</td>
        <td><a class="url" href="{{ row.url }}" target="_blank">{{ row.url }}</a></td>
        <td>
          {{ row.reason }}
          {% if row.segments %}
          <div class="segments">
            {% for seg in row.segments %}
            <div><span class="let-label">{{ seg.label }}</span>{{ seg.text }}</div>
            {% endfor %}
          </div>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
  {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def search():
    query = None
    result = None
    rows = None
    error = None
    classify = False

    if request.method == "POST":
        query = (request.form.get("query") or "").strip()
        classify = bool(request.form.get("classify"))
        if not query:
            error = "Type something to search for -- the box can't be empty."
        else:
            try:
                provider = get_default_search_provider()
                registry = SourceRegistry()
                result = run_free_text_search(query, provider, registry, classify_accepted=classify)
                rows = _rows_for_query(registry, result.topic, result.classified_documents)
            except SearchProviderError as exc:
                # No API key configured, or every query failed -- shown as
                # a normal page message, not a crash. See search_provider.py:
                # there is no meaningful fallback for "search the web," so
                # this is expected to happen until a provider is configured.
                error = str(exc)
            except Exception as exc:  # last-resort catch at the route boundary -- see module docstring
                logger.error("unexpected error handling search for %r: %s", query, exc)
                error = f"Something went wrong: {exc}"

    return render_template_string(PAGE_TEMPLATE, query=query, result=result, rows=rows, error=error, classify=classify)


def _rows_for_query(registry: SourceRegistry, topic_candidate: str, classified_documents: dict | None = None) -> list[dict]:
    """Pulls the rows this specific search just wrote, for display. Reads
    directly from the registry rather than accumulating results in memory
    during the search, so what's shown always matches what's durably
    recorded -- consistent with this repo's "the registry is the source
    of truth" principle elsewhere.

    classified_documents, when given (only populated if the "also
    classify" checkbox was ticked), attaches the in-memory classification
    result for each accepted document -- keyed by source_id, same as
    RunResult/TopicSearchRunResult. Nothing here persists these; refresh
    the page or search again and they're gone, since there is still no
    Segments database table (see README "known gaps"). This is a
    deliberate interim choice, not an oversight.
    """
    from sqlalchemy import select

    from polaris.scraper.source_registry import source_documents

    classified_documents = classified_documents or {}

    with registry.engine.connect() as conn:
        query_result = conn.execute(
            select(source_documents).where(source_documents.c.topic_candidate == topic_candidate)
        ).mappings().all()

    rows = []
    for r in query_result:
        if r["accepted"]:
            status = "accepted"
            reason = r["acceptance_reason"]
        elif r["rejection_reason"] and "review" in (r["rejection_reason"] or "").lower():
            # FRAGILE, KNOWN LIMITATION: there is no distinct "needs_review"
            # field on SourceDocument yet -- only accepted/rejected plus a
            # free-text rejection_reason that happens to mention "review"
            # for the two current needs-review cases (ambiguous
            # qualification, likely-scanned document). This is exactly the
            # gap the pending registry state-machine rebuild would fix
            # (see README/AUDIT.md) -- a real needs_review status instead
            # of guessing from prose. If a rejection reason's wording ever
            # changes to drop the word "review," this silently
            # misclassifies a needs-review row as a plain rejection in the
            # UI -- it would still be correctly recorded in the database,
            # just displayed under the wrong status here.
            status = "needs_review"
            reason = r["rejection_reason"]
        else:
            status = "rejected"
            reason = r["rejection_reason"] or "n/a"

        segments = None
        classification = classified_documents.get(r["source_id"])
        if classification is not None:
            segments = [{"label": s.label.value, "text": s.text[:140]} for s in classification.segments]

        rows.append({
            "status": status, "organisation": r["organisation"],
            "url": r["origin_url"] or r["raw_uri"], "reason": reason,
            "segments": segments,
        })
    return rows


if __name__ == "__main__":
    configure()
    app.run(debug=False, port=5000)
