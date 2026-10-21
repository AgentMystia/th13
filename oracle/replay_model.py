"""回放自机模型——StageBattle.cpp 的 python 孪生（event-contract.md §7.2）。

oracle --replay 模式用它驱动原版内存：DAT_004c22c4 的 x/y（+0x5b8/+0x5bc）
与敌机 life（+0x3f4c）写入。全部运算为整数或整值/二进分数浮点，f32 语义
经 f32() 包装对齐 C++（判定边界处单舍入差消解）。
"""


def f32(x):
    """按 IEEE binary32 舍入（对齐 C++ float 运算的单舍入语义）。"""
    import struct
    return struct.unpack("<f", struct.pack("<f", x))[0]


# 0x4abc30 方向表（§7.2；斜向不归一）。
DIR_X = (0, 0, 0, -1, 1, -1, 1, -1, 1)
DIR_Y = (0, -1, 1, 0, 0, -1, -1, 1, 1)


def dir_index(held):
    """0x442960:55-78 的方向判定序。"""
    if (held & 0x50) == 0x50:
        return 5
    if (held & 0x60) == 0x60:
        return 7
    if (held & 0x90) == 0x90:
        return 6
    if (held & 0xA0) == 0xA0:
        return 8
    if (held & 0x20) == 0:
        if (held & 0x10) == 0:
            if (held & 0x40) == 0:
                return 4 if (held & 0x80) else 0
            return 3
        return 1
    return 2


class PlayerModel:
    """定点运动 + 垂直弹模型。常量对齐 StageBattle.hpp（§7.2【待校准】项）。"""

    FIRE_INTERVAL = 4
    SHOT_DAMAGE = 12.0
    SHOT_SPEED = 12.0
    SHOT_HALF = 4.0
    SHOT_OFFSET_X = 8.0
    SHOT_OFFSET_Y = 12.0

    def __init__(self, sht_bytes):
        """sht_bytes：plXXa.sht 原始文件；+0x10..+0x1c 四浮点 ×128 取整。"""
        import struct
        if len(sht_bytes) < 0x20:
            raise ValueError("sht too small")
        fields = struct.unpack_from("<4f", sht_bytes, 0x10)
        self.speed_orth = int(f32(fields[0] * 128.0))
        self.speed_orth_focus = int(f32(fields[1] * 128.0))
        self.speed_diag = int(f32(fields[2] * 128.0))
        self.speed_diag_focus = int(f32(fields[3] * 128.0))
        self.pos_fx = 0
        self.pos_fy = 51200  # (0, 400)
        self.x = 0.0
        self.y = 400.0
        self.focus = False
        self.cooldown = 0
        self.shots = []  # (x, y) 活弹（垂直直进）

    def step_move(self, held):
        """移动（0x442960:223-236 定点累加 + 钳制）；返回 (x, y) f32。"""
        self.focus = (held & 0x08) != 0
        d = dir_index(held)
        if d == 0:
            speed = 0
        elif self.focus:
            speed = self.speed_orth_focus if d < 5 else self.speed_diag_focus
        else:
            speed = self.speed_orth if d < 5 else self.speed_diag
        self.pos_fx += speed * DIR_X[d]
        self.pos_fy += speed * DIR_Y[d]
        if self.pos_fx < -0x5C00:
            self.pos_fx = -0x5C00
        elif self.pos_fx > 0x5C00:
            self.pos_fx = 0x5C00
        if self.pos_fy < 0x1000:
            self.pos_fy = 0x1000
        elif self.pos_fy > 0xD800:
            self.pos_fy = 0xD800
        self.x = f32(self.pos_fx * 0.0078125)
        self.y = f32(self.pos_fy * 0.0078125)
        return self.x, self.y

    def step_fire(self, held):
        """发射与步进（fire → step 顺序与 StageBattle::step 一致）。"""
        if held & 0x01:
            if self.cooldown > 0:
                self.cooldown -= 1
            else:
                self.cooldown = self.FIRE_INTERVAL
                for i in (-1, 0, 1):
                    self.shots.append(
                        (f32(self.x + f32(self.SHOT_OFFSET_X * i)),
                         f32(self.y - self.SHOT_OFFSET_Y)))
        elif self.cooldown > 0:
            self.cooldown -= 1
        keep = []
        for x, y in self.shots:
            y = f32(y - self.SHOT_SPEED)
            if y >= -32.0 and -224.0 <= x <= 224.0:
                keep.append((x, y))
        self.shots = keep

    @staticmethod
    def shot_hits(shot, ex, ey, hw, hh):
        """0x446870 AABB：|dx| ≤ hw/2+4 且 |dy| ≤ hh/2+4（含边界，f32 单舍入）。"""
        dx = f32(shot[0] - ex)
        dy = f32(shot[1] - ey)
        rx = f32(f32(hw * 0.5) + PlayerModel.SHOT_HALF)
        ry = f32(f32(hh * 0.5) + PlayerModel.SHOT_HALF)
        return not (dx < -rx or dx > rx or dy < -ry or dy > ry)
