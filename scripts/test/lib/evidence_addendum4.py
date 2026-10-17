"""Review round 3 regression: truncating an open writer's file to 0 bytes must poison it (D-EV-28)."""

import os

from scripts.evidence.chain import ChainIntegrityError, ChainWriter


def test_d_ev_28_truncate_to_zero(td, epub, cpub, esig, csig):
    """The st_size > 0 guard used to skip the shrink check, so a 0-byte truncate went unnoticed."""
    for op in ("append_intent", "get_head"):
        chain_p = td / f"chain_zero_{op}.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        for i in range(4):
            w.append_intent(f"g{i}", f"inv{i}")
        os.truncate(chain_p, 0)
        try:
            if op == "append_intent":
                w.append_intent("g-new", "inv-new")
            else:
                w.get_head()
            raise AssertionError(f"{op}: open writer must raise after truncate(0)")
        except ChainIntegrityError:
            pass
        assert w._poisoned is True
        assert chain_p.stat().st_size == 0, "poisoned writer must write nothing"
        try:
            w.append_intent("g-other", "inv-other")
            raise AssertionError("poisoned writer must refuse every later append")
        except ChainIntegrityError:
            pass


ADDENDUM4_TESTS = [
    ("D-EV-28: truncate to zero poisons open writer", test_d_ev_28_truncate_to_zero),
]
