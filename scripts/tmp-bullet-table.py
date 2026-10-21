# 临时提取：th13.exe .data 0x4bbe30 起的弹幕外观表（0x114/项）→ C++ 初始化器行。
# 仅开发期使用；产物写入 /tmp/bullet-table.txt。
import struct

data = open("games/th13/th13.exe", "rb").read()
pe = struct.unpack_from("<I", data, 0x3C)[0]
nsec = struct.unpack_from("<H", data, pe + 6)[0]
opt = struct.unpack_from("<H", data, pe + 20)[0]
image_base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
secs = []
off = pe + 24 + opt
for _ in range(nsec):
    vsize, va, rsize, ro = struct.unpack_from("<IIII", data, off + 8)
    secs.append((va, vsize, ro, rsize))
    off += 40


def va2off(va):
    rva = va - image_base
    for sva, vsz, ro, rsz in secs:
        if sva <= rva < sva + max(vsz, rsz):
            return ro + (rva - sva)
    return None


base = 0x4BBE30
o = va2off(base)
assert o is not None
with open("artifacts/reference/bullet-table.txt", "w") as out:
    for t in range(64):
        e = o + t * 0x114
        v = struct.unpack_from("<69i", data, e)
        script = v[0]
        colors = [tuple(v[1 + c * 4:5 + c * 4]) for c in range(16)]
        scale = struct.unpack_from("<f", data, e + 0x104)[0]
        hit_kind, cancel_kind, spawn = v[0x108 // 4], v[0x10C // 4], v[0x110 // 4]
        out.write("%d %r %d %d %d %s\n" % (script, scale, hit_kind, cancel_kind, spawn, colors))
print("ok")
