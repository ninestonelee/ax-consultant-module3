#!/usr/bin/env python3
"""Validate a closed-lot manufacturing ledger against a case's explicit assumptions."""
import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from html import escape
import io
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile


class CaseError(ValueError):
    pass


def required(condition, message):
    if not condition:
        raise CaseError(message)


def label(value, field):
    required(isinstance(value, str) and value.strip(), f"{field}: 비어 있는 이름/근거")
    return value.strip()


def number(value, field, *, limit=None, integer=False, optional=False):
    if optional and value is None:
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise CaseError(f"{field}: 숫자가 아님 ({value!r})") from None
    required(result.is_finite() and result >= 0, f"{field}: 음수/비유한값")
    if limit is not None:
        required(result <= limit, f"{field}: {limit} 초과")
    if integer:
        required(result == result.to_integral_value(), f"{field}: 정수 필요")
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_ledger(data, spec):
    cols = spec["columns"]
    required(isinstance(cols, dict), "columns: 객체 필요")
    for key in ("lot", "period", "units", "defects", "kwh"):
        label(cols.get(key), f"columns.{key}")
    required(len(set(cols.values())) == len(cols), "한 열이 두 역할에 연결됨")
    required(("power_kw" in cols) == ("hours" in cols), "정격·시간은 쌍으로 입력")
    energy = spec["energy"]
    required(energy["kind"] in ("estimated", "measured"), "energy.kind: estimated/measured")
    label(energy["source"], "energy.source")
    label(energy["boundary"], "energy.boundary")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    header = reader.fieldnames or []
    required(len(header) == len(set(header)), "중복 CSV 헤더")
    required(set(cols.values()) <= set(header), "연결한 CSV 열이 없음")
    rows = []
    ids = set()
    for line in reader:
        at = f"CSV {reader.line_num}행"
        required(None not in line and None not in line.values(), at + ": 열 수 불일치")
        record = {name: line[header_name].strip() for name, header_name in cols.items()}
        lot = label(record["lot"], at + " LOT")
        required(lot not in ids, at + ": 중복 LOT " + lot)
        ids.add(lot)
        period = label(record["period"], at + " 기간")
        units = number(record["units"], at + " 생산", integer=True)
        defects = number(record["defects"], at + " 불량", integer=True)
        kwh = number(record["kwh"], at + " 전력")
        required(defects <= units, at + ": 불량수가 생산수보다 큼")
        if "energy_boundary" in cols:
            required(record["energy_boundary"] == energy["boundary"], at + ": 계측 경계 혼재")
        if "energy_kind" in cols:
            required(record["energy_kind"] == energy["kind"], at + ": 실측/추정 혼재")
        row = dict(row=reader.line_num, lot=lot, period=period, units=units, defects=defects, kwh=kwh)
        if "power_kw" in cols and energy["kind"] == "estimated":
            power = number(record["power_kw"], at + " 정격")
            hours = number(record["hours"], at + " 등가가동시간")
            required(kwh == power * hours, at + ": 추정전력과 정격×시간이 불일치")
            row.update(power_kw=power, hours=hours)
        rows.append(row)
    required(rows and sum(row["units"] for row in rows) > 0, "전체 생산 분모가 0이거나 원장이 비어 있음")
    return rows


def totals(rows):
    sums = {key: sum(row[key] for row in rows) for key in ("units", "defects", "kwh")}
    units = sums["units"]
    return {**sums, "defect_rate": sums["defects"] / units if units else None,
            "energy_intensity": sums["kwh"] / units if units else None}


def choose_target(name, spec, boundary, diagnostics):
    candidates = spec.get("targets", {}).get(name, [])
    required(isinstance(candidates, list), name + ": 후보 배열 필요")
    entries = []
    for item in candidates:
        entries.append({
            "source": label(item["source"], name + ".source"),
            "boundary": label(item["boundary"], name + ".boundary"),
            "value": number(item["value"], name, limit=1 if name == "defect_rate" else None)
        })
    distinct = {(item["value"], item["boundary"]) for item in entries}
    selected = entries[0] if len(distinct) == 1 else None
    status = "candidate" if selected else ("conflict" if entries else "missing")
    approval = spec.get("approvals", {}).get(name)
    if approval is not None:
        for key in ("source", "by", "date", "basis"):
            label(approval.get(key), "approval." + key)
        date.fromisoformat(approval["date"])
        matches = [item for item in entries if item["source"] == approval["source"]]
        required(len(matches) == 1, name + ": 승인 대상 출처가 없거나 중복")
        selected, status = matches[0], "approved"
    if selected and selected["boundary"] != boundary:
        selected, status = None, "boundary_mismatch"
    if selected is None:
        diagnostics.append(name + ": " + status)
    return dict(status=status, value=selected["value"] if selected else None,
                source=selected["source"] if selected else None, candidates=entries,
                conflicting_candidates=len(distinct) > 1, approval=approval)


def assess_energy(energy, base, target, units_per_period, diagnostics):
    reduction = (base - target) * units_per_period if target is not None else None
    result = {"required_reduction_kwh_per_period": reduction,
              "reduction_fraction": (base-target) / base if target is not None and base else None,
              "recoverable_kwh_per_period": None, "status": "conditional"}
    action = energy.get("action") or {}
    fields = ("idle_kw", "idle_hours_per_period", "recoverable_fraction")
    if all(action.get(f) is not None for f in fields) and action.get("measured_source"):
        if action.get("boundary") == energy["boundary"]:
            recoverable = (number(action["idle_kw"], fields[0])
                           * number(action["idle_hours_per_period"], fields[1])
                           * number(action["recoverable_fraction"], fields[2], limit=1))
            result.update(recoverable_kwh_per_period=recoverable, action_source=action["measured_source"])
            if reduction is not None and reduction >= 0:
                if recoverable < reduction:
                    result["status"] = "insufficient_measured_loss"
                elif energy["kind"] == "measured":
                    result["status"] = "arithmetically_supported_plan"
        else:
            diagnostics.append("감축 조치와 기준선의 설비 경계 불일치")
    if energy["kind"] == "estimated":
        diagnostics.append("전력 기준선 추정: 개선 전 같은 회로의 실측 원장 필요")
    if result["status"] != "arithmetically_supported_plan":
        diagnostics.append("에너지 목표의 조치별 회피 가능량/근거 미충족")
    return result


def budget(spec, diagnostics):
    funding = spec.get("funding") or {}
    parts = {k: number(funding.get(k), "funding."+k, optional=True)
             for k in ("total_won", "public_won", "cash_won", "in_kind_won")}
    complete = all(v is not None for v in parts.values())
    balanced = sum(parts[k] for k in ("public_won", "cash_won", "in_kind_won")) == parts["total_won"] if complete else None
    if balanced is not True:
        diagnostics.append("재원 합계 불일치 또는 미확정")
    expenses = spec.get("expenditure") or {}
    tax_basis = expenses.get("tax_basis", "net")
    required(tax_basis in ("net", "gross"), "expenditure.tax_basis: net/gross")
    ids = set()
    lines = []
    for item in expenses.get("lines", []):
        ident = label(item["id"], "소요비 ID")
        required(ident not in ids, "소요비 ID 중복: "+ident)
        ids.add(ident)
        output = dict(id=ident, kind=item["kind"], module=label(item["module"], ident+" module"),
                      source=item.get("source"), evidence_kind=item.get("evidence_kind", "unknown"))
        vat = number(item.get("vat_won"), ident+" 세액", optional=True)
        if item["kind"] == "purchase":
            ref = label(item["qty_ref"], ident+" qty_ref")
            required(ref in expenses.get("quantities", {}), ident+": 미등록 수량 ID "+ref)
            qty = number(expenses["quantities"][ref], ident+" 수량", optional=True)
            unit = number(item.get("unit_won"), ident+" 단가", optional=True)
            factors = [qty, unit]
            output.update(qty_ref=ref, quantity=qty, unit_won=unit)
        else:
            required(item["kind"] == "labor", ident+": 지원하지 않는 kind")
            factors = [number(item.get(key), ident+" "+key, optional=True, limit=1 if key=="fraction" else None)
                       for key in ("monthly_won", "months", "fraction")]
            output.update(zip(("monthly_won", "months", "fraction"), factors))
        net = Decimal(1)
        for factor in factors:
            if factor is None:
                net = None
                break
            net *= factor
        gross = net+vat if net is not None and vat is not None else None
        source_ready = bool(output["source"] and output["evidence_kind"] == ("quote" if item["kind"] == "purchase" else "payroll"))
        output.update(net_won=net, vat_won=vat, gross_won=gross, named_basis=source_ready)
        lines.append(output)
    basis = tax_basis+"_won"
    known = [r[basis] for r in lines if r[basis] is not None]
    expenditure_total = sum(known) if lines and len(known)==len(lines) else None
    reconciled = expenditure_total==parts["total_won"] if expenditure_total is not None and parts["total_won"] is not None else None
    if reconciled is not True:
        diagnostics.append("소요비와 재원 합계 대사 미충족")
    if not lines or any(not r["named_basis"] for r in lines):
        diagnostics.append("제출용 견적·인력 근거가 없는 소요비 존재")
    return dict(funding={**parts,"source": funding.get("source"),"balanced":balanced}, tax_basis=tax_basis,
                lines=lines, known_subtotal_won=sum(known) if known else None,
                expenditure_total_won=expenditure_total, matches_funding=reconciled)


def scenarios(config, reduction_defects, reduction_energy, diagnostics):
    rates = config.get("realization", [1])
    required(isinstance(rates, list) and rates, "realization: 비어 있지 않은 배열 필요")
    defect_price = number(config.get("defect_won"), "benefits.defect_won", optional=True)
    energy_price = number(config.get("kwh_won"), "benefits.kwh_won", optional=True)
    opex = number(config.get("added_opex_per_period_won"), "benefits.opex", optional=True)
    separate = config.get("excludes_energy")
    required(separate is None or type(separate) is bool, "excludes_energy: true, false, null 중 선택")
    if separate is not True:
        diagnostics.append("회피비용의 전력비 제외가 미확인/중복되어 합산 보류")
    if opex is None:
        diagnostics.append("추가 운영비가 없어 순편익 미확정")
    result = []
    for v in rates:
        rate = number(v, "realization", limit=1)
        q = reduction_defects*defect_price*rate if reduction_defects is not None and defect_price is not None else None
        e = reduction_energy*energy_price*rate if reduction_energy is not None and energy_price is not None else None
        gross = q+e if q is not None and e is not None and separate is True else None
        net = gross-opex if gross is not None and opex is not None else None
        result.append(dict(realization=rate, avoided_quality_won=q, avoided_energy_won=e,
                           combined_gross_won=gross, net_after_added_opex_won=net))
    return {"source":config.get("source"),"basis":"목표 실현율별 기간당 조건부 시나리오","scenarios":result}


def evaluate(spec, raw):
    source = label(spec["source"], "source")
    quality = label(spec["quality_boundary"], "quality_boundary")
    rows = read_ledger(raw, spec)
    diagnostics = []
    by_period = {}
    for row in rows:
        by_period.setdefault(row["period"], []).append(row)
    sums = totals(rows)
    monthly_units = sums["units"] / len(by_period)
    boundaries = {"defect_rate": quality, "energy_intensity": spec["energy"]["boundary"]}
    targets = {k:choose_target(k,spec,b,diagnostics) for k,b in boundaries.items()}
    produced = [r for r in rows if r["units"] > 0]
    positives = sum(r["defects"] > 0 for r in produced)
    negatives = len(produced)-positives
    if not positives or not negatives:
        diagnostics.append("후보 LOT 정답이 단일 클래스: 해당 정의로 현장 분류 능력 판정 불가")
    diagnostics.append("집계 원장만으로 시점별 특징·라벨·시간 분리 시험 및 실제 F1을 확인할 수 없음")
    energy = assess_energy(spec["energy"], sums["energy_intensity"],
                           targets["energy_intensity"]["value"], monthly_units, diagnostics)
    target_rate = targets["defect_rate"]["value"]
    defects_saved = (sums["defect_rate"]-target_rate)*monthly_units if target_rate is not None else None
    if (defects_saved is not None and defects_saved < 0) or (energy["required_reduction_kwh_per_period"] is not None and energy["required_reduction_kwh_per_period"] < 0):
        diagnostics.append("감소 목표가 기준보다 높음: 목표 또는 산식 검토")
    money = budget(spec, diagnostics)
    benefits = scenarios(spec.get("benefits") or {}, defects_saved,
                         energy["required_reduction_kwh_per_period"], diagnostics)
    return {
        "schema_version":"0.3","source":source,
        "trace":{"csv_sha256":digest(raw),"source_rows":rows},
        "aggregates":{"all":sums,"periods":{p:totals(rr) for p,rr in by_period.items()},
                      "period_unit":spec.get("period_unit","입력 기간"),"mean_units_per_period":monthly_units},
        "metrics":{
            "defect_rate":{"value":sums["defect_rate"],"unit":"개/개","formula":"sum(defects)/sum(units)",
                           "boundary":quality,"source":source,"kind":"calculated","rows":[r["row"] for r in rows]},
            "energy_intensity":{"value":sums["energy_intensity"],"unit":"kWh/개","formula":"sum(kwh)/sum(units)",
                                "boundary":spec["energy"]["boundary"],"source":spec["energy"]["source"],
                                "kind":spec["energy"]["kind"],"rows":[r["row"] for r in rows]}},
        "targets":targets,
        "label_diagnostic":{"candidate_unit":"closed LOT","candidate_label":"최초 불량수가 1개 이상",
                            "positive_lots":positives,"negative_lots":negatives,
                            "excluded_zero_production_lots":len(rows)-len(produced),
                            "always_alert_f1_on_input":Decimal(2*positives)/(2*positives+negatives),
                            "verified_model_f1":None},
        "energy_feasibility":energy,"budget":money,"benefits":benefits,"diagnostics":diagnostics,
    }


def json_number(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value).__name__)


def render_html(result):
    def esc(value):
        return escape(str(value), quote=True)
    if "input_errors" in result:
        body = "<h1>원장 입력 오류</h1>" + "".join("<p>"+esc(e)+"</p>" for e in result["input_errors"])
    else:
        links, evidence = [], []
        for key, metric in result["metrics"].items():
            is_rate = key == "defect_rate"
            value = (f"{100*metric['value']:.2f}%" if is_rate else f"{metric['value']:.4f} kWh/개")
            name = "공정 불량률" if is_rate else "전력 원단위"
            links.append(f'<li><a href="#{key}">{name} {esc(value)}</a> · {esc(metric["kind"])}</li>')
            target = result["targets"][key]
            candidates = []
            for index, candidate in enumerate(target["candidates"], 1):
                anchor = f"{key}-target-{index}"
                goal = candidate["value"]
                display = f"{100*goal:.2f}%" if is_rate else f"{goal:.3f} kWh/개"
                links.append(f'<li><a href="#{anchor}">{name} 목표 후보 {display}</a></li>')
                candidates.append(f'<li id="{anchor}">{esc(goal)} · {esc(candidate["source"])} · {esc(candidate["boundary"])}</li>')
            evidence.append(f'<section id="{key}"><h2>{name}</h2><p>{esc(metric["formula"])} · {esc(metric["boundary"])} · {esc(metric["source"])}</p>'
                            f'<p>기준 원장 <a href="#rows">CSV {len(metric["rows"])}개 행</a></p><p>목표 후보 · {esc(target["status"])}</p><ul>{"".join(candidates)}</ul></section>')
        columns = ["row","lot","period","units","defects","kwh"]
        headers = ["CSV 행","LOT","기간","생산 개수","최초 불량","kWh"]
        if "power_kw" in result["trace"]["source_rows"][0]:
            columns.extend(["power_kw","hours"])
            headers.extend(["합산 정격 kW","등가가동 h"])
            evidence.append("<p>추정 전력량은 각 행의 합산 정격(kW) × 등가가동시간(h)이다. 원단위는 이 전력량의 합계를 같은 기간 생산수량의 합계로 나눈 값이다.</p>")
        trs = "".join("<tr>"+"".join("<td>"+esc(r[k])+"</td>" for k in columns)+"</tr>" for r in result["trace"]["source_rows"])
        warnings = "".join("<li>"+esc(v)+"</li>" for v in result["diagnostics"])
        body = ("<h1>지표와 원장 근거</h1><ul>"+"".join(links)+"</ul>"+"".join(evidence)
                +'<section id="rows"><h2>원장 행 대조</h2><table><thead><tr>'+"".join(f"<th>{h}</th>" for h in headers)+'</tr></thead><tbody>'
                +trs+"</tbody></table></section><h2>미확정 항목</h2><ul>"+warnings+"</ul>")
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>지표 검산 근거</title><style>body{font:16px/1.65 system-ui,sans-serif;margin:2rem auto;max-width:58rem;padding:0 1rem;color:#163345}table{border-collapse:collapse}td,th{border:1px solid #afc6c9;padding:.35rem .6rem}section{overflow:auto}a{color:#075c79}</style>'+body+'</html>')


def atomic_text(path, data):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(data)
    Path(temp.name).replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--html", type=Path, help="offline evidence drill-down")
    args = parser.parse_args()
    paths = [p.resolve() for p in (args.out, args.csv, args.spec, args.html) if p is not None]
    if len(set(paths)) != len(paths):
        parser.error("inputs and outputs must have distinct paths")
    try:
        definition = args.spec.read_bytes()
        spec = json.loads(definition.decode("utf-8-sig"), parse_float=Decimal)
        result = evaluate(spec, args.csv.read_bytes())
        result["trace"]["spec_sha256"] = digest(definition)
        code = 0
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, csv.Error) as e:
        result = {"schema_version":"0.3","input_errors":[str(e)]}
        code = 2
    payload = json.dumps(result,ensure_ascii=False,indent=2,default=json_number,allow_nan=False)+"\n"
    atomic_text(args.out, payload)
    if args.html:
        atomic_text(args.html, render_html(result))
    if code:
        print(payload,file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
