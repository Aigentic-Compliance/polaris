"""Content-addressed raw byte storage -- closes the gap flagged as a
placeholder since the first version of this repo: SourceDocument.raw_uri
was set to the original web URL, identical to origin_url, because there
was nowhere else for it to point. Every document's original bytes existed
only for the duration of one function call and then were gone -- fine
when classification happened immediately inline (in memory, same call),
not fine the moment classification becomes a genuinely separate later
step, which has nothing to classify without this.

Design, per the WP1 framework's own C4.1 ("content-addressed write-once
raw store... separate from the operational database"): a plain directory,
one file per SHA-256 hash, no database involved. The registry (a real
database) stores metadata and a raw_uri pointing here; it never stores the
bytes themselves. This is a deliberately boring first version -- a local
folder, not object storage (GCS/S3) -- because nothing about the pipeline
downstream needs to know the difference: it asks for bytes by hash and
gets them back, or gets a clear RawStoreError. Swapping the local folder
for a real object store later is a change to this one file, not to
anything that calls it.

Failure modes: write failures (disk full, permission denied) raise
RawStoreError with the path and the underlying OSError, not a silent
"pretend it worked." Read failures distinguish two real, different cases:
the file is simply missing (moved, deleted, never written) vs. the file
exists but its content no longer matches the hash it's named after (silent
disk corruption, a mistaken manual edit) -- the second case is far more
dangerous, since it means something is being classified that is NOT what
was actually accepted, and is checked and raised explicitly rather than
left to surface later as a confusing classification result.
"""

from __future__ import annotations

import re
from pathlib import Path

from polaris.common.exceptions import RawStoreError
from polaris.common.logging_config import get_logger
from polaris.scraper.hashing import sha256_of_bytes

logger = get_logger(__name__)

DEFAULT_RAW_STORE_DIR = "raw_store"

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _path_for_hash(file_hash: str, base_dir: str | Path) -> Path:
    # Defense in depth, not just convenience: every current caller always
    # passes the output of sha256_of_bytes(), but this validates the
    # format explicitly rather than trusting that forever -- a malformed
    # or maliciously-crafted file_hash containing path-traversal segments
    # (e.g. "../../etc/passwd") must never reach a filesystem path
    # construction, regardless of how it got here.
    if not _HASH_PATTERN.match(file_hash):
        raise RawStoreError(f"file_hash {file_hash!r} is not a valid sha256:<64 hex chars> string -- refusing to construct a path from it")
    digest = file_hash.split(":", 1)[-1]
    return Path(base_dir) / f"{digest}.bin"


def save_raw(content: bytes, file_hash: str, base_dir: str | Path = DEFAULT_RAW_STORE_DIR) -> str:
    """Writes content to the store, keyed by its own hash. Idempotent --
    if a file already exists at that path, it is not rewritten (the hash
    guarantees identical content, so there is nothing to update; this also
    means the store is naturally write-once, matching C4.1's requirement).
    Returns the path as a string, for storing in SourceDocument.raw_uri.
    """
    path = _path_for_hash(file_hash, base_dir)
    if path.exists():
        return str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename -- an interrupted write (process
        # killed mid-save) must never leave a partial file sitting at the
        # final path with a hash-matching name but truncated content.
        tmp_path = path.with_suffix(".bin.tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(path)
    except OSError as exc:
        logger.error("failed to write raw store file %s: %s", path, exc)
        raise RawStoreError(f"Could not write {path}: {exc}") from exc
    return str(path)


def load_raw(raw_uri: str, expected_hash: str | None = None) -> bytes:
    """Reads bytes back from a raw_uri produced by save_raw(). If
    expected_hash is given (normally SourceDocument.file_hash for the same
    row), the loaded bytes are re-hashed and checked against it -- a
    mismatch means the file on disk is not what was originally accepted
    (corruption, an accidental overwrite) and must be surfaced loudly, not
    fed into classification as if it were the real thing.

    Defense in depth: raw_uri ultimately comes from a database row
    (SourceDocument.raw_uri), not a hardcoded trusted value -- this checks
    the filename matches save_raw()'s own <64 hex chars>.bin pattern before
    ever opening it, so a corrupted or maliciously-edited database row
    pointing at an arbitrary path (e.g. "../../../etc/passwd") is refused
    here rather than silently read.
    """
    path = Path(raw_uri)
    if ".." in path.parts:
        raise RawStoreError(f"raw_uri {raw_uri!r} contains a path-traversal segment ('..') -- refusing to read it")
    if not _HASH_PATTERN.match(f"sha256:{path.stem}") or path.suffix != ".bin":
        raise RawStoreError(
            f"raw_uri {raw_uri!r} does not look like a file this store ever wrote "
            f"(expected <64 hex chars>.bin) -- refusing to read it"
        )
    if not path.exists():
        raise RawStoreError(f"Raw store file not found: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        logger.error("failed to read raw store file %s: %s", path, exc)
        raise RawStoreError(f"Could not read {path}: {exc}") from exc

    if expected_hash is not None:
        actual_hash = sha256_of_bytes(content)
        if actual_hash != expected_hash:
            raise RawStoreError(
                f"Integrity check failed for {path}: expected {expected_hash}, got {actual_hash}. "
                f"The stored file no longer matches the hash it was saved under -- do not classify this."
            )
    return content
