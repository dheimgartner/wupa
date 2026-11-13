"""HTTP session with Windows SSPI authentication, plus OData error handling."""
from __future__ import annotations

import requests

AUTH_CHOICES = ("sspi", "none")

ODATA_HEADERS = {
    "Accept": "application/json",
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
}


class ODataError(RuntimeError):
    """An HTTP or OData error, carrying the server's own message where available."""


def make_session(auth: str = "sspi", verify: bool | str = True) -> requests.Session:
    """Create a requests session for the Web API.

    auth: sspi (Windows SSO) or none.
    verify: True, False, or a path to a CA bundle (for an internal CA).
    """
    s = requests.Session()
    s.headers.update(ODATA_HEADERS)
    s.verify = verify

    if auth == "sspi":
        try:
            from requests_negotiate_sspi import HttpNegotiateAuth
        except ImportError as e:
            raise ODataError(
                "SSPI authentication needs 'requests-negotiate-sspi': "
                "pip install \"odata-openapi[sspi]\""
            ) from e
        s.auth = HttpNegotiateAuth()
    elif auth != "none":
        raise ODataError(f"Unknown auth '{auth}', choose one of {', '.join(AUTH_CHOICES)}")
    return s


def check(r: requests.Response) -> requests.Response:
    """Raise ODataError with the OData error message if the response failed."""
    if r.ok:
        return r
    try:
        msg = r.json()["error"]["message"]
    except Exception:
        msg = r.text[:500].strip() or r.reason
    hint = " (not authenticated — check that SSPI is working)" if r.status_code == 401 else ""
    raise ODataError(f"{r.request.method} {r.url} -> HTTP {r.status_code}{hint}: {msg}")
