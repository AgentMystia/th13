"""检查纠偏轮产物，不重生成、不接受golden、不隐藏分歧。容器内运行。"""
import hashlib
import json
from pathlib import Path


def describe(path):
    count = ops = 0
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for line in source:
            row = json.loads(line)
            if row['frame'] != count:
                raise ValueError('%s: non-contiguous frame %s' % (path, row['frame']))
            count += 1
            ops += len(row['ops'])
            digest.update(line)
    return dict(path=path, frames=count, ops=ops, sha256=digest.hexdigest())


def main():
    cases = {
        'normal_s1_capture': (
            'artifacts/oracle-draws/audit-normal-s1-clock-fixed.jsonl',
            'artifacts/oracle-draws/audit-normal-s1-clock-fixed-r2.jsonl', 11675),
        'normal_s1_startup': (
            'artifacts/oracle-draws/audit-normal-s1-600.jsonl',
            'artifacts/oracle-draws/audit-normal-s1-600-r2.jsonl', 600),
        'title_oracle': (
            'artifacts/oracle-draws/audit-title-fresh.jsonl',
            'artifacts/oracle-draws/audit-title-fresh-r2.jsonl', 600),
        'title_port': (
            'artifacts/port-draws/audit-title-restored.jsonl',
            'artifacts/port-draws/audit-title-restored-r2.jsonl', 600),
    }
    result = {}
    for name, (a, b, expected) in cases.items():
        first, second = describe(a), describe(b)
        if first['frames'] != expected or second['frames'] != expected:
            raise ValueError('%s: incomplete window' % name)
        if first['sha256'] != second['sha256']:
            raise ValueError('%s: nondeterministic capture' % name)
        result[name] = dict(first=first, second=second, identical=True)
    result['scope'] = 'capture-repeatability-only'
    result['replay_fidelity'] = 'unverified'
    result['stage1_clear'] = 'unverified'
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
