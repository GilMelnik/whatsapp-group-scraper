"""Lightweight logging helpers (no Selenium dependency)."""

from __future__ import annotations


def log(msg: str) -> None:
    print(msg, flush=True)
