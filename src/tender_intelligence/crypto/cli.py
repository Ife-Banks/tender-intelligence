"""CLI entrypoint: ``python -m tender_intelligence.crypto` subcommands.

Subcommands:
    generate-key   Print a fresh 32-byte master key (base64) for ``TI_MASTER_KEY``.
"""

from __future__ import annotations

import base64
import sys

from tender_intelligence.crypto.secrets import generate_master_key


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] != "generate-key":
        print("usage: python -m tender_intelligence.crypto generate-key", file=sys.stderr)
        return 2
    print(base64.b64encode(generate_master_key()).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())