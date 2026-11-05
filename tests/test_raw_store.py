import pytest

from polaris.common.exceptions import RawStoreError
from polaris.scraper.hashing import sha256_of_bytes
from polaris.scraper.raw_store import load_raw, save_raw


def test_save_and_load_round_trip(tmp_path):
    content = b"some real document bytes"
    file_hash = sha256_of_bytes(content)
    path = save_raw(content, file_hash, base_dir=tmp_path)
    assert load_raw(path) == content


def test_save_is_idempotent_does_not_rewrite(tmp_path):
    content = b"same content twice"
    file_hash = sha256_of_bytes(content)
    path1 = save_raw(content, file_hash, base_dir=tmp_path)
    path2 = save_raw(content, file_hash, base_dir=tmp_path)
    assert path1 == path2
    assert load_raw(path1) == content


def test_save_creates_base_dir_if_missing(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "yet"
    content = b"content"
    path = save_raw(content, sha256_of_bytes(content), base_dir=nested)
    assert load_raw(path) == content


def test_load_missing_file_raises_raw_store_error(tmp_path):
    with pytest.raises(RawStoreError, match="not found"):
        load_raw(str(tmp_path / ("a" * 64 + ".bin")))  # correctly-shaped filename, genuinely absent


def test_load_with_hash_mismatch_raises_integrity_error(tmp_path):
    content = b"original content"
    file_hash = sha256_of_bytes(content)
    path = save_raw(content, file_hash, base_dir=tmp_path)

    # Simulate corruption / a mistaken overwrite after the fact.
    from pathlib import Path
    Path(path).write_bytes(b"corrupted, different content")

    with pytest.raises(RawStoreError, match="Integrity check failed"):
        load_raw(path, expected_hash=file_hash)


def test_load_without_expected_hash_skips_integrity_check(tmp_path):
    """Callers that don't have a hash to check against (e.g. reading an
    arbitrary file) can still read -- the check is opt-in via
    expected_hash, not mandatory."""
    content = b"anything"
    path = save_raw(content, sha256_of_bytes(content), base_dir=tmp_path)
    from pathlib import Path
    Path(path).write_bytes(b"different now")
    assert load_raw(path) == b"different now"  # no expected_hash given -- no check, no raise


def test_different_content_produces_different_paths(tmp_path):
    path_a = save_raw(b"content a", sha256_of_bytes(b"content a"), base_dir=tmp_path)
    path_b = save_raw(b"content b", sha256_of_bytes(b"content b"), base_dir=tmp_path)
    assert path_a != path_b


def test_save_raw_os_error_raises_raw_store_error(tmp_path, monkeypatch):
    from pathlib import Path

    def broken_write_bytes(self, data):
        raise OSError("simulated: disk full")

    monkeypatch.setattr(Path, "write_bytes", broken_write_bytes)
    with pytest.raises(RawStoreError, match="Could not write"):
        save_raw(b"content", sha256_of_bytes(b"content"), base_dir=tmp_path)


def test_load_raw_os_error_raises_raw_store_error(tmp_path, monkeypatch):
    from pathlib import Path

    content = b"content"
    path = save_raw(content, sha256_of_bytes(content), base_dir=tmp_path)

    def broken_read_bytes(self):
        raise OSError("simulated: permission denied")

    monkeypatch.setattr(Path, "read_bytes", broken_read_bytes)
    with pytest.raises(RawStoreError, match="Could not read"):
        load_raw(path)


def test_save_raw_rejects_malformed_hash_format(tmp_path):
    with pytest.raises(RawStoreError, match="not a valid sha256"):
        save_raw(b"content", "not-a-real-hash-format", base_dir=tmp_path)


def test_save_raw_rejects_hash_with_path_traversal_attempt(tmp_path):
    malicious_hash = "sha256:" + "../../../etc/passwd".ljust(64, "0")
    with pytest.raises(RawStoreError, match="not a valid sha256"):
        save_raw(b"content", malicious_hash, base_dir=tmp_path)


def test_load_raw_rejects_path_traversal_in_raw_uri(tmp_path):
    """Defense in depth: raw_uri comes from a database row, not a
    hardcoded trusted value -- a corrupted or maliciously-edited row
    must never be able to read an arbitrary file via '..' segments."""
    traversal_path = str(tmp_path / ".." / ".." / "etc" / ("a" * 64 + ".bin"))
    with pytest.raises(RawStoreError, match="path-traversal"):
        load_raw(traversal_path)


def test_load_raw_rejects_filename_not_matching_hash_pattern(tmp_path):
    fake_path = tmp_path / "not_a_real_hash.bin"
    fake_path.write_bytes(b"content")
    with pytest.raises(RawStoreError, match="does not look like a file this store ever wrote"):
        load_raw(str(fake_path))


def test_load_raw_rejects_wrong_extension(tmp_path):
    fake_path = tmp_path / ("a" * 64 + ".txt")
    fake_path.write_bytes(b"content")
    with pytest.raises(RawStoreError, match="does not look like a file"):
        load_raw(str(fake_path))


def test_load_raw_still_accepts_a_genuine_saved_file(tmp_path):
    """Confirms the hardening didn't break the normal, legitimate path."""
    content = b"real content"
    path = save_raw(content, sha256_of_bytes(content), base_dir=tmp_path)
    assert load_raw(path) == content
