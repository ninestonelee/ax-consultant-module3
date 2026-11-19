"""Render shared values from a sourced dictionary; fail on unresolved references."""
import argparse
import json
import re
from pathlib import Path

TOKEN = re.compile(r"\{\{([a-zA-Z][a-zA-Z0-9_.-]*)\}\}")
FIELDS = ("value", "status", "source", "scope", "period", "owner")
def render(template, data, appendix=True):
    used = []
    def insert(match):
        key = match[1]
        item = data.get(key)
        if not isinstance(item, dict) or any(
            item.get(field) is None or not str(item[field]).strip() for field in FIELDS
        ):
            raise ValueError(f"Missing value or provenance: {key}")
        if item["status"] == "충돌":
            raise ValueError(f"Resolve the conflicting source values first: {key}")
        if key not in used:
            used.append(key)
        return str(item["value"])
    out = TOKEN.sub(insert, template)
    if "{{" in out or "}}" in out:
        raise ValueError("Malformed value token")
    if appendix and used:
        out += "\n\n## 공통 수치와 근거\n\n| ID | 표시값 | 상태 | 출처·위치 | 경계·단위 | 기간 | 담당 |\n|---|---|---|---|---|---|---|\n"
        for key in used:
            out += "| " + " | ".join([key]+[str(data[key][f]).replace("|","／").replace("\n"," ") for f in FIELDS]) + " |\n"
    return out
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("template",type=Path);p.add_argument("dictionary",type=Path);p.add_argument("output",type=Path)
    p.add_argument("--no-appendix",action="store_true")
    a=p.parse_args()
    result=render(a.template.read_text(encoding="utf-8"),json.loads(a.dictionary.read_text(encoding="utf-8")),not a.no_appendix)
    a.output.write_text(result,encoding="utf-8")
