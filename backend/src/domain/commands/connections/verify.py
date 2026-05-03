"""Verify a Connection by hitting the source's auth endpoint."""

from src.domain.connections import ConnectionStore, VerifyResult


def execute(store: ConnectionStore, slug: str) -> VerifyResult:
    return store.verify(slug)
