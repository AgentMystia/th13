#!/bin/bash
# 解包原版 th13ogg.7z → games/th13/，生成 SHA256 清单与版本判定
set -euo pipefail
cd /work
mkdir -p games/th13 artifacts

echo "== 解包 =="
7z x -y -ogames/th13 th13ogg.7z

echo "== SHA256 清单 =="
find games/th13 -type f -print0 | sort -z | xargs -0 sha256sum > artifacts/games-sha256.txt
cat artifacts/games-sha256.txt

echo "== 关键文件检查 =="
for f in th13.exe th13.dat thbgm.dat; do
    if [ -f "games/th13/$f" ]; then echo "OK  $f  $(stat -c%s games/th13/$f) bytes"; else echo "MISS $f"; fi
done

echo "== EXE 版本判定 =="
EXE=$(find games/th13 -maxdepth 2 -iname 'th13.exe' | head -1 || true)
if [ -n "$EXE" ]; then
    file "$EXE"
    # PE 时间戳
    python3 - "$EXE" <<'EOF'
import struct, sys, datetime
data = open(sys.argv[1], 'rb').read(0x400)
pe = struct.unpack_from('<I', data, 0x3C)[0]
ts = struct.unpack_from('<I', data, pe + 8)[0]
print('PE timestamp:', ts, datetime.datetime.utcfromtimestamp(ts).isoformat() + 'Z')
EOF
    # 版本字符串（资源段内 "v1.00x"）
    strings -e l "$EXE" 2>/dev/null | grep -i -m5 'th13\|v1\.0' || strings "$EXE" | grep -i -m5 'v1\.0' || true
else
    echo "!! 未找到 th13.exe"
    find games/th13 -maxdepth 2 | head -40
fi
