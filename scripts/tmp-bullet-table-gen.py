# 由 artifacts/reference/bullet-table.txt（th13.exe .data 0x4bbe30 提取）生成
# BulletAppearance.cpp 的初始化器行。仅开发期使用。
import re

lines = open("artifacts/reference/bullet-table.txt").read().splitlines()
rows = []
for line in lines:
    head, _, colors = line.partition("[")
    colors = colors.rstrip()[:-1]  # drop trailing ']'
    script, scale, hit, cancel, spawn = head.split()
    assert re.fullmatch(r"-?\d+", script)
    quads = []
    for quad in re.findall(r"\(([^)]*)\)", colors):
        a, b, c, d = (x.strip() for x in quad.split(","))
        for x in (a, b, c, d):
            assert re.fullmatch(r"-?\d+", x), quad
        quads.append("{%s,%s,%s,%s}" % (a, b, c, d))
    assert len(quads) == 16
    rows.append(
        "{%s,%sf,{{%s}},%s,%s,%s}" % (script, scale, ",".join(quads), hit, cancel, spawn)
    )
    if len(rows) == 38:
        break
with open("artifacts/reference/bullet-table-gen.txt", "w") as out:
    for row in rows:
        out.write(row + "\n")
print(len(rows))
