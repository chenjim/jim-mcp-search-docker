"""Cookie store for domains that need a logged-in session.

``cookie.<name>.txt`` holds a raw ``k=v; k=v`` string used verbatim as the
``Cookie`` request header. Files live in a ``cookies/`` directory mounted
read-only into the container; lookup order is ``JIM_SEARCH_COOKIE_DIR`` →
``/app/cookies`` → ``/app``.

Domain → cookie-name resolution is driven by ``JIM_SEARCH_COOKIE_MAP``
(e.g. ``{"toutiao.com": "toutiao"}``) and matches the host itself or any
subdomain of it.
"""
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _candidate_dirs() -> list:
    """Directories to search for ``cookie.<name>.txt``, highest priority first."""
    dirs = []
    override = (os.environ.get("JIM_SEARCH_COOKIE_DIR") or "").strip()
    if override:
        dirs.append(Path(override))
    dirs.extend([Path("/app/cookies"), Path("/app")])
    seen = []
    for directory in dirs:
        if directory not in seen:
            seen.append(directory)
    return [directory for directory in seen if directory.is_dir()]


def load_cookie(name: str) -> str:
    """Return the raw ``Cookie`` header value for ``name``, or "" when absent.

    Read on every call (no caching) so a rotated cookie file takes effect
    without restarting the service.
    """
    if not name:
        return ""
    for directory in _candidate_dirs():
        path = directory / f"cookie.{name}.txt"
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("Cannot read cookie file %s: %s", path, exc)
                return ""
    logger.info("No cookie.%s.txt found in %s", name, [str(d) for d in _candidate_dirs()])
    return ""


def host_of(url: str) -> str:
    """Lowercased hostname of ``url``, or "" when unparseable."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def matches_domain(url: str, domains) -> bool:
    """True when ``url``'s host equals any of ``domains`` or is a subdomain."""
    host = host_of(url)
    if not host:
        return False
    for domain in domains or ():
        candidate = (domain or "").strip().lower().lstrip(".")
        if candidate and (host == candidate or host.endswith("." + candidate)):
            return True
    return False


def cookie_name_for(url: str, mapping: dict) -> str:
    """Resolve the cookie name for ``url`` via ``mapping`` (suffix match)."""
    host = host_of(url)
    if not host or not mapping:
        return ""
    for domain, name in mapping.items():
        if not name:
            continue
        candidate = (domain or "").strip().lower().lstrip(".")
        if candidate and (host == candidate or host.endswith("." + candidate)):
            return str(name).strip()
    return ""


def referer_for(url: str) -> str:
    """Site root to send as ``Referer`` — some sites 403 without one."""
    host = host_of(url)
    return f"https://{host}/" if host else ""
