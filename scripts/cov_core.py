import json
import sys
from pathlib import Path

with Path(sys.argv[1]).open() as fh:
    cov = json.load(fh)
CORE = (
    "/domain/",
    "/ingestion/",
    "/detection/",
    "/metrics/",
    "/reliability/",
    "/incidents/",
    "/infrastructure/storage/",
    "/infrastructure/security/",
    "/contracts/",
    "/application/",
    "/workspace",
)
stmts = miss = br = brp = 0
low = []
for f, d in cov["files"].items():
    norm = f.replace("\\", "/")
    if any(p in norm for p in CORE):
        s = d["summary"]
        stmts += s["num_statements"]
        miss += s["missing_lines"]
        br += s.get("num_branches", 0)
        brp += s.get("missing_branches", 0)
        if s["percent_covered"] < 90:
            low.append((s["percent_covered"], f))
for p, f in sorted(low):
    print(f"{p:5.1f}%  {f}")
den = stmts + br
pct = 100.0 * (den - miss - brp) / den if den else 100.0
print(f"CORE: stmts={stmts} miss={miss} branches={br} missing_br={brp} -> {pct:.2f}%")
