#!/bin/bash
# 清点 th13ogg.7z 内容（只读，不解包）
set -euo pipefail
cd /work
mkdir -p artifacts
7z l th13ogg.7z | tee artifacts/inventory.txt
echo "---- 顶层条目 ----"
7z l -slt th13ogg.7z 2>/dev/null | grep '^Path = ' | sed 's/^Path = //' | cut -d/ -f1 | sort -u | tee artifacts/inventory-top.txt
