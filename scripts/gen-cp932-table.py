#!/usr/bin/env python3
# 生成 web/cpp/game/MsgPlayerCp932.hpp：cp932 双字节 → Unicode 平面表。
# 表数据由本脚本从 Python 标准编码 cp932（Microsoft 行为，含 NEC/IBM 扩展）
# 枚举生成——不是游戏资产，属公开字符集标准的派生数据。
# 重新生成：python3 scripts/gen-cp932-table.py > eagler-touhou/ports/th13/web/cpp/game/MsgPlayerCp932.hpp
ROWS = list(range(0x81, 0xA0)) + list(range(0xE0, 0xF0))  # 0x81..0x9F + 0xE0..0xEF
COLS = 192  # trail 0x40..0xFF（含空洞位，置 0 = 无效）


def main() -> None:
    out = []
    out.append("// cp932 双字节→Unicode 查找表（scripts/gen-cp932-table.py 生成，勿手改）。")
    out.append("// 行 = 首字节 0x81..0x9F、0xE0..0xEF；列 = 次字节 - 0x40（0x00..0xBF）；")
    out.append("// 0 = 无效组合。半角片假名 0xA1..0xDF 与 ASCII 由 MsgPlayer 算法处理。")
    out.append("#pragma once")
    out.append("#include \"Types.hpp\"")
    out.append("")
    out.append("namespace th13 {")
    out.append("")
    out.append("constexpr u16 kCp932LeadFirst = 0x81;")
    out.append("constexpr u16 kCp932LeadRows = %d;" % len(ROWS))
    out.append("constexpr u16 kCp932TrailBase = 0x40;")
    out.append("constexpr u16 kCp932TrailCols = %d;" % COLS)
    out.append("// 行序 = 首字节序（0x81..0x9F 后接 0xE0..0xEF）；kCp932Row[lead] 给行号。")
    out.append("constexpr u8 kCp932Row[0x70] = {")
    row_of = {}
    for index, lead in enumerate(ROWS):
        row_of[lead] = index
    flat = []
    for lead in range(0x81, 0xF1):
        flat.append(row_of.get(lead, 0xFF))
    for chunk in range(0, len(flat), 24):
        out.append("    " + ",".join("0x%02x" % v for v in flat[chunk:chunk + 24]) + ",")
    out.append("};")
    out.append("")
    out.append("constexpr u16 kCp932ToUnicode[%d][%d] = {" % (len(ROWS), COLS))
    for lead in ROWS:
        row = []
        for trail in range(0x40, 0x40 + COLS):
            try:
                text = bytes([lead, trail]).decode("cp932")
                row.append(ord(text) if len(text) == 1 else 0)
            except UnicodeDecodeError:
                row.append(0)
        cells = []
        for value in row:
            cells.append("0x%04x" % value if value else "0")
        out.append("    {%s}," % ",".join(cells))
    out.append("};")
    out.append("")
    out.append("}  // namespace th13")
    print("\n".join(out))


if __name__ == "__main__":
    main()
