"""HTTP session with Windows authentication, plus OData error handling."""
from __future__ import annotations

import getpass
import os

import requests

from . import config

AUTH_CHOICES = ("sspi", "ntlm", "none")

ODATA_HEADERS = {
    "Accept": "application/json",
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
}


class ODataError(RuntimeError):
    """An HTTP or OData error, carrying the server's own message where available."""


def _missing(extra: str, package: str) -> ODataError:
    return ODataError(f"Authentication needs '{package}': pip install \"odata-openapi[{extra}]\"")


def make_session(auth: str = "sspi", user: str | None = None, password: str | None = None,
                 verify: bool | str = True) -> requests.Session:
    """Create a requests session for the Web API.

    auth: sspi (Windows SSO), ntlm (explicit user/password), or none.
    verify: True, False, or a path to a CA bundle (for an internal CA).
    """
    s = requests.Session()
    s.headers.update(ODATA_HEADERS)
    s.verify = verify

    if auth == "sspi":
        try:
            from requests_negotiate_sspi import HttpNegotiateAuth
        except ImportError as e:
            raise _missing("sspi", "requests-negotiate-sspi") from e
        s.auth = HttpNegotiateAuth()
    elif auth == "ntlm":
        user = user or os.environ.get(config.ENV_USER) or input("User (DOMAIN\\name): ")
        password = password or os.environ.get(config.ENV_PASSWORD) or getpass.getpass(f"Password for {user}: ")
        try:
            from requests_ntlm import HttpNtlmAuth
        except ImportError as e:
            raise _missing("ntlm", "requests-ntlm") from e
        s.auth = HttpNtlmAuth(user, password)
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
    hint = " (not authenticated — check --auth and credentials)" if r.status_code == 401 else ""
    raise ODataError(f"{r.request.method} {r.url} -> HTTP {r.status_code}{hint}: {msg}")
