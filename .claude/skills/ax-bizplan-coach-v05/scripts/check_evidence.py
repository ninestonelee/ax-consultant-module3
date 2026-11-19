#!/usr/bin/env python3
"""Check a case's source and decision registers. Only the Python standard library is used."""

import argparse
import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import sys


SOURCE_FIELDS = "evidence_id kind path sha256 locator issuer".split()
CLAIM_FIELDS = "claim_id decision status scope statement required_evidence_ids evidence_ids approver".split()
SIMULATION = re.compile(r"합성|가상|모의|역할극|synthetic|simulat(?:ion|ed)|fictional|role.play", re.I)
SOURCE_LABEL = re.compile(
    r"교육용\s*(?:합성|가상|모의|역할극)|(?:합성|가상|모의|역할극)\s*(?:입력|자료|원장|집계|사례|면담|인터뷰|회의록|증빙)"
    r"|(?:출처|구분|자료|입력|kind|source)\s*[:=]\s*(?:합성|가상|모의|역할극|synthetic)"
    r"|\b(?:synthetic|fictional|simulated)[ _-]+(?:data|input|record|example|case|document|source)", re.I)
UNCERTAIN = re.compile(
    r"미(?:수령|승인|확정|확인|서명|날인|등록|배치|보유|측정)|무서명|보류|요청|예정|필요|계획|가정|가안|잠정|제안|검토|확인\s*전"
    r"|(?:하지|되지|받지|있지)\s*(?:않|못)|이?\s*아니[다며라었]|부재|없[는음다어]|보완|대기|반려|거절|조건부|여부"
    r"|\b(?:not|pending|unsigned|unconfirmed|proposed|rejected|draft)\b", re.I)
REAL = re.compile(r"실제|실존|정식|공식|실명|\b(?:actual|real.world)\b", re.I)
FACT_TOPICS = {
    "approval": re.compile(r"승인|날인|서명|결재|확약|견적(?:서|액|가|금액)?\s*(?:이|을|은|를)?\s*확정|"
                           r"확정\s*견적|\b(?:signed|approved|binding quote)\b", re.I),
    "identity": re.compile(r"참석|배석|배치|선임|임명|고용|재직|등록\s*(?:공급|업체)|(?:공급\s*(?:기[업관]|사|업체)|사업자)(?:가|로|는|의)?\s*등록|"
                           r"대표자|실명|\b(?:attended|employed|appointed|registered supplier)\b", re.I),
    "ownership": re.compile(r"소유|귀속|권리자|소유권|저작권|(?:설비|자산|장비|라이선스|소프트웨어)\s*(?:를|을)?\s*보유|"
                            r"\b(?:owns?|owned|ownership|rights holder)\b", re.I),
}
AFFIRMED = re.compile(
    r"완료|확정|확인됨|체결|등재|등록됨|날인본|서명본|(?:했|하였|됐|되었|받았|맡았)"
    r"|(?:승인|서명|날인|참석|등록|배치|보유|귀속|소유|확약|고용)(?:한다|함|됨|되다|된다|이다|된\s|한\s)"
    r"|(?:참석자|소유자|권리자|대표자)\s*[:：]|(?:결재|판정|상태|결과)\s*[:：]\s*승인"
    r"|\b(?:approved|signed|owns?|attended|employed|appointed)\b", re.I)
APPROVAL_FACT = re.compile(
    r"(?:승인|서명|날인|결재|확약)(?:을|를|이|가)?\s*(?:하였|했|함|한다|완료|확정|되었|됐|됨|받았|받음|합니다)"
    r"|(?:견적(?:서|액|가|금액)?\s*(?:이|을|은|를)?\s*확정|확정\s*견적)"
    r"|(?:결재|판정|상태|결과)\s*[:：]\s*승인|\b(?:approved|signed)\b", re.I)


class ContractError(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise ContractError(message)


def local_path(root, name):
    path = Path(name)
    check(name and not path.is_absolute(), f"사례 내부 상대경로 필요 ({name!r})")
    target = (root / path).resolve(strict=True)
    check(target.is_relative_to(root) and target.is_file(), f"사례 내부 파일 필요 ({name})")
    return target


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_register(data, fields, name):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    header = reader.fieldnames or []
    check(len(set(header)) == len(header), f"{name} 중복 헤더")
    check(set(fields) <= set(header), f"{name} 누락 열 {sorted(set(fields) - set(header))}")
    rows = {}
    for row in reader:
        at = f"{name} {reader.line_num}행"
        check(None not in row and None not in row.values(), f"{at} 열 개수 불일치")
        row = {key: value.strip() for key, value in row.items()}
        key = row[fields[0]]
        check(key and not any(ch.isspace() or ch == "|" for ch in key), f"{at} ID 형식 오류")
        check(key not in rows, f"{at} 중복 ID {key}")
        rows[key] = row
    check(rows, f"{name} 데이터 행 없음")
    return rows


def excerpt(source, locator, key):
    if locator.startswith("text:"):
        quote = locator[5:]
        check(quote.strip() and source.count(quote) == 1, f"{key} 인용문이 원문 한 곳에 있어야 함")
        start = source.index(quote)
        end = start + len(quote)
        # Preserve the whole physical line(s) so text: cannot cut a negation off a quoted fact.
        left = max(source.rfind("\n", 0, start), source.rfind("\r", 0, start)) + 1
        right = re.search(r"[\r\n]", source[end:])
        return quote, source[left:end + right.start() if right else len(source)]
    lines = source.splitlines()
    point = re.fullmatch(r"line:(\d+)", locator)
    span = re.fullmatch(r"lines:(\d+)-(\d+)", locator)
    check(point or span, f"{key} 위치는 line:N, lines:A-B 또는 text:원문")
    start, end = (int(point[1]), int(point[1])) if point else (int(span[1]), int(span[2]))
    check(1 <= start <= end <= len(lines), f"{key} 원본 범위를 벗어난 위치 {locator}")
    result = "\n".join(lines[start - 1:end])
    check(result.strip(), f"{key} 비어 있는 인용 범위")
    return result, result


@dataclass(frozen=True)
class Source:
    kind: str
    quote: str
    context: str


def read_sources(root, manifest, ledgers):
    sources, files, kinds_by_hash = {}, {}, {}
    for key, row in manifest.items():
        kind = row["kind"]
        check(kind in ("actual", "synthetic", "missing"), f"{key} kind 오류")
        check(row["issuer"], f"{key} 출처/요청처 누락")
        if kind == "missing":
            check(not any(row[c] for c in ("path", "sha256", "locator")), f"{key} 미수령 자료에 파일·지문·인용 위치 있음")
            sources[key] = Source(kind, "", "")
            continue
        path = local_path(root, row["path"])
        check(path not in ledgers, f"{key} 대장 자체를 원본으로 인용")
        if path not in files:
            data = path.read_bytes()
            files[path] = (digest(data), data.decode("utf-8-sig"))
        actual_hash, content = files[path]
        check(row["sha256"].lower() == actual_hash, f"{key} 원본 SHA-256 불일치")
        check(kinds_by_hash.setdefault(actual_hash, kind) == kind, f"{key} 같은 원본의 합성/실제 구분 충돌")
        if kind == "actual":
            # Data labels in the title, issuer or initial records also cover uncited header rows.
            first_lines = "\n".join(content.splitlines()[:5])
            check(not SOURCE_LABEL.search(" ".join((row["issuer"], path.stem, first_lines))),
                  f"{key} 합성 표시가 있는 원본을 actual로 등록")
        quote, context = excerpt(content, row["locator"], key)
        sources[key] = Source(kind, quote, context)
    return sources


def id_list(row, field, sources):
    ids = [s.strip() for s in row[field].split("|")] if row[field] else []
    key = row["claim_id"]
    check(len(set(ids)) == len(ids), f"{key} {field} 안에 중복 ID")
    for source_id in ids:
        check(source_id in sources, f"{key} {field} 미등록 근거 ID {source_id!r}")
    return ids


def normalized(value):
    return " ".join(value.split()).strip(' .。“”"')


def quoted_fact(clause, quote):
    # An embedded phrase in e.g. “대표가 승인했다는 주장은 사실이 아니다” is not an affirmative statement.
    for sentence in re.split(r"(?<=[.!?。])\s+|[;；\n]", quote):
        if not UNCERTAIN.search(sentence) and normalized(clause) == normalized(sentence):
            return True
    return False


def check_sensitive(row, citations):
    """Guard high-impact affirmative facts even when the author labels them 'misc'."""
    key, decision, statement = row["claim_id"], row["decision"], row["statement"]
    for clause in re.split(r"(?<=[.!?。])\s+|[;；\n]", statement):
        clause = clause.strip()
        if not clause:
            continue
        if row["scope"] == "simulation" and REAL.search(clause) and not UNCERTAIN.search(clause):
            raise ContractError(f"{key} 모의 판단에서 실제 사실 주장")
        topics = {topic for topic, pattern in FACT_TOPICS.items() if pattern.search(clause)}
        if row["status"] == "confirmed" and decision in FACT_TOPICS:
            topics.add(decision)
        if not topics:
            continue
        if UNCERTAIN.search(clause):
            check(row["status"] == "hold" or decision not in FACT_TOPICS,
                  f"{key} 미확정·조건부 사실을 확정 상태로 기록")
            continue
        affirmative = (AFFIRMED.search(clause) or REAL.search(clause) or decision in topics
                       or (row["status"] == "confirmed" and (row["scope"] == "actual" or "approval" in topics)))
        if not affirmative:
            continue
        check(row["status"] == "confirmed", f"{key} 보류문 안에 {','.join(sorted(topics))} 확정 사실 주장")
        if row["scope"] == "simulation":
            if "approval" not in topics:
                continue
            check(SIMULATION.search(clause), f"{key} 모의 승인임을 문장에 명시")
        else:
            check(not SIMULATION.search(clause), f"{key} 합성 사실을 실제로 확정")
        # For these specific facts a short verbatim sentence binds the actor, object and action.
        matches = [source for source in citations if normalized(clause) in normalized(source.quote)
                   and quoted_fact(clause, source.context)]
        check(matches and all(FACT_TOPICS[t].search(clause) for t in topics),
              f"{key} 확인하려는 사실 문장을 인용 위치의 원문에서 찾을 수 없음")
        check(AFFIRMED.search(clause), f"{key} 확인할 사실의 명시적인 긍정 문장 누락")
        if "approval" in topics:
            check(APPROVAL_FACT.search(clause), f"{key} 명시적인 승인·서명·확정 사실 누락")
            approver = row["approver"]
            check(approver and any(approver in s.quote for s in matches), f"{key} 승인 주체가 같은 인용 범위에 없음")


def validate(case_dir, manifest_name="manifest.csv", claims_name="claims.csv"):
    root = Path(case_dir).resolve(strict=True)
    check(root.is_dir(), "사례 디렉터리 필요")
    manifest_path, claims_path = (local_path(root, name) for name in (manifest_name, claims_name))
    check(manifest_path != claims_path, "자료 대장과 결정 대장이 같은 파일")
    manifest_data, claims_data = manifest_path.read_bytes(), claims_path.read_bytes()
    manifest = read_register(manifest_data, SOURCE_FIELDS, manifest_name)
    sources = read_sources(root, manifest, {manifest_path, claims_path})
    claims = read_register(claims_data, CLAIM_FIELDS, claims_name)
    check(not sources.keys() & claims.keys(), "자료·결정 대장 간 중복 ID")
    pinned = {key: set() for key in claims}
    for source_id, source_row in manifest.items():
        if source_row.get("required_for"):
            for target in source_row["required_for"].split("|"):
                key = target.strip()
                check(key in pinned, f"{source_id} required_for 미등록 결정 ID {key!r}")
                check(source_id not in pinned[key], f"{source_id} required_for 중복 결정 ID {key}")
                pinned[key].add(source_id)
    held = []
    for key, row in claims.items():
        status, scope = row["status"], row["scope"]
        check(status in ("confirmed", "hold"), f"{key} status 오류")
        check(scope in ("actual", "simulation"), f"{key} scope 오류")
        check(row["decision"] and row["statement"], f"{key} 판단 주제/문장 누락")
        needed = id_list(row, "required_evidence_ids", sources)
        cited = id_list(row, "evidence_ids", sources)
        check(needed, f"{key} 필요한 입력 ID 누락")
        check(pinned[key] <= set(needed), f"{key} 자료 대장에서 지정한 필수 입력 ID 누락")
        usable = {i for i in cited if sources[i].kind == "actual" or
                  (scope == "simulation" and sources[i].kind == "synthetic")}
        pending = [i for i in needed if i not in usable]
        if status == "confirmed":
            check(not pending, f"{key} 필수 입력 미충족 {pending}")
            check(usable == set(cited), f"{key} 미수령/합성 자료의 확인 등급 승격")
        else:
            held.append(dict(claim_id=key, reason=row["statement"], pending_evidence_ids=pending))
        check_sensitive(row, [sources[i] for i in cited if i in usable])
    return dict(valid=True, sources=len(sources), confirmed=len(claims) - len(held), hold=len(held), held=held,
                manifest_sha256=digest(manifest_data), claims_sha256=digest(claims_data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir")
    parser.add_argument("--manifest", default="manifest.csv", help="사례 기준 자료 대장 경로")
    parser.add_argument("--claims", default="claims.csv", help="사례 기준 결정 대장 경로")
    args = parser.parse_args()
    try:
        result = validate(args.case_dir, args.manifest, args.claims)
    except (ValueError, OSError, RuntimeError, csv.Error) as exc:
        print(json.dumps(dict(valid=False, error=str(exc)), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
