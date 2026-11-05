import responses
from sqlalchemy import create_engine

from polaris.scraper.pipeline import run_for_organisation
from polaris.scraper.source_registry import SourceRegistry


@responses.activate
def test_download_failures_are_counted_not_silently_dropped():
    # robots.txt allows everything
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    # One common-path candidate the discovery module will try: /governance
    # -- make it fail so run_for_organisation must record it.
    responses.add(responses.GET, "https://example.com/governance", status=500)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    # At least the /governance candidate should have failed to download.
    assert result.download_failed >= 1
    # And the total candidates_found must still account for it -- it's not
    # simply missing from every count.
    assert result.candidates_found >= result.download_failed
