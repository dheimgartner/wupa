"""HTTP session with pluggable authentication, plus OData error handling."""
from __future__ import annotations

import getpass
import os

import requests

from . import config

AUTH_CHOICES = ("sspi", "kerberos", "ntlm", "basic", "bearer", "none")

ODATA_HEADERS = {
    "Accept": "application/json",
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
}


class GevError(RuntimeError):
    """An HTTP or OData error, carrying the server's own message where available."""


def _missing(extra: str, package: str) -> GevError:
    return GevError(f"Authentication needs '{package}': pip install \"gev-openapi[{extra}]\"")


def make_session(auth: str = "none", user: str | None = None, password: str | None = None,
                 token: str | None = None, verify: bool | str = True) -> requests.Session:
    """Create a requests session for the Web API.

    auth: sspi (Windows SSO), kerberos, ntlm, basic, bearer or none.
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
    elif auth == "kerberos":
        try:
            from requests_kerberos import OPTIONAL, HTTPKerberosAuth
        except ImportError as e:
            raise _missing("kerberos", "requests-kerberos") from e
        s.auth = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
    elif auth in ("ntlm", "basic"):
        user = user or os.environ.get(config.ENV_USER) or input("User (DOMAIN\\name): ")
        password = password or os.environ.get(config.ENV_PASSWORD) or getpass.getpass(f"Password for {user}: ")
        if auth == "ntlm":
            try:
                from requests_ntlm import HttpNtlmAuth
            except ImportError as e:
                raise _missing("ntlm", "requests-ntlm") from e
            s.auth = HttpNtlmAuth(user, password)
        else:
            s.auth = (user, password)
    elif auth == "bearer":
        token = token or os.environ.get(config.ENV_TOKEN)
        if not token:
            raise GevError(f"--auth bearer needs a token in ${config.ENV_TOKEN}")
        s.headers["Authorization"] = f"Bearer {token}"
    elif auth != "none":
        raise GevError(f"Unknown auth '{auth}', choose one of {', '.join(AUTH_CHOICES)}")
    return s


def check(r: requests.Response) -> requests.Response:
    """Raise GevError with the OData error message if the response failed."""
    if r.ok:
        return r
    try:
        msg = r.json()["error"]["message"]
    except Exception:
        msg = r.text[:500].strip() or r.reason
    hint = ""
    if r.status_code == 401:
        hint = " (not authenticated: check --auth and your credentials)"
    raise GevError(f"{r.request.method} {r.url} -> HTTP {r.status_code}{hint}: {msg}")
