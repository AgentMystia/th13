"""opcode→处理块地址解析 + 小区间反汇编（容器内跑）。

用法：
  python3 scripts/op_probe.py ops 511 512 513 ...   # 解析跳表
  python3 scripts/op_probe.py dis <start> <end>     # 反汇编
"""
import struct
import sys

from capstone import Cs, CS_ARCH_X86, CS_MODE_32

DATA = open("games/th13/th13.exe", "rb").read()
PE_OFF = struct.unpack_from("<I", DATA, 0x3C)[0]
NSEC = struct.unpack_from("<H", DATA, PE_OFF + 6)[0]
OPT = struct.unpack_from("<H", DATA, PE_OFF + 20)[0]
IMGBASE = struct.unpack_from("<I", DATA, PE_OFF + 24 + 28)[0]
SECTIONS = []
for i in range(NSEC):
    s = PE_OFF + 24 + OPT + i * 40
    name = DATA[s:s + 8].rstrip(b"\0").decode()
    vsz, va, rsz, ro = struct.unpack_from("<IIII", DATA, s + 8)
    SECTIONS.append((name, va, vsz, ro, rsz))
BYTE_TABLE = 0x41F9D8
JUMP_TABLE = 0x41F760
MD = Cs(CS_ARCH_X86, CS_MODE_32)


def v2o(va):
    rva = va - IMGBASE
    for _, sva, vsz, ro, rsz in SECTIONS:
        if sva <= rva < sva + max(vsz, rsz):
            return ro + (rva - sva)
    return None


def r32(va):
    return struct.unpack_from("<I", DATA, v2o(va))[0]


def r8(va):
    return DATA[v2o(va)]


def handler(op):
    dense = r8(BYTE_TABLE + (op - 300))
    return r32(JUMP_TABLE + dense * 4)


if __name__ == "__main__":
    if sys.argv[1] == "ops":
        for op in map(int, sys.argv[2:]):
            print("op %d: handler=0x%08x" % (op, handler(op)))
    else:
        start, end = int(sys.argv[2], 16), int(sys.argv[3], 16)
        off = v2o(start)
        for ins in MD.disasm(DATA[off:off + (end - start)], start):
            print("%08x  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
