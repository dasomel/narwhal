"""Chain reader utilities (D-EV-11, 17, 31)."""

import os
from pathlib import Path
from typing import List, Union

try:
    from .canonical import parse_strict_json_line
except ImportError:
    from canonical import parse_strict_json_line


class ChainIntegrityError(ValueError):
    """Raised when evidence chain fails integrity or strict formatting."""


def read_chain(path: Union[str, Path, int]) -> List[dict]:
    """Read entries from a JSON lines chain file or fd (D-EV-11, 17, 31)."""
    if isinstance(path, int):
        os.lseek(path, 0, os.SEEK_SET)
        entries: List[dict] = []
        with os.fdopen(os.dup(path), "rb") as f:
            for line_no, raw_line in enumerate(f, start=1):
                try:
                    entries.append(parse_strict_json_line(raw_line, line_no))
                except Exception as e:
                    raise ChainIntegrityError(f"unparseable JSON line {line_no}: {e}")
        return entries

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"chain file not found: {path}")
    if not p.is_file():
        raise ChainIntegrityError(f"chain path is not a regular file: {path}")

    entries = []
    with open(p, "rb") as f:
        for line_no, raw_line in enumerate(f, start=1):
            try:
                entries.append(parse_strict_json_line(raw_line, line_no))
            except Exception as e:
                raise ChainIntegrityError(f"unparseable JSON line {line_no}: {e}")
    return entries
