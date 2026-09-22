"""Run the admin API: ``python -m tender_intelligence.admin``."""

from __future__ import annotations

import uvicorn


def main() -> int:
    uvicorn.run("tender_intelligence.admin.main:app", host="127.0.0.1", port=8000, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
