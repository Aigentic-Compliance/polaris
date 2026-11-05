import pytest

from polaris.scraper.hashing import sha256_of_bytes, sha256_of_file


def test_hash_is_deterministic_and_shaped():
    h1 = sha256_of_bytes(b"hello world")
    h2 = sha256_of_bytes(b"hello world")
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64


def test_different_content_different_hash():
    assert sha256_of_bytes(b"a") != sha256_of_bytes(b"b")


def test_sha256_of_file_matches_sha256_of_bytes(tmp_path):
    """sha256_of_file had zero call sites anywhere in this codebase before
    this audit -- genuinely dead code, found by grepping for references.
    Not deleted (flagged for a decision instead, see AUDIT.md), but now at
    least verified to actually work correctly if/when something calls it."""
    content = b"some file content for hashing"
    path = tmp_path / "test.bin"
    path.write_bytes(content)

    assert sha256_of_file(path) == sha256_of_bytes(content)


def test_sha256_of_file_handles_content_larger_than_chunk_size(tmp_path):
    content = b"x" * (2 * 1024 * 1024 + 137)  # bigger than the default 1 MB chunk size, not a round number
    path = tmp_path / "big.bin"
    path.write_bytes(content)
    assert sha256_of_file(path, chunk_size=1024) == sha256_of_bytes(content)


def test_sha256_of_file_missing_file_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        sha256_of_file("/no/such/path/exists.bin")
