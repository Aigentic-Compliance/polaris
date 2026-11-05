import responses

from polaris.scraper.discovery import Candidate
from polaris.scraper.pipeline import _download
from polaris.common.exceptions import DownloadError


@responses.activate
def test_download_raises_download_error_not_none_on_network_failure():
    import requests

    responses.add(responses.GET, "https://example.com/broken.pdf", status=500)
    session = requests.Session()
    try:
        _download(session, "https://example.com/broken.pdf")
        assert False, "expected DownloadError"
    except DownloadError as exc:
        assert "broken.pdf" in str(exc)


@responses.activate
def test_download_raises_on_size_cap_exceeded():
    import requests

    from polaris.scraper import pipeline

    big_body = b"x" * 1000
    responses.add(responses.GET, "https://example.com/big.pdf", body=big_body, status=200)
    session = requests.Session()
    original_cap = pipeline.MAX_DOWNLOAD_BYTES
    pipeline.MAX_DOWNLOAD_BYTES = 100  # shrink the cap so the 1000-byte body trips it
    try:
        try:
            _download(session, "https://example.com/big.pdf")
            assert False, "expected DownloadError"
        except DownloadError as exc:
            assert "byte cap" in str(exc)
    finally:
        pipeline.MAX_DOWNLOAD_BYTES = original_cap


@responses.activate
def test_download_succeeds_returns_bytes():
    import requests

    responses.add(responses.GET, "https://example.com/good.pdf", body=b"%PDF-1.4 fake", status=200)
    session = requests.Session()
    content = _download(session, "https://example.com/good.pdf")
    assert content == b"%PDF-1.4 fake"
