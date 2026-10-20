#!/bin/bash
# 清理 thtk 临时克隆与构建目录
set -euo pipefail
cd /work
rm -rf tools/thtk-tmp
echo "thtk 临时目录已清理。"
