""":mod:`tender_intelligence.storage.local` — local filesystem storage."""

from __future__ import annotations

import os
from pathlib import Path

from tender_intelligence.storage.interface import ObjectStorage, StoredObject


class LocalFileSystemStorage(ObjectStorage):
    """Filesystem implementation of :class:`ObjectStorage`.

    Keys are mapped under ``root`` with path-traversal protection: a key containing ``..``
    or an absolute path is rejected. Writes go to a temp file then a rename so a crash never
    leaves a half-written object.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if key.startswith("/") or Path(key).is_absolute() or ".." in Path(key).parts:
            raise ValueError(f"unsafe storage key: {key!r}")
        return (self.root / key).resolve()

    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    def get(self, key: str) -> bytes:
        dest = self._resolve(key)
        if not dest.is_file():
            raise KeyError(key)
        return dest.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        dest = self._resolve(key)
        if dest.is_file():
            dest.unlink()

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        base = self._resolve(prefix)
        if not base.exists():
            return keys
        for path in base.rglob("*"):
            if path.is_file():
                keys.append(str(path.relative_to(self.root)).replace("\\", "/"))
        return sorted(keys)