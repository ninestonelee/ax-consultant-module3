"""Check reported metric rows against their raw column or period-specific definition."""
import argparse
import csv
import json
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path


def compact(value):
    return ''.join(unicodedata.normalize('NFC', value).split())


def numeric(value):
    result = Decimal(value.strip().replace(',', ''))
    if not result.is_finite():
        raise ValueError('non-finite metric value')
    return result


def source_path(root, name):
    p = (root / name).resolve()
    if not name or not p.is_relative_to(root) or not p.is_file():
        raise ValueError('invalid source file')
    return p


def verify_line(root, row):
    period, name = row['period'].strip(), row['name'].strip()
    value, kind = numeric(row['value']), row['kind'].strip()
    if not period or not name:
        raise ValueError('missing period or metric name')
    src = source_path(root, row['file'])
    if kind == 'raw':
        col = row['value_column']
        head = re.sub(r'_(건|명|원|개|회|천원|백만원|%)$', '', col)
        if compact(name) not in (compact(col), compact(head)):
            raise ValueError('reported raw metric differs from source column')
        with src.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if not {col, row['period_column']} <= set(reader.fieldnames or []):
                raise ValueError('source column missing')
            hits = [r[col] for r in reader if r[row['period_column']].strip() == period]
        if len(hits) != 1 or numeric(hits[0]) != value:
            raise ValueError('source value or period is ambiguous or differs')
        return
    if kind not in ('defined', 'target'):
        raise ValueError('unknown metric kind')
    source = unicodedata.normalize('NFC', src.read_text(encoding='utf-8-sig'))
    quote, rule = (unicodedata.normalize('NFC', row[k].strip()) for k in ('quote', 'rule'))
    if not quote or not rule or quote not in source or rule not in source:
        raise ValueError('definition or metric quotation missing from source')
    if re.search(r'[.!?。](?:\s+|$).+|[\r\n]', quote.rstrip('.!?。')):
        raise ValueError('use one sentence for the dated metric claim')
    if not re.search(r'(?<![0-9A-Za-z])' + re.escape(period) + r'(?![0-9A-Za-z])', quote):
        raise ValueError('period outside quoted claim')
    if compact(name) not in compact(quote):
        raise ValueError('metric not named in quoted claim')
    amounts = re.findall(r'(?<![\dA-Za-z+\-])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', quote)
    if value not in {numeric(token) for token in amounts}:
        raise ValueError('value absent from quoted claim')


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('case', type=Path)
    args.add_argument('register', type=Path)
    opts = args.parse_args()
    errors, checked = [], 0
    required = set('period name value kind file period_column value_column quote rule'.split())
    try:
        with opts.register.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if not required <= set(reader.fieldnames or []):
                raise ValueError('missing register columns')
            for i, row in enumerate(reader, 2):
                checked += 1
                try:
                    if any(row[k] is None for k in required):
                        raise ValueError('incomplete row')
                    verify_line(opts.case.resolve(), row)
                except (ValueError, InvalidOperation, OSError) as exc:
                    errors.append(f'row {i}: {exc}')
            if not checked:
                raise ValueError('empty register')
    except (OSError, ValueError, csv.Error) as exc:
        errors.append(str(exc))
    print(json.dumps(dict(valid=not errors, rows=checked, errors=errors), ensure_ascii=False))
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
