"""Unit tests for the local filesystem storage implementation."""

from __future__ import annotations

import pytest

from tender_intelligence.storage.local import LocalFileSystemStorage


class TestLocalStorage:
    def test_roundtrip(self, tmp_storage):
        store = LocalFileSystemStorage(tmp_storage)
        obj = store.put("a/b.txt", b"hello", content_type="text/plain")
        assert obj.size_bytes == 5
        assert store.get("a/b.txt") == b"hello"
        assert store.exists("a/b.txt")
        assert store.list_keys() == ["a/b.txt"]

    def test_overwrite(self, tmp_storage):
        store = LocalFileSystemStorage(tmp_storage)
        store.put("k", b"one")
        store.put("k", b"two")
        assert store.get("k") == b"two"

    def test_delete_and_missing(self, tmp_storage):
        store = LocalFileSystemStorage(tmp_storage)
        store.put("k", b"x")
        store.delete("k")
        assert not store.exists("k")
        store.delete("k")  # no-op
        with pytest.raises(KeyError):
            store.get("k")

    @pytest.mark.parametrize("bad", ["../escape", "/abs/root", "a/../../.."])
    def test_path_traversal_rejected(self, tmp_storage, bad):
        store = LocalFileSystemStorage(tmp_storage)
        with pytest.raises(ValueError):
            store.put(bad, b"x")
        with pytest.raises(ValueError):
            store.get(bad)

    def test_list_prefix(self, tmp_storage):
        store = LocalFileSystemStorage(tmp_storage)
        store.put("tenders/1/a.pdf", b"a")
        store.put("tenders/1/b.pdf", b"b")
        store.put("kb.md", b"kb")
        assert store.list_keys("tenders/1") == ["tenders/1/a.pdf", "tenders/1/b.pdf"]