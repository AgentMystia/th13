#!/bin/bash
# thtk 提取 th13.dat：raw 解包 + 文本 dump（ECL/STD/MSG）+ ANM 清单
# 产物落 artifacts/reference/（工作区，不入库）；仅格式结论写入 ports/th13/docs/
set -euo pipefail
cd /work
TK=/work/tools/thtk-tmp/build
RAW=artifacts/reference/raw
DUMP=artifacts/reference/dumps
mkdir -p "$RAW" "$DUMP"

echo "== thdat 解包 th13.dat =="
(cd "$RAW" && "$TK/thdat/thdat" -x 13 /work/games/th13/th13.dat)

echo "== 文件清单 =="
find "$RAW" -type f | sed "s|$RAW/||" | sort > artifacts/reference/inventory.txt
wc -l < artifacts/reference/inventory.txt
du -sh "$RAW"

echo "== ECL dump =="
mkdir -p "$DUMP/ecl"
find "$RAW" -iname '*.ecl' | while read -r f; do
    "$TK/thecl/thecl" -d13 -j "$f" "$DUMP/ecl/$(basename "${f%.ecl}").txt" && echo "ok $(basename "$f")"
done

echo "== STD dump =="
mkdir -p "$DUMP/std"
find "$RAW" -iname '*.std' | while read -r f; do
    "$TK/thstd/thstd" -d13 "$f" "$DUMP/std/$(basename "${f%.std}").txt" && echo "ok $(basename "$f")"
done

echo "== MSG dump =="
mkdir -p "$DUMP/msg"
find "$RAW" -iname '*.msg' | while read -r f; do
    "$TK/thmsg/thmsg" -d13 "$f" "$DUMP/msg/$(basename "${f%.msg}").txt" && echo "ok $(basename "$f")"
done

echo "== ANM 清单 =="
find "$RAW" -iname '*.anm' | while read -r f; do
    echo "--- $(basename "$f") ---" >> "$DUMP/anm-list.txt"
    "$TK/thanm/thanm" -l13 "$f" >> "$DUMP/anm-list.txt" 2>&1 || echo "(list failed)" >> "$DUMP/anm-list.txt"
done
echo "完成：$(find "$DUMP" -type f | wc -l) 个 dump 文件"
