"""Targeted removal of the known dead loopback proxy."""

from __future__ import annotations

from collections.abc import MutableMapping


_PROXY_NAMES = ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY")
_DEAD_PROXY_VALUES = {"http://127.0.0.1:9", "https://127.0.0.1:9"}


def clear_invalid_loopback_proxies(environ: MutableMapping[str, str]) -> list[str]:
    removed: list[str] = []
    for name in _PROXY_NAMES:
        value = environ.get(name)
        if value and value.strip().lower() in _DEAD_PROXY_VALUES:
            del environ[name]
            removed.append(name)
    return removed
