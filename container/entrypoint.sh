#!/bin/bash
# 容器入口：环境摘要 + 缓存落盘 + 执行用户命令
set -e
# emscripten 端口缓存（SDL3 等）落到工作区卷，避免每次容器运行重复下载
if [ -w /work ]; then
    export EM_CACHE_DIR="${EM_CACHE_DIR:-/work/.cache/emscripten}"
    mkdir -p "$EM_CACHE_DIR"
fi
if [ "$1" = "bash" ] || [ -z "$1" ]; then
    echo "== th13 神灵庙移植开发环境 =="
    echo "node    $(node --version 2>/dev/null || echo n/a)"
    echo "python  $(python3 --version 2>/dev/null || echo n/a)"
    echo "emcc    $(emcc --version 2>/dev/null | head -1 || echo n/a)"
    echo "wasi    ${WASI_SDK_PATH:-n/a}"
    echo "ghidra  $( [ -d /opt/ghidra ] && echo /opt/ghidra || echo n/a )"
    echo "workdir /work"
fi
exec "$@"
