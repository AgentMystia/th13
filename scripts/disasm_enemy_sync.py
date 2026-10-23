#!/usr/bin/env python3
# 0x41a8a0 ANM 槽位同步趟的指令级反汇编（capstone；容器内运行）。
# 用法：python3 scripts/disasm_enemy_sync.py [起始VA] [长度]
import struct, sys
import capstone

data = open('games/th13/th13.exe', 'rb').read()
pe = struct.unpack_from('<I', data, 0x3c)[0]
imgbase = struct.unpack_from('<I', data, pe + 24 + 28)[0]
nsec = struct.unpack_from('<H', data, pe + 6)[0]
opt = struct.unpack_from('<H', data, pe + 20)[0]
secs = []
for i in range(nsec):
    off = pe + 24 + opt + i * 40
    name = data[off:off + 8].rstrip(b'\0').decode()
    vsize, va, rsize, roff = struct.unpack_from('<IIII', data, off + 8)
    secs.append((name, va, vsize, roff, rsize))

def va2off(va):
    rva = va - imgbase
    for _, sva, vsize, roff, rsize in secs:
        if sva <= rva < sva + max(vsize, rsize):
            return roff + (rva - sva)
    return None

start = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x41a8a0
length = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x1c0
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
code = data[va2off(start):va2off(start) + length]
for ins in md.disasm(code, start):
    print(f"{ins.address:x}  {ins.mnemonic} {ins.op_str}")
