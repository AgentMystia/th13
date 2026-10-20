#!/bin/bash
# 临时克隆并编译 thtk（thpatch/thtk）到 tools/thtk-tmp/
# 约定：thtk 阶段完成后执行 scripts/thtk-clean.sh 清理
set -euo pipefail
cd /work
mkdir -p tools/thtk-tmp
if [ ! -d tools/thtk-tmp/thtk/.git ]; then
    git clone --depth 1 https://github.com/thpatch/thtk tools/thtk-tmp/thtk
fi
cmake -S tools/thtk-tmp/thtk -B tools/thtk-tmp/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build tools/thtk-tmp/build
echo "== thtk 工具就绪 =="
ls -1 tools/thtk-tmp/build/
tools/thtk-tmp/build/thdat --help 2>&1 | head -3 || true
