"""Unit tests for safe ZIP extraction (prompt 08 §8–§10, §2 order: proof before store).

ZIPs are untrusted input. Extraction happens in memory under :class:`ZipLimits`; unsafe,
oversized or corrupt members are rejected per member without failing the rest, and
whole-archive limit aborts raise a structured ``archive_limit``/``invalid_archive`` error.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from tender_intelligence.acquisition.errors import (
    ARCHIVE_LIMIT,
    INVALID_ARCHIVE,
    DocumentAcquisitionError,
)
from tender_intelligence.acquisition.zip import (
    ZipLimits,
    recurse_depth_exceeded,
    safe_extract_archive,
)


def _zip_bytes(members: dict[str, bytes], *, mode: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", mode) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _symlink_entry(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo(name)
        # POSIX symlink mode: the extractor must reject it, never follow it.
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"")
    return buffer.getvalue()


def _default() -> ZipLimits:
    return ZipLimits()


class TestExtraction:
    def test_flat_members_extracted_with_data(self) -> None:
        report = safe_extract_archive(
            _zip_bytes({"a.txt": b"a", "sub/b.csv": b"1,2"}), limits=_default()
        )
        assert report.ok
        assert report.archive_reason is None
        assert not report.member_failures
        by_name = {entry.name: entry.data for entry in report.entries}
        assert by_name == {"a.txt": b"a", "sub/b.csv": b"1,2"}

    def test_directory_entries_ignored(self) -> None:
        report = safe_extract_archive(
            _zip_bytes({"dir/": b"", "dir/a.txt": b"x"}), limits=_default()
        )
        names = [entry.name for entry in report.entries]
        assert "dir/" not in names
        assert "dir/a.txt" in names

    def test_nested_zip_detected_by_magic(self) -> None:
        inner = _zip_bytes({"m.txt": b"inner document"})
        report = safe_extract_archive(
            _zip_bytes({"inner.zip": inner}), limits=_default()
        )
        assert len(report.entries) == 1
        assert report.entries[0].is_nested_zip is True
        assert report.entries[0].data == inner


class TestSafety:
    @pytest.mark.parametrize(
        ("name", "reason_part"),
        [
            ("../escape.txt", "path segment"),
            ("a/../../up.txt", "path segment"),
            ("a/./self.txt", "path segment"),
            ("/etc/passwd", "absolute"),
            ("C:\\evil.bat", "drive-letter"),
            ("\\\\server\\share\\f.txt", "absolute"),
        ],
    )
    def test_unsafe_member_rejected_per_member(self, name: str, reason_part: str) -> None:
        report = safe_extract_archive(
            _zip_bytes({name: b"x", "ok.txt": b"fine"}), limits=_default()
        )
        # The archive itself is fine; only the bad member is dropped.  zipfile
        # normalises backslashes when storing names, so the reported member name is
        # the on-disk form — assert security, not the writer's spelling.
        assert report.ok
        names = [entry.name for entry in report.entries]
        assert names == ["ok.txt"]
        assert len(report.member_failures) == 1
        failure = report.member_failures[0]
        assert failure.member_name == name.replace("\\", "/")
        assert reason_part in failure.reason

    def test_symlink_entry_rejected(self) -> None:
        report = safe_extract_archive(_symlink_entry("link"), limits=_default())
        assert report.ok
        assert not report.entries
        assert report.member_failures[0].reason == "symlink/link entry is not allowed"

    def test_name_length_bounded(self) -> None:
        limits = ZipLimits(max_name_length=20)
        long_name = "x" * 25 + ".txt"
        report = safe_extract_archive(_zip_bytes({long_name: b"x"}), limits=limits)
        assert report.ok
        assert not report.entries
        assert "name exceeds" in report.member_failures[0].reason

    def test_oversized_member_rejected_per_member(self) -> None:
        limits = ZipLimits(max_single_member_bytes=64, max_total_uncompressed_bytes=10_000)
        report = safe_extract_archive(
            _zip_bytes({"big.bin": b"y" * 200, "ok.txt": b"x"}), limits=limits
        )
        assert report.ok
        names = [entry.name for entry in report.entries]
        assert names == ["ok.txt"]
        assert "single-file size limit" in report.member_failures[0].reason

    def test_high_compression_ratio_rejected(self) -> None:
        limits = ZipLimits(max_compression_ratio=10)
        # ~10 KB of a single repeated byte compresses to well over 10:1.
        report = safe_extract_archive(
            _zip_bytes({"bomb.txt": b"z" * 10_000}), limits=limits
        )
        assert report.ok
        assert not report.entries
        assert "compression ratio" in report.member_failures[0].reason


class TestWholeArchiveAborts:
    def test_malformed_zip_raises_invalid_archive(self) -> None:
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            safe_extract_archive(b"this is not a zip file at all", limits=_default())
        assert exc_info.value.category == INVALID_ARCHIVE
        assert exc_info.value.retryable is False

    def test_member_count_limit_aborts_whole_archive(self) -> None:
        limits = ZipLimits(max_members=2)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            safe_extract_archive(
                _zip_bytes({"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"}),
                limits=limits,
            )
        error = exc_info.value
        assert error.category == ARCHIVE_LIMIT
        assert error.context["members"] == 3

    def test_total_uncompressed_limit_aborts_whole_archive(self) -> None:
        limits = ZipLimits(
            max_total_uncompressed_bytes=100,
            max_single_member_bytes=200,
            max_compression_ratio=0,
        )
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            safe_extract_archive(
                _zip_bytes({"a.bin": b"a" * 60, "b.bin": b"b" * 60}), limits=limits
            )
        error = exc_info.value
        assert error.category == ARCHIVE_LIMIT
        assert error.context["total_uncompressed_bytes"] > 100


class TestRecursionDepth:
    def test_depth_budget(self) -> None:
        limits = ZipLimits(max_nested_depth=2)
        assert recurse_depth_exceeded(limits, depth=1) is False
        assert recurse_depth_exceeded(limits, depth=2) is False
        assert recurse_depth_exceeded(limits, depth=3) is True
        assert recurse_depth_exceeded(limits, depth=4) is True
