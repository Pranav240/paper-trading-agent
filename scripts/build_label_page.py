r"""
Write eval/label.html: the phase 09 labelling page with the test set
embedded, stripped of anything that says how each headline was retrieved
(rank and method stay only in eval/golden_days.json), so labels are blind.

Run after build_golden_days.py:
    .\papertrading\Scripts\python.exe scripts/build_label_page.py
r"""

import json
from pathlib import Path

golden = json.loads(Path("eval/golden_days.json").read_text(encoding="utf-8"))
blind = {
    "built": golden["built"],
    "days": [
        {
            "date": d["date"],
            "return": d["return"],
            "candidates": [
                {"id": c["id"], "published_at": c["published_at"], "headline": c["headline"]}
                for c in d["candidates"]
            ],
        }
        for d in golden["days"]
    ],
}
template = Path("eval/label_template.html").read_text(encoding="utf-8")
assert "/*DATA*/null" in template
data = json.dumps(blind, ensure_ascii=False).replace("</", r"<\/")
Path("eval/label.html").write_text(template.replace("/*DATA*/null", data), encoding="utf-8")
print(f"wrote eval/label.html: {len(blind['days'])} days, "
      f"{sum(len(d['candidates']) for d in blind['days'])} headlines (method and rank removed)")
