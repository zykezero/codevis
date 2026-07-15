"""Run both front-ends, emit the shared index, and score link quality.

    python run_spike.py
"""
from __future__ import annotations

import json
from pathlib import Path

import frontend_python
import frontend_r

HERE = Path(__file__).parent
OUT = HERE / "index_out"


def report(name, idx):
    s = idx.stats()
    by = s["by_target"]
    total = s["references"]
    code_refs = total - by.get("data", 0)          # NSE column names are not code
    resolvable = by.get("project", 0) + by.get("local", 0) + by.get("external", 0)
    print(f"\n=== {name} ({idx.language}) ===")
    print(f"  symbols      {s['symbols']}")
    print(f"  references   {total}")
    print(f"  edges        {s['edges']}")
    for k in ("project", "local", "external", "data", "unresolved"):
        if k in by:
            print(f"    {k:<12} {by[k]:>4}  ({by[k]/total:>5.1%})")
    print(f"  RESOLUTION RATE (of code refs): {resolvable/code_refs:.1%}")
    return s


def main():
    OUT.mkdir(exist_ok=True)
    py = frontend_python.build_index(HERE.parent / "fixtures" / "python_demo")
    r = frontend_r.build_index(HERE.parent / "fixtures" / "r_demo")

    for name, idx in (("python_demo", py), ("r_demo", r)):
        report(name, idx)
        (OUT / f"{name}.index.json").write_text(json.dumps(idx.to_dict(), indent=2))

    # The cross-language claim: identical downstream consumption.
    print("\n=== D12 check: one consumer, two languages ===")
    for label, idx in (("python", py), ("r", r)):
        fan = sorted({e.from_symbol.split("::")[0] for e in idx.edges
                      if e.to_symbol.endswith("log_step")})
        print(f"  {label:<7} log_step is called from {len(fan)} files: {', '.join(fan)}")
    print(f"\n  wrote {OUT}/python_demo.index.json and r_demo.index.json")


if __name__ == "__main__":
    main()
