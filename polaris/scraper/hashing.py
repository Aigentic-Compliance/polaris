"""Immutable content hashing and dedupe checks.

SHA-256 over raw bytes, hashed before any transformation (PDF-to-text,
re-encoding, etc.) so the hash always identifies the original file, per
POLARIS v5.0 principle "Everything traces" and the ADAPT note in the audit
("hash before transformation; immutable original").

Failure modes: sha256_of_file deliberately does NOT catch OSError/
FileNotFoundError -- a missing or unreadable file at this stage is a bug in
the caller (it should have been downloaded and checked already), and
hiding that behind a caught exception would turn a real defect into a
silently-skipped file. Let it raise.
"""

from __future__ import annotations

import hashlib


def sha256_of_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return "sha256:" + h.hexdigest()
