#!/bin/bash
# Ghidra headless 分析 th13.exe 并导出反编译候选
# 用法: scripts/decomp-analyze.sh [--refine]
set -euo pipefail
cd /work
mkdir -p artifacts/decomp/jp
export MAXMEM="${MAXMEM:-6G}"

PROJ_DIR=artifacts/decomp/project
if [ -d "$PROJ_DIR/th13.rep" ] || [ -f "$PROJ_DIR/th13.gpr" ]; then
    MODE=(-process th13.exe -noanalysis)
    echo "== 复用已有 Ghidra 工程 =="
else
    MODE=(-import games/th13/th13.exe)
    echo "== 导入并全量分析 =="
fi

if [ "${1:-}" = "--refine" ]; then
    POST=RefineTh13.java
else
    POST=ExportTh13.java
fi

analyzeHeadless "$PROJ_DIR" th13 \
    "${MODE[@]}" \
    -scriptPath decomp \
    -postScript "$POST" artifacts/decomp/jp \
    2>&1 | tee artifacts/decomp/analysis.log | grep -E 'INFO  REPORT|exporting|functions:|decompiling|strings:|external symbols|export complete|refine complete|ERROR' || true

echo "== 导出产物 =="
ls artifacts/decomp/jp/ | head
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path('artifacts/decomp/jp/functions.json')
if p.exists():
    d = json.loads(p.read_text())['functions']
    ok = [f for f in d if f['decompiled']]
    art = [f for f in d if f.get('decompilerArtifact')]
    print(f"函数总数 {len(d)}，反编译成功 {len(ok)}，含 artifact {len(art)}")
EOF
