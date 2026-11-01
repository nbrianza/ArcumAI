# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Transport encryption (TLS) helpers for server startup (issue #38).

Pure functions, importable without starting the server or loading models.
See doc/TLS_SETUP.md for the deployment options.
"""
import ipaddress
import os


def is_loopback_host(host: str) -> bool:
    """True if ``host`` is ``localhost`` or a loopback IP address
    (127.0.0.0/8, ::1). Brackets around IPv6 addresses are ignored.
    Wildcard binds (0.0.0.0, ::), LAN addresses and other host names
    are not loopback."""
    name = (host or "").strip().strip("[]")
    if name.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def build_ssl_kwargs(certfile: str, keyfile: str) -> dict:
    """Return the uvicorn keyword arguments that enable TLS.

    Both empty: TLS is off, returns ``{}``. Both set to existing files:
    returns ``ssl_certfile`` and ``ssl_keyfile``. Only one set, or a path
    that does not exist: raises ``ValueError`` naming the variable, so the
    server stops at startup instead of running with a broken setup.
    Messages never contain file content.
    """
    if not certfile and not keyfile:
        return {}
    if not certfile:
        raise ValueError("Invalid TLS config: SSL_KEYFILE is set but SSL_CERTFILE is empty. "
                         "Set both SSL_CERTFILE and SSL_KEYFILE, or neither. See doc/TLS_SETUP.md.")
    if not keyfile:
        raise ValueError("Invalid TLS config: SSL_CERTFILE is set but SSL_KEYFILE is empty. "
                         "Set both SSL_CERTFILE and SSL_KEYFILE, or neither. See doc/TLS_SETUP.md.")
    if not os.path.isfile(certfile):
        raise ValueError("Invalid TLS config: the file in SSL_CERTFILE does not exist. See doc/TLS_SETUP.md.")
    if not os.path.isfile(keyfile):
        raise ValueError("Invalid TLS config: the file in SSL_KEYFILE does not exist. See doc/TLS_SETUP.md.")
    return {"ssl_certfile": certfile, "ssl_keyfile": keyfile}


def warn_if_unencrypted(host: str, port: int, tls_enabled: bool, upstream: bool, logger) -> bool:
    """Log one WARNING when the server listens on a non-loopback interface
    without TLS and no upstream TLS termination is declared.

    Never raises and never refuses to start (owner decision, #38).
    Returns True if the warning was logged.
    """
    if tls_enabled or upstream or is_loopback_host(host):
        return False
    logger.warning(
        f"Transport is NOT encrypted: listening on {host}:{port} over plain HTTP/WS. "
        "Passwords, session cookies and emails cross the network in cleartext. "
        "Set SSL_CERTFILE and SSL_KEYFILE, or set TLS_TERMINATED_UPSTREAM=true behind a TLS "
        "reverse proxy, or bind HOST=127.0.0.1. See doc/TLS_SETUP.md."
    )
    return True


def session_cookie_kwargs(tls_enabled: bool, upstream: bool) -> dict:
    """Return the extra ``SessionMiddleware`` keyword arguments for the
    session cookie (issue #50).

    When direct TLS is active or TLS is declared as terminated upstream,
    returns ``{"https_only": True}`` so browsers send the session cookie
    over HTTPS only. Otherwise returns ``{}``, which leaves the cookie
    exactly as before. A new dict is returned on every call.
    """
    if tls_enabled or upstream:
        return {"https_only": True}
    return {}
