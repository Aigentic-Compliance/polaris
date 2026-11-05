"""HTTP session with retry/backoff and a real user agent.

Adapted (rewritten, not copied) from the pattern the BUAS audit rated
ADAPT-worthy: urllib3 Retry on 429/5xx with exponential backoff, connect/read
timeouts, and a session-level identifying user agent so a target site can
see who is crawling it and block/throttle if they choose to -- crawling
anonymously is itself a governance smell for a compliance company.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = "AigenticCompliancePolarisBot/0.1 (+https://aigenticcompliance.com/polaris-bot)"


def build_session(
    user_agent: str = DEFAULT_USER_AGENT,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    timeout: tuple[float, float] = (5.0, 15.0),
) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # requests has no session-level timeout; every call site must pass one
    # explicitly. Stashing the default here so call sites don't forget.
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session
