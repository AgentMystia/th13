"""回放自机模型——StageBattle.cpp 的 python 孪生（event-contract.md §7.2）。

oracle --replay 模式用它驱动原版内存：DAT_004c22c4 的 x/y（+0x5b8/+0x5bc）
与敌机 life（+0x3f4c）写入。全部运算为整数或整值/二进分数浮点，f32 语义
经 f32() 包装对齐 C++（判定边界处单舍入差消解）。

自机弹 = .sht 弹型域原版语义（replay-twin-divergence.md §3，0x446000/0x445c40
字节直读）：
  块 = 功率/100（sht+0x24 步长，0x446000:13-16）+ 聚焦×5（sht+0x20=4 再 +1）；
  出膛 = 逐记录「帧 % 间隔 == 相位」（记录 +0/+1 字节，无冷却计数）；
  位置 = 自机 + (记录 +4/+8 出膛偏移；anchor≠0 卫星锚定记录孪生口径≈自机位)；
  伤害 = 记录 +0x18（pl00a 主弹 24 / 卫星 4）。
回放口径功率恒为初值 100（0x42b990:149 st01 故事开局），拾取演化分期未接。
弹速 12 为定标值（sht 速度域 +0xc/+0x10=48/16 的运动函数表语义待 oracle 实证）。
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


def parse_sht_shots(sht_bytes):
    """0x443e40 弹型域：+2 u16 块数、+0xc8 u32 指针表（+0xf4 基准）、
    记录 0x38 步、首字节 i8<0 终止。返回块表（与 StageBattle.cpp 同式）。"""
    import struct
    blocks = struct.unpack_from("<H", sht_bytes, 2)[0]
    if blocks == 0 or blocks > 11:
        raise ValueError("sht block count out of range: %d" % blocks)
    table = []
    for block in range(blocks):
        offset = struct.unpack_from("<I", sht_bytes, 0xC8 + block * 4)[0]
        cursor = 0xF4 + offset
        records = []
        while True:
            at = cursor + len(records) * 0x38
            if at >= len(sht_bytes):
                raise ValueError("sht record out of file")
            if struct.unpack_from("<b", sht_bytes, at)[0] < 0:
                break
            if at + 0x38 > len(sht_bytes):
                raise ValueError("sht record body out of file")
            x, y = struct.unpack_from("<2f", sht_bytes, at + 4)
            angle, damage = struct.unpack_from("<2f", sht_bytes, at + 0x14)
            anchor = struct.unpack_from("<b", sht_bytes, at + 0x20)[0]
            records.append({
                "interval": sht_bytes[at],
                "phase": sht_bytes[at + 1],
                "script": struct.unpack_from("<H", sht_bytes, at + 2)[0],
                "x": x, "y": y, "angle": angle, "damage": damage,
                "anchor": anchor,
            })
        table.append(records)
    return table


class PlayerModel:
    """定点运动 + .sht 弹型域垂直弹模型（原版语义，与 StageBattle.cpp 孪生）。
    移动常量对齐 StageBattle.hpp；弹型/伤害/间隔/相位全部出自 .sht 记录。"""

    # 无 .sht 回退口径（与 StageBattle 回退路径同式；回放对拍必有 .sht）。
    FIRE_INTERVAL = 4
    SHOT_DAMAGE = 12.0
    SHOT_SPEED = 12.0
    SHOT_HALF = 4.0

    def __init__(self, sht_bytes):
        """sht_bytes：plXXa.sht 原始文件；+0x10..+0x1c 四浮点 ×128 取整（移速），
        +0xc8 弹型域指针表（出膛）。"""
        import struct
        if len(sht_bytes) < 0x20:
            raise ValueError("sht too small")
        fields = struct.unpack_from("<4f", sht_bytes, 0x10)
        self.speed_orth = int(f32(fields[0] * 128.0))
        self.speed_orth_focus = int(f32(fields[1] * 128.0))
        self.speed_diag = int(f32(fields[2] * 128.0))
        self.speed_diag_focus = int(f32(fields[3] * 128.0))
        self.blocks = parse_sht_shots(sht_bytes)
        self.power = 100      # 0x42b990:149 st01 故事模式初值（内部 0..400）
        self.power_step = 100  # sht+0x24（0x441dd0:52 运行时写入）
        self.pos_fx = 0
        self.pos_fy = 51200  # (0, 400)
        self.x = 0.0
        self.y = 400.0
        self.focus = False
        self.shots = []  # (x, y, damage) 活弹（垂直直进，每发自带伤害）

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

    def step_fire(self, held, frame):
        """发射与步进（fire → step 顺序与 StageBattle::step 一致）。

        frame：对局帧计数（1 基，自对局首帧）——0x446000 的
        「帧 % 间隔 == 相位」判决源（玩家 ctx+0x146cc 同源口径）。"""
        if held & 0x01:
            block = self.power // self.power_step
            block = 0 if block < 0 else (4 if block > 4 else block)
            if self.focus:
                block += 5
            if block < len(self.blocks):
                for rec in self.blocks[block]:
                    interval = rec["interval"]
                    if interval <= 0:
                        continue
                    if frame % interval != rec["phase"]:
                        continue
                    self.shots.append(
                        (f32(self.x + rec["x"]), f32(self.y + rec["y"]),
                         rec["damage"]))
        keep = []
        for shot in self.shots:
            y = f32(shot[1] - self.SHOT_SPEED)
            if y >= -32.0 and -224.0 <= shot[0] <= 224.0:
                keep.append((shot[0], y, shot[2]))
        self.shots = keep

    def on_kill(self):
        """击杀反馈：功率 +8 封顶 400（StageBattle::reward_kill 同式——原版
        道具自动拾取的近似口径；块 = power/100 随之演进）。"""
        self.power = min(self.power + 8, 400)

    @staticmethod
    def shot_damage(shot):
        """每发伤害（.sht 记录 +0x18；apply_shots 侧消费）。"""
        return shot[2]

    @staticmethod
    def shot_hits(shot, ex, ey, hw, hh):
        """0x446870 AABB：|dx| ≤ hw/2+4 且 |dy| ≤ hh/2+4（含边界，f32 单舍入）。"""
        dx = f32(shot[0] - ex)
        dy = f32(shot[1] - ey)
        rx = f32(f32(hw * 0.5) + PlayerModel.SHOT_HALF)
        ry = f32(f32(hh * 0.5) + PlayerModel.SHOT_HALF)
        return not (dx < -rx or dx > rx or dy < -ry or dy > ry)
