"""th13 录像（.rpy）解析骨架。

当前目标：校验 magic、读取头、定位各 Stage 压缩块并尝试 zlib 解压。
完整字段语义（键值、输入位打包、符卡/难度字段）随 Ghidra 对原版
replay 读写函数的还原逐步补全——本模块只做结构探测，不猜语义。
"""
import struct
import zlib

MAGIC = b"t13r"


def hexdump(data, limit=0x40):
    for off in range(0, min(len(data), limit), 16):
        chunk = data[off:off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        print(f"{off:08x}  {hexs:<47}  {text}")


def try_inflate(data, offset, wbits_candidates=(-15, 15, 47)):
    """在指定偏移尝试 zlib/raw 解压，返回 (consumed, decompressed) 或 None。"""
    for wbits in wbits_candidates:
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(data[offset:])
            if len(out) > 0:
                consumed = len(data) - offset - len(d.unused_data)
                return consumed, out
        except zlib.error:
            continue
    return None


def parse(path):
    data = open(path, "rb").read()
    print(f"file: {path} ({len(data)} bytes)")
    if data[:4] != MAGIC:
        raise ValueError(f"bad magic {data[:4]!r}, expected {MAGIC!r}")
    hexdump(data)
    # th10 系头部长度约 0xDA 字节；逐字节扫描找压缩流起点
    print("== 压缩块探测 ==")
    off = 4
    found = []
    while off < min(len(data), 0x400):
        r = try_inflate(data, off)
        if r and r[0] > 2:
            consumed, out = r
            print(f"  offset 0x{off:x}: +{consumed} bytes → {len(out)} bytes")
            found.append((off, consumed, out))
            off += consumed
        else:
            off += 1
    return {"data": data, "blocks": found}


if __name__ == "__main__":
    import sys

    parse(sys.argv[1] if len(sys.argv) > 1 else "games/replays/th13_udSr01.rpy")
