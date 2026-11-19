"""Replay a proposed factory acceptance scenario or compare dependency-based schedules.

Usage: python check_delivery.py replay|delay scenario.json [--links delivery-links.csv]
All input records are local. A successful simulated test is not a factory acceptance.
"""
import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys


def instant(value):
    if not isinstance(value, str):
        raise ValueError("시각은 ISO 문자열이어야 합니다")
    stamp = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if stamp.tzinfo is None:
        raise ValueError("시각에는 UTC 오프셋을 포함하세요")
    return stamp


def dec(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("음수·비유한 값을 사용할 수 없습니다")
    return number


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("0 이상의 정수가 필요합니다")
    return value


def replay(data):
    windows = {}
    by_equipment = {}
    for entry in data["lots"]:
        lot = entry["lot"]
        span = (instant(entry["start"]), instant(entry["end"]), entry["equipment"])
        if lot in windows or span[0] >= span[1]:
            raise ValueError("중복 로트 또는 유효하지 않은 시간 범위")
        windows[lot] = span
        by_equipment.setdefault(span[2], []).append((span[0], span[1], lot))
    if not windows:
        raise ValueError("시험할 로트가 없습니다")
    for periods in by_equipment.values():
        periods.sort()
        if any(first[1] > second[0] for first, second in zip(periods, periods[1:])):
            raise ValueError("하나의 설비에서 로트 시간이 겹칩니다")
    holds = []
    # Identical retransmissions count once. Conflicting payloads invalidate the endpoint.
    readings = {}
    for reading in data["readings"]:
        key = (reading["equipment"], instant(reading["at"]))
        readings.setdefault(key, set()).add(dec(reading["kwh"]))
    revisions = {}
    owners = {}
    seen = {}
    for event in data["counts"]:
        lot = event["lot"]
        if lot not in windows or type(event["approved"]) is not bool:
            raise ValueError("마감 로트가 없거나 승인 여부가 불명확합니다")
        record = event["id"]
        sequence = integer(event["revision"])
        if record in owners and owners[record] != lot:
            raise ValueError("같은 마감 ID가 서로 다른 로트를 가리킵니다")
        owners[record] = lot
        payload = integer(event["qty"])
        key = (record, sequence)
        if key in seen and seen[key] != payload:
            raise ValueError("같은 마감 ID와 개정에 상이한 수량이 있습니다")
        seen[key] = payload
        if not event["approved"]:
            holds.append(f"{record}/{sequence}: 정정 승인 대기")
            continue
        if record not in revisions or sequence > revisions[record][0]:
            revisions[record] = (sequence, payload)
    results = {}
    for lot, (start, end, equipment) in windows.items():
        qty_parts = [row[1] for rid, row in revisions.items() if owners[rid] == lot]
        if not qty_parts:
            holds.append(f"{lot}: 승인된 마감 수량 없음")
        # An interior fall indicates a meter reset even if the endpoint increased in total.
        series = sorted((stamp, values) for (device, stamp), values in readings.items()
                        if device == equipment and start <= stamp <= end)
        kwh = None
        if not series or series[0][0] != start or series[-1][0] != end:
            holds.append(f"{lot}: 경계 검침 누락")
        elif any(len(values) != 1 for _, values in series):
            holds.append(f"{lot}: 동일 시각 계기값 충돌")
        else:
            values = [next(iter(options)) for _, options in series]
            if any(later < earlier for earlier, later in zip(values, values[1:])):
                holds.append(f"{lot}: 계기 역행·교체 검토")
            else:
                kwh = values[-1] - values[0]
        results[lot] = {"qty": sum(qty_parts) if qty_parts else None, "kwh": kwh}
    attributions = {}
    for point in data.get("production_events", []):
        stamp = instant(point["at"])
        owner = next((lot for a, b, lot in by_equipment.get(point["equipment"], [])
                      if a <= stamp < b), None)
        rid = point["id"]
        if rid in attributions and attributions[rid] != owner:
            raise ValueError("같은 생산 이벤트가 다른 로트에 배정됩니다")
        if owner is None:
            holds.append(f"{rid}: 배정 가능한 생산 로트 없음")
        attributions[rid] = owner
    totals = {}
    for field in ("qty", "kwh"):
        vals = [r[field] for r in results.values()]
        totals[field] = None if any(v is None for v in vals) else sum(vals)
    return {"lots": results, "assigned_events": attributions, "totals": totals,
            "issues": sorted(set(holds))}


def delay(data):
    jobs = {}
    dependencies = {}
    for task in data["tasks"]:
        task_id = task["id"]
        if task_id in jobs:
            raise ValueError("중복된 작업 ID")
        jobs[task_id] = integer(task["duration"])
        if not isinstance(task["after"], list):
            raise ValueError("선행작업은 작업 ID 목록이어야 합니다")
        dependencies[task_id] = set(task["after"])
    if not jobs:
        raise ValueError("작업이 없습니다")
    if any(pre - jobs.keys() for pre in dependencies.values()):
        raise ValueError("선행 작업 ID가 없습니다")
    changes = data["delay"]
    if not isinstance(changes, dict) or set(changes) - jobs.keys():
        raise ValueError("지연 입력에 정의되지 않은 작업이 있습니다")
    finish = {}
    shifted = {}
    pending = list(jobs)
    while pending:
        ready = [job for job in pending if dependencies[job] <= finish.keys()]
        if not ready:
            raise ValueError("작업 간 순환 의존성이 있습니다")
        for job in ready:
            pre = dependencies[job]
            finish[job] = max((finish[p] for p in pre), default=0) + jobs[job]
            shifted[job] = max((shifted[p] for p in pre), default=0) + jobs[job] + integer(changes.get(job, 0))
            pending.remove(job)
    return {"tasks": {job: {"baseline_finish": finish[job], "delayed_finish": shifted[job],
                           "shift": shifted[job] - finish[job]} for job in jobs},
            "project_shift": max(shifted.values()) - max(finish.values())}


def linked_deliverables(result, links):
    needed = {"작업ID", "기능ID", "시험ID"}
    attached = {}
    with Path(links).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not needed <= set(reader.fieldnames or []):
            raise ValueError("delivery-links.csv에 작업ID·기능ID·시험ID 열이 필요합니다")
        for row in reader:
            job = row["작업ID"].strip()
            if job not in result["tasks"]:
                raise ValueError(f"연결표의 작업을 일정에서 찾을 수 없습니다: {job}")
            if result["tasks"][job]["shift"]:
                attached.setdefault(job, []).append({"function": row["기능ID"], "test": row["시험ID"]})
    return attached


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["replay", "delay"])
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--links", type=Path)
    args = parser.parse_args()
    if args.links and args.mode != "delay":
        parser.error("--links는 delay에서만 사용합니다")
    try:
        data = json.loads(args.scenario.read_text(encoding="utf-8-sig"))
        result = replay(data) if args.mode == "replay" else delay(data)
        if args.links:
            result["affected_deliverables"] = linked_deliverables(result, args.links)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 1 if result.get("issues") else 0
    except (KeyError, TypeError, ValueError, InvalidOperation, OSError) as error:
        print(f"검사 실패: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
