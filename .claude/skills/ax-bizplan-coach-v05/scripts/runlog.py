"""Sequential, content-addressed snapshots for one local coaching run."""
import argparse
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STEPS = {
    "full": ["draft1", "review1", "draft2", "review2", "draft3", "final"],
    "simple": ["draft1", "review1", "draft2", "final"],
    "comparison": ["calculations", "draft", "review", "final"],
}

COMPARISON_FILES = {
    "calculations": {"checks.json"},
    "draft": {"draft.md", "decisions.csv"},
    "review": {"review.md"},
    "final": {"final.md", "final.docx"},
}

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

def write_record(root, name, files, meta):
    target = root / name
    if target.exists():
        raise ValueError(f"Already recorded: {name}")
    work = Path(tempfile.mkdtemp(prefix=".pending-", dir=root))
    try:
        copied = {}
        for file, destination in files:
            if destination in copied or destination == "record.json":
                raise ValueError(f"Duplicate or reserved filename: {destination}")
            path = work / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, path)
            copied[destination] = digest(path)
        for item in meta.get("allowed_inputs", []):
            item["sha256"] = copied[item["file"]]
        meta.update(files=copied, time=datetime.now(timezone.utc).isoformat())
        (work / "record.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        work.rename(target)
    finally:
        if work.exists():
            shutil.rmtree(work)

def history(root):
    previous = None
    records = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(".pending-"):
            continue
        record = folder / "record.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        if records:
            steps = STEPS[records[0]["depth"]]
            if len(records) > len(steps):
                raise ValueError(f"Unexpected snapshot: {folder.name}")
            stage = steps[len(records) - 1]
            expected_name = f"{len(records):02}_{stage}"
            if data.get("stage") != stage:
                raise ValueError(f"Wrong recorded stage: {folder.name}")
            if records[0]["depth"] == "comparison":
                missing = COMPARISON_FILES[stage] - data["files"].keys()
                if missing:
                    raise ValueError(f"Missing snapshot files: {sorted(missing)}")
        else:
            expected_name = "00_init"
        if folder.name != expected_name or data["previous"] != previous:
            raise ValueError(f"Invalid sequence at {folder.name}")
        mode = records[0]["depth"] if records else data["depth"]
        if mode == "comparison":
            actual = {str(path.relative_to(folder)) for path in folder.rglob("*")
                      if path.is_file() and path.name != ".DS_Store"}
            if actual != set(data["files"]) | {"record.json"}:
                raise ValueError(f"Snapshot inventory changed: {folder.name}")
        for file, checksum in data["files"].items():
            if Path(file).is_absolute() or ".." in Path(file).parts:
                raise ValueError(f"Invalid snapshot filename: {file}")
            if digest(folder / file) != checksum:
                raise ValueError(f"Changed snapshot: {folder.name}/{file}")
        if not records and data["depth"] == "comparison":
            allowed = data["allowed_inputs"]
            if not allowed or len({item["id"] for item in allowed}) != len(allowed):
                raise ValueError("Invalid input IDs")
            if {data["prompt_file"], *(item["file"] for item in allowed)} != set(data["files"]):
                raise ValueError("Allowlist and recorded inputs disagree")
            for item in allowed:
                if data["files"][item["file"]] != item["sha256"]:
                    raise ValueError(f"Allowlist hash mismatch: {item['id']}")
        previous = digest(record)
        records.append(data)
    return records, previous

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("out", type=Path)
    init.add_argument("--prompt", type=Path, required=True)
    init.add_argument("--model", required=True)
    init.add_argument("--inputs", nargs="+", type=Path, required=True)
    init.add_argument("--input-ids", nargs="+", help="자료별 ID, --inputs와 같은 순서")
    init.add_argument("--depth", choices=STEPS, required=True)
    init.add_argument("--purpose", choices=["education","submission"], required=True)
    add = sub.add_parser("add")
    add.add_argument("out", type=Path); add.add_argument("stage")
    add.add_argument("files", type=Path, nargs="+")
    verify = sub.add_parser("verify"); verify.add_argument("out", type=Path)
    a = parser.parse_args()
    root = a.out / "audit"
    if a.cmd == "init":
        root.mkdir(parents=True, exist_ok=True)
        if list(root.iterdir()):
            parser.error("Choose a fresh run directory, or verify the existing run.")
        skill = Path(__file__).resolve().parents[1]
        hashes = {str(p.relative_to(skill)): digest(p) for p in sorted(skill.rglob("*"))
                  if p.is_file() and "__pycache__" not in p.parts and not any(x.startswith(".") for x in p.relative_to(skill).parts)}
        files = [(a.prompt, a.prompt.name)]
        meta = dict(previous=None, depth=a.depth, purpose=a.purpose, model=a.model,
                    skill_version="0.5", skill_files=hashes, prompt_file=a.prompt.name)
        if a.depth == "comparison" and (not a.input_ids or a.purpose != "submission"):
            parser.error("근거 비교 실행에는 --input-ids와 --purpose submission이 필요합니다.")
        if a.depth == "comparison" and not a.prompt.read_text(encoding="utf-8-sig").strip():
            parser.error("실제 실행 지시를 기록한 prompt 파일이 비어 있습니다.")
        if a.input_ids:
            if len(a.input_ids) != len(a.inputs) or len(set(a.input_ids)) != len(a.inputs):
                parser.error("입력마다 중복되지 않는 ID 하나를 같은 순서로 지정하세요.")
            meta["allowed_inputs"] = []
            for source_id, path in zip(a.input_ids, a.inputs):
                if not re.fullmatch(r"[\w-]+", source_id):
                    parser.error("자료 ID에는 글자, 숫자, 밑줄과 붙임표를 쓸 수 있습니다.")
                destination = f"inputs/{source_id}/{path.name}"
                files.append((path, destination))
                meta["allowed_inputs"].append(dict(id=source_id, file=destination))
        else:
            files.extend((path, path.name) for path in a.inputs)
        write_record(root, "00_init", files, meta)
    else:
        records, prev = history(root)
        if not records:
            parser.error("Initialize before adding stages.")
        steps=STEPS[records[0]["depth"]]
        idx=len(records)-1
        if a.cmd == "verify":
            print(f"{len(records)} verified records; next: {steps[idx] if idx < len(steps) else 'complete'}")
            return
        if idx >= len(steps) or steps[idx] != a.stage:
            parser.error("Stage out of sequence or run complete")
        if records[0]["depth"] == "comparison":
            missing = COMPARISON_FILES[a.stage] - {path.name for path in a.files}
            if missing:
                parser.error(f"근거 비교 단계의 필수 산출물이 없습니다: {sorted(missing)}")
            if any(path.stat().st_size == 0 for path in a.files if path.name in COMPARISON_FILES[a.stage]):
                parser.error("필수 산출물의 내용이 비어 있습니다.")
        write_record(root, f"{len(records):02}_{a.stage}",
                     [(path, path.name) for path in a.files], dict(previous=prev, stage=a.stage))
if __name__ == "__main__":
    main()
