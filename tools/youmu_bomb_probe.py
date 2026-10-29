#!/usr/bin/env python3
"""妖梦 Bomb 原版独立取证（0x40c910 / 0x40cb50 / 0x4732c0 倒计时 / 0x4457e0 矩形池）。

模式（互斥）：
  capture  BattleOracle 全真机链路取证。--replay 给实际录像（只读，不合成、
           不写回任何头/输入字节）；缺省为合成实验（基底 th13_01.rpy 仅三处
           合成：顶层头 char/shot、stage1 头帧数/块长、stage1 输入流——与实际
           录像产物以 tag 分开）。
  --disasm capstone 反汇编 0x40cb50/0x40c910/0x4732c0/0x4016c0 实际指令 +
           .data 常量 + 运行时 spawn age 交叉核对 → 机器 JSON。
  --verify 对既有 dump 离线复核：421 条矩形中心逐位（27 条 spawn 帧 h=0 用
           post-tick 槽态）、lane y 27/27、spawn 间隔、倒计时模型、scale 生命。

产物均落 --out（默认 artifacts/youmu-lunatic-20261029/bomb/，ignored），
文件名带 tag 不互相覆盖；不读写 artifacts/tmp-scratch。

用法（容器内 /work）：
  python3 tools/youmu_bomb_probe.py [--disasm|--verify]
  python3 tools/youmu_bomb_probe.py --replay games/replays/th13_udN01.rpy \
      --stage 4 --frames 3650 [--bomb-bf 3305] [--detail-frames 300] [--out DIR]
  python3 tools/youmu_bomb_probe.py 700 140            # 合成模式（旧用法兼容）
"""
import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn import UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED  # noqa: E402
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_EIP, UC_X86_REG_ESP  # noqa: E402
from oracle.battle_oracle import BattleOracle  # noqa: E402
from oracle.replayfmt import ReplayFile        # noqa: E402

OUT_DIR = "artifacts/youmu-lunatic-20261029/bomb"
EXE = "games/th13/th13.exe"
DAT_PLAYER = 0x004C22C4
ANM_MGR = 0x004DC688
RNG_OBJ = 0x004DC324            # {u16 seed; u16 pad; u32 count}
F_40C910 = 0x0040C910
F_40CB50 = 0x0040CB50
F_40CB50_END = 0x0040CF20       # 0x40cb50 末尾（963 字节体内 ret 全归属）
F_472E10 = 0x00472E10
F_4457E0 = 0x004457E0

BOMB_PRESS_DEFAULT = 140        # 合成模式：battle frame 按下炸弹
TOTAL_DEFAULT = 1300            # 合成模式：绝对帧数

P_CURSOR = 0xD90                # param_1[0x364]
P_SCALE_CURSOR = 0xD94          # param_1[0x365]（spawn 间隔源，0x4016c0 目标）
P_INTERP_VALUE = 0xD98          # param_1[0x366]（0x4732c0: *ESI = ESI[1] 镜像）
P_GUARD = 0xD9C                 # param_1[0x367]（0x4732c0: ESI[1] = round(acc)）
P_IDS = 0x610                   # param_1[0x184 + i]
P_PARITY = 0x700                # param_1[0x1c0 + i]（±192.0f 位型）
P_YS = 0x7F0                    # param_1[0x1fc + i]
P_SCALE = 0x8E8                 # param_1[0x23a + 5i] 5 浮点族
P_TIMER_A = 0x18                # param_1[6]
P_TIMER_B = 0x1C                # param_1[7]
P_BOMB_ANM = 0x4C
P_RECT_COUNT = 0xA9E0
P_RECT_POOL = 0xA9E4            # stride 0x9c, 256 槽


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def rd_f32(uc, addr):
    return struct.unpack("<f", bytes(uc.mem_read(addr, 4)))[0]


def rd_u32(uc, addr):
    return struct.unpack("<I", bytes(uc.mem_read(addr, 4)))[0]


def vm_of(mgr, anm_id):
    """0x46fb90 小 id 分支：vm = mgr + (id&0x1fff)*0x5bc + 0xdc。"""
    return mgr + (anm_id & 0x1FFF) * 0x5BC + 0xDC


def in_bomb(ret):
    return F_40CB50 <= ret < F_40CB50_END


# ---------------------------------------------------------------- disasm ----

DISASM_FUNCS = [
    ("0040cb50", 0x0040CB50, 963,
     "妖梦 Bomb 每帧 tick：spawn 门/倒计时消费/60 槽循环/矩形生成"),
    ("0040c910", 0x0040C910, 571,
     "Bomb 触发：cursor=0、scale_cursor=25、0xD98=-1、0xD9C=0、本体 anm"),
    ("004732c0", 0x004732C0, 97,
     "插值 tick（ESI 对象，param_3=rate）：time_rate 条件 0.99<*ESI[3]<1.01"),
    ("004016c0", 0x004016C0, 61,
     "插值置位：*EAX=param-1、EAX[1]=param、EAX[2]=(float)param"),
]

DISASM_CALLEES = {
    0x00472E10: "signed_unit 抽随机（lane 抖动源）",
    0x004732C0: "插值 tick（倒计时/槽 scale 推进；rate=-1.0 → 逐帧 -1）",
    0x004016C0: "插值置位（0xD98 族：value=param-1/target=param/acc=float(param)）",
    0x0046F820: "ANM VM 创建（script 19/20 斩击；25 炸弹本体）",
    0x0046FB90: "按 anm id 查 VM（小 id: mgr+(id&0x1fff)*0x5bc+0xdc）",
    0x004457E0: "矩形池生成（96.0f × scale×128，life=30）",
    0x004971F0: "x87 fistp 取整（round acc → 倒计时/scale 整数化）",
    0x00460BC0: "SE/事件触发",
    0x00470F30: "任务链注册",
    0x0047A1E1: "对象分配",
    0x0045F8C0: "任务例程",
    0x004119C0: "矩形渲染登记",
    0x004300F0: "（矩形后处理）",
}

DISASM_MEM_NOTES = [
    (0x18, "param_1[6] timer_a（触发时 -1，逐帧增；spawn 门 [7]!=[6]）"),
    (0x1C, "param_1[7] timer_b（=bomb age；门 [7]<0x105）"),
    (0x4C, "param_1[0x13] 炸弹本体 anm id（age 261 由 0x46fc30 复员）"),
    (0xD90, "param_1[0x364] slash cursor"),
    (0xD94, "param_1[0x365] scale_cursor（spawn 间隔源：25→23→…→7）"),
    (0xD98, "param_1[0x366] 倒计时镜像（0x4732c0: *ESI=ESI[1]）"),
    (0xD9C, "param_1[0x367] 倒计时值（0x4732c0: ESI[1]=round(acc)；门 <1）"),
    (0xDA0, "param_1[0x368] 倒计时 acc（float，0x4732c0: += rate）"),
    (0x5B8, "player x"), (0x5BC, "player y"),
    (0xA9E0, "矩形池游标"), (0xA9E4, "矩形池首槽（stride 0x9c）"),
]


def mode_disasm(args):
    import capstone
    raw = open(EXE, "rb").read()
    from oracle.pe import PE
    pe = PE(raw)

    def read(va, n):
        off = pe.rva_to_offset(va - pe.image_base)
        return raw[off:off + n]

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    out = {"exe": EXE, "mode": "capstone-disasm",
           "globals": {
               "DAT_004c0a28_f32": struct.unpack("<f", read(0x4C0A28, 4))[0],
               "DAT_004c0a28_note": "0x4732c0 的 *ESI[3]（time_rate 常量）；∈(0.99,1.01) → acc+=rate 整数支路",
               "DAT_004bba54_lanes_f32x11": list(struct.unpack("<11f", read(0x4BBA54, 44))),
               "DAT_004ae9c0_f64": struct.unpack("<d", read(0x4AE9C0, 8))[0],
               "DAT_004ae8c8_f64": struct.unpack("<d", read(0x4AE8C8, 8))[0],
               "DAT_004aebe8_f32": struct.unpack("<f", read(0x4AEBE8, 4))[0],
               "DAT_004ae908_f32": struct.unpack("<f", read(0x4AE908, 4))[0],
               "DAT_004ae908_note": "0x40cd47 装载的 0x4732c0 第三参（倒计时 rate=-1.0）",
               "DAT_004ae9d0_f64": struct.unpack("<d", read(0x4AE9D0, 8))[0],
               "DAT_004ae9d8_f64": struct.unpack("<d", read(0x4AE9D8, 8))[0],
               "DAT_004ae9d8_note": "槽 scale 内联 time_rate 双比较 0.99/1.01（0x40cea4/0x40ceb7）",
               "DAT_004ae860_f64": struct.unpack("<d", read(0x4AE860, 8))[0],
               "DAT_004ae860_note": "time_rate∈(0.99,1.01) 整数支路的 +1.0",
               "DAT_004aefe0_f64": struct.unpack("<d", read(0x4AEFE0, 8))[0],
               "DAT_004aefe0_note": "0x40cc19 lane 抖动 ×48.0",
           },
           "key_sites": {
               "0040cbc6": "age 门：cmp [edi+0x1c],0x104; jle 激活支路（>0x104 → 0x40cbda call 0x46fc30 复员）",
               "0040cbf6": "spawn 门 a：cmp [edi+0x1c],[edi+0x18]（age != age−1）",
               "0040cbff": "spawn 门 b：cmp [edi+0xd9c],0; jg 跳过（D9C≤0 才 spawn）",
               "0040cc14": "call 0x472e10 signed_unit（ret=0x40cc19；每 spawn 恰 1 抽）",
               "0040cc19": "fmul 48.0 → 0x40cc33 lane 表 [edx*4+0x4bba54] → 0x40cc43 fstp [edi+ecx*4+0x7f0]",
               "0040cd47": "每帧 tick 起点合流：fld -1.0",
               "0040cd4e": "lea esi,[edi+0xd98]（倒计时插值对象）",
               "0040cd57": "call 0x4732c0（rate=-1.0；*rate=DAT_004c0a28=1.0f ∈(0.99,1.01) → 整数支路：0xDA0+=−1、D9C=round(acc)、D98=旧 D9C）",
               "0040cea4": "槽 scale 内联 time_rate 条件 1（fcomp 0.99）",
               "0040ceb7": "槽 scale 内联 time_rate 条件 2（fcomp 1.01）→ ∈(0.99,1.01) 走 0x40cec0 整数 +1（fadd 1.0）支路",
               "0040cae5": "0x40c910：scale_cursor=25（spawn 间隔源）",
               "0040cb04": "0x40c910：0xD9C=0（首帧立即 spawn）",
               "0040cb14": "0x40c910：0xDA4=&0x4c0a28（倒计时 rate 指针）",
               "0040cb32": "0x40c910：0xD98=-1",
               "0040cb3c": "0x40c910：cursor=0",
           },
           "functions": {}}
    for name, va, size, note in DISASM_FUNCS:
        code = read(va, size)
        insns = []
        for ins in md.disasm(code, va):
            row = {"addr": ins.address, "bytes": ins.bytes.hex(),
                   "mnem": ins.mnemonic, "op": ins.op_str}
            notes = []
            if ins.mnemonic in ("call", "jmp") and ins.operands and \
               ins.operands[0].type == capstone.x86.X86_OP_IMM:
                tgt = ins.operands[0].imm
                if tgt in DISASM_CALLEES:
                    notes.append("→ %s" % DISASM_CALLEES[tgt])
                    row["call_target"] = tgt
            for mdisp, mnote in DISASM_MEM_NOTES:
                for op in ins.operands:
                    if op.type == capstone.x86.X86_OP_MEM and \
                       op.mem.disp == mdisp:
                        notes.append(mnote)
            if notes:
                row["note"] = notes
            insns.append(row)
        out["functions"][name] = {"va": va, "size": size, "note": note,
                                  "instructions": insns}
    # 运行时交叉核对（spawn age 序列来自既有 capture dump）。
    rng_path = os.path.join(args.out, "rng_bomb.jsonl")
    if os.path.exists(rng_path):
        ages = sorted(g["bf"] - 140 for g in
                      (json.loads(l) for l in open(rng_path)))
        out["runtime_cross_check"] = {
            "source": rng_path, "bomb_trigger_bf": 140,
            "spawn_ages": ages,
            "intervals": [ages[i + 1] - ages[i]
                          for i in range(len(ages) - 1)],
            "model": "age0 立即 spawn（0x40c910 清 0xD9C）；其后间隔=scale_cursor"
                     "（25 起，每 spawn −2，下限 7）；0x4732c0 以 rate=-1.0 逐帧"
                     "递减 0xDA0，0xD9C=round(acc)，门 0xD9C<1（ret 0x40cc19 恰每 spawn 1 抽）",
        }
    path = os.path.join(args.out, "disasm.json")
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("WROTE", path)
    return 0


# ---------------------------------------------------------------- verify ----

def mode_verify(args):
    D = args.out
    t = [json.loads(l) for l in open(os.path.join(D, "bomb_ticks.jsonl"))]
    rng = [json.loads(l) for l in open(os.path.join(D, "rng_bomb.jsonl"))]
    rects = [json.loads(l) for l in open(os.path.join(D, "rect_bomb.jsonl"))]
    bybf = {r["bf"]: r for r in t}
    bfs = sorted(bybf)
    idx = {b: i for i, b in enumerate(bfs)}

    def post(bf):
        return bybf[bfs[idx[bf] + 1]]

    lanes = [224.0, 192.0, 320.0, 224.0, 64.0, 80.0, 384.0, 320.0, 96.0, 128.0, 224.0]

    # 1) 矩形 421/421：中心/角度/h 全逐位（spawn 帧 h=0 用 post-tick 槽态）。
    ok_rect = 0
    rect_bad = []
    for r in rects:
        bf = r["bf"]
        row = bybf[bf]
        if r["h"] == 0.0:
            ps = post(bf)
            c = row["cursor"]
            y = ps["ys"][c]
            par = struct.unpack("<f", struct.pack("<I", ps["parity"][c]))[0]
            vm = ps["vms"][c]
            rot = vm["rot_z"] if vm else None
            pre_scale = row["scale"][c * 5]
        else:
            m = [i for i, vm in enumerate(row["vms"])
                 if vm and vm["rot_z"] == r["ang"]
                 and f32(f32(row["scale"][i * 5]) * 128.0) == r["h"]]
            if len(m) != 1:
                rect_bad.append({"bf": bf, "why": "slot-match", "n": len(m)})
                continue
            i = m[0]
            y = row["ys"][i]
            par = struct.unpack("<f", struct.pack("<I", row["parity"][i]))[0]
            rot = row["vms"][i]["rot_z"]
            pre_scale = row["scale"][i * 5]
        if rot is None:
            rect_bad.append({"bf": bf, "why": "no-vm"})
            continue
        ap = f32(rot + f32(1.5707963705062866))
        dx = f32(math.cos(ap) * 256.0)
        dy = f32(math.sin(ap) * 256.0)
        if par < 0:
            cx, cy = f32(0.0 - dx), f32(y - dy)
        else:
            cx, cy = dx, f32(y + dy)
        if (f32(pre_scale * 128.0) != r["h"] or cx != r["cx"] or cy != r["cy"]
                or r["w_bits"] != 0x42C00000 or r["arg5"] != 0 or r["arg6"] != 30):
            rect_bad.append({"bf": bf, "cx": cx, "cx0": r["cx"],
                             "cy": cy, "cy0": r["cy"], "h": pre_scale * 128.0,
                             "h0": r["h"]})
        else:
            ok_rect += 1

    # 2) lane y 27/27（0x472c90 公式从抽前种子重算）。
    def u16(x):
        return x & 0xFFFF

    def next_pair(seed):
        a = u16((seed ^ 0x9630) - 0x6553)
        step = u16((a >> 14) + a * 4)
        b = u16((step ^ 0x9630) - 0x6553)
        return a, b

    prev = [0] * 60
    spawns = []
    for r in t:
        for i, a in enumerate(r["ids"]):
            if a and not prev[i]:
                spawns.append((r["bf"], i, r["ys"][i]))
        prev = r["ids"]
    ok_lane = 0
    lane_bad = []
    for s, g in zip(spawns, rng):
        bf, slot, y = s
        a, b = next_pair(g["seed"])
        val = f32(((a << 16) | b) * (1.0 / 2147483648.0) - 1.0)
        ymodel = f32(f32(lanes[slot % 11]) + f32(f32(val * 48.0)))
        if ymodel == y:
            ok_lane += 1
        else:
            lane_bad.append({"slot": slot, "bf": bf, "model": ymodel, "real": y})

    # 3) spawn 间隔与倒计时模型逐帧核对。
    #    模型（0x4732c0 语义）：entry(t).D9C==0 → 本 tick spawn；
    #    spawn tick 内 scale_cursor=old−2(下限 7)、0x4016c0(scale) 置
    #    {D98=scale−1, D9C=scale, acc=scale.0}，同帧 tick → D98←D9C、
    #    acc−=1、D9C=round(acc)。故 entry 规则：
    #      spawn tick t        : D9C==0（bf140 首帧由 0x40c910 直接清 0）
    #      spawn 于 t−1        : D98=scale_new, D9C=scale_new−1
    #      其余                : D98(t)=D9C(t−1), D9C(t)=D9C(t−1)−1
    spawn_bfs = [g["bf"] for g in rng]
    ages = [b - 140 for b in spawn_bfs]
    intervals = [ages[i + 1] - ages[i] for i in range(len(ages) - 1)]
    spawn_set = set(spawn_bfs)
    cd_bad = []

    def s32(x):
        return struct.unpack("<i", struct.pack("<I", x & 0xFFFFFFFF))[0]

    prev = None
    for r in t:
        bf = r["bf"]
        d98, d9c = s32(r["interp_value"]), s32(r["guard"])
        if bf in spawn_set:
            if d9c != 0:
                cd_bad.append({"bf": bf, "field": "spawn-gate",
                               "want_d9c": 0, "got": d9c})
        elif prev is not None and prev["bf"] in spawn_set and prev["bf"] != bf:
            if d98 != r["scale_cursor"] or d9c != r["scale_cursor"] - 1:
                cd_bad.append({"bf": bf, "field": "post-spawn-init",
                               "want": (r["scale_cursor"],
                                        r["scale_cursor"] - 1),
                               "got": (d98, d9c)})
        elif prev is not None:
            pd9c = s32(prev["guard"])
            if d98 != pd9c or d9c != pd9c - 1:
                cd_bad.append({"bf": bf, "field": "countdown-step",
                               "want": (pd9c, pd9c - 1), "got": (d98, d9c)})
        prev = r
    # scale_cursor 递减模型（25 起，每个 spawn tick 内 −2、下限 7；
    # entry dump 为 tick 前值 → spawn tick 自身仍是旧值）。
    want_sc = 25
    for r in t:
        if r["scale_cursor"] != want_sc:
            cd_bad.append({"bf": r["bf"], "field": "scale_cursor",
                           "want": want_sc, "got": r["scale_cursor"]})
        if r["bf"] in spawn_set:
            want_sc = max(7, want_sc - 2)

    # 4) scale +1/冻结；5) 261 生命周期。
    scale_bad = []
    for k in range(len(t) - 1):
        a, b = t[k], t[k + 1]
        for i, aid in enumerate(a["ids"]):
            if aid:
                va, vb = a["scale"][i * 5], b["scale"][i * 5]
                if b["ids"][i]:
                    if abs(vb - (va + 1.0)) > 1e-6:
                        scale_bad.append({"bf": b["bf"], "slot": i})
                else:
                    if vb != va:
                        scale_bad.append({"bf": b["bf"], "slot": i,
                                          "why": "freeze"})
    last = t[-1]
    lifecycle = {
        "tick_n": len(t), "first_bf": t[0]["bf"], "last_bf": last["bf"],
        "last_age": last["bf"] - 140,
        "age261_no_spawn_no_rect": (last["cursor"] == t[-2]["cursor"]
                                    and last["bomb_anm"] != 0),
    }
    res = {
        "rect_bit_exact": [ok_rect, len(rects)],
        "rect_bad": rect_bad[:8],
        "lane_formula_exact": [ok_lane, len(spawns)],
        "lane_bad": lane_bad[:8],
        "spawn_ages": ages,
        "spawn_intervals": intervals,
        "countdown_model_bad": cd_bad[:8],
        "scale_step_bad": scale_bad[:8],
        "lifecycle": lifecycle,
    }
    path = os.path.join(D, "verification.json")
    with open(path, "w") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("WROTE", path)
    print(json.dumps(res, ensure_ascii=False)[:1200])
    return 0


def math_cos(x):
    return math.cos(x)


def math_sin(x):
    return math.sin(x)


# --------------------------------------------------------------- capture ----

def build_synthetic_replay():
    """合成 Youmu 回放（内存内；真实文件基底 + 三处合成，见模块 docstring）。"""
    replay = ReplayFile().decode(open("games/replays/th13_01.rpy", "rb").read())
    top = bytearray(replay.top_header)
    struct.pack_into("<ii", top, 0x5C, 3, 0)     # character=3(Youmu) shot=0
    replay.top_header = bytes(top)
    replay.character, replay.shot_type = 3, 0
    stage = replay.stages[0]
    hdr = bytearray(stage.header)
    n_frames = 800
    events = bytes((n_frames + 29) // 30)
    blob = n_frames * 6 + len(events)
    struct.pack_into("<I", hdr, 0x04, n_frames)  # 头+0x04 frame_count
    struct.pack_into("<I", hdr, 0x08, blob)      # 头+0x08 blob_size
    stage.header = bytes(hdr)
    inp = bytearray()
    for f in range(n_frames):
        if f == BOMB_PRESS_DEFAULT:
            inp += struct.pack("<HHH", 0x0003, 0x0002, 0x0000)  # Z 保持 + X 按下
        elif f == BOMB_PRESS_DEFAULT + 1:
            inp += struct.pack("<HHH", 0x0001, 0x0000, 0x0002)  # X 释放
        else:
            inp += struct.pack("<HHH", 0x0001, 0x0000, 0x0000)  # Z 待机
    stage.input = bytes(inp)
    stage.events = events
    stage.frame_count = n_frames
    stage.blob_size = blob
    return replay


def first_bomb_bf(stage):
    """实际录像只读探测：首个 X(0x02) 按下沿帧。"""
    for f in range(stage.frame_count):
        if stage.frame(f)[1] & 0x02:
            return f
    return None


def mode_capture(args):
    os.makedirs(args.out, exist_ok=True)
    if args.replay:
        # 实际录像：只读解码，不写任何头/输入字节。
        replay = ReplayFile().decode(open(args.replay, "rb").read())
        stage = replay.stages[args.stage]
        bomb_bf = args.bomb_bf if args.bomb_bf is not None \
            else first_bomb_bf(stage)
        if bomb_bf is None:
            # 本 stage 无炸弹按下沿：照常只读跑（boot/通路健全性取证）。
            print("note: no bomb press in stage %d input; capture-only run"
                  % args.stage)
        tag = "%s-s%d-bf%s" % (os.path.splitext(
            os.path.basename(args.replay))[0], args.stage,
            bomb_bf if bomb_bf is not None else "nobomb")
        synthetic = False
    else:
        replay = build_synthetic_replay()
        stage = replay.stages[0]
        bomb_bf = args.bomb_bf if args.bomb_bf is not None else BOMB_PRESS_DEFAULT
        tag = "synthetic-bf%d" % bomb_bf
        synthetic = True
    if not args.frames:
        total = (bomb_bf + args.detail_frames + 40) if bomb_bf is not None \
            else 400
    else:
        total = args.frames

    out_draws = os.path.join(args.out, "draws-%s.jsonl" % tag)
    oracle = BattleOracle(EXE, "artifacts/reference/raw", out_draws)
    oracle.replay = replay
    oracle.replay_stage = stage
    oracle.replay_stage_index = args.stage
    oracle._replay_file_name = os.path.basename(args.replay) if args.replay \
        else "youmu_bomb_probe.rpy"
    oracle.difficulty = replay.rank & 3
    oracle.character = replay.character & 3
    oracle.shot_type = replay.shot_type
    oracle.target_frames = total

    bomb_ticks = []      # detail 窗口内全量 / 窗口外 compact
    trigger = []
    rng_detail = []      # 炸弹域抽随机（仅 detail 窗口）
    rng_per_frame = {}   # abs_f → [total, in_bomb]
    rect_detail = []     # 炸弹域矩形生成（仅 detail 窗口）
    rect_n = [0]
    first_tick = [None]

    def bf():
        return (oracle.frame - oracle.battle_first
                if oracle.battle_first is not None else None)

    def dump_slots(uc, player, mgr, compact):
        ids = list(struct.unpack("<60I", bytes(uc.mem_read(player + P_IDS, 240))))
        alive = [i for i, a in enumerate(ids) if a]
        row = {
            "frame": oracle.frame, "bf": bf(),
            "timer_a": struct.unpack("<i", struct.pack("<I", rd_u32(uc, player + P_TIMER_A)))[0],
            "timer_b": struct.unpack("<i", struct.pack("<I", rd_u32(uc, player + P_TIMER_B)))[0],
            "cursor": rd_u32(uc, player + P_CURSOR),
            "scale_cursor": rd_u32(uc, player + P_SCALE_CURSOR),
            "interp_value": rd_u32(uc, player + P_INTERP_VALUE),
            "guard": rd_u32(uc, player + P_GUARD),
            "bomb_anm": rd_u32(uc, player + P_BOMB_ANM),
            "rect_count": rd_u32(uc, player + P_RECT_COUNT),
            "alive_n": len(alive),
        }
        if compact:
            row["alive"] = alive
            return row
        parity = list(struct.unpack("<60I", bytes(uc.mem_read(player + P_PARITY, 240))))
        ys = list(struct.unpack("<60f", bytes(uc.mem_read(player + P_YS, 240))))
        scale = list(struct.unpack("<300f", bytes(uc.mem_read(player + P_SCALE, 1200))))
        vms = []
        for aid in ids:
            if aid == 0:
                vms.append(None)
                continue
            v = vm_of(mgr, aid)
            vms.append({"vm": v, "vm_id": rd_u32(uc, v + 0x530),
                        "rot_z": rd_f32(uc, v + 0x50)})
        row.update({"player": player, "mgr": mgr, "ids": ids,
                    "parity": parity, "ys": ys, "scale": scale, "vms": vms,
                    "x": rd_f32(uc, player + 0x5B8),
                    "y": rd_f32(uc, player + 0x5BC)})
        return row

    def on_trigger(machine):
        uc = machine.uc
        player = uc.reg_read(UC_X86_REG_ECX)
        trigger.append({
            "frame": oracle.frame, "bf": bf(), "player": player,
            "cursor": rd_u32(uc, player + P_CURSOR),
            "scale_cursor": rd_u32(uc, player + P_SCALE_CURSOR),
            "interp_value": rd_u32(uc, player + P_INTERP_VALUE),
            "guard": rd_u32(uc, player + P_GUARD),
            "timer_a": rd_u32(uc, player + P_TIMER_A),
            "timer_b": rd_u32(uc, player + P_TIMER_B),
            "bomb_anm": rd_u32(uc, player + P_BOMB_ANM),
            "rect_count": rd_u32(uc, player + P_RECT_COUNT),
            "rng": bytes(uc.mem_read(RNG_OBJ, 8)).hex(),
        })

    def on_tick(machine):
        uc = machine.uc
        player = uc.reg_read(UC_X86_REG_ECX)
        mgr = rd_u32(uc, ANM_MGR)
        if first_tick[0] is None:
            first_tick[0] = oracle.frame
        compact = (first_tick[0] is not None
                   and oracle.frame - first_tick[0] >= args.detail_frames)
        bomb_ticks.append(dump_slots(uc, player, mgr, compact))

    def on_rng(machine):
        uc = machine.uc
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = rd_u32(uc, esp)
        cell = rng_per_frame.setdefault(oracle.frame, [0, 0])
        cell[0] += 1
        if in_bomb(ret):
            cell[1] += 1
            if first_tick[0] is None or \
               oracle.frame - first_tick[0] < args.detail_frames:
                seed, _pad, count = struct.unpack(
                    "<HHI", bytes(uc.mem_read(RNG_OBJ, 8)))
                rng_detail.append({"abs_f": oracle.frame, "bf": bf(),
                                   "ret": ret, "ret_off": ret - F_40CB50,
                                   "seed": seed, "count": count})

    def on_rect(machine):
        uc = machine.uc
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = rd_u32(uc, esp)
        if not in_bomb(ret):
            return
        rect_n[0] += 1
        if first_tick[0] is None or \
           oracle.frame - first_tick[0] >= args.detail_frames:
            return
        args6 = struct.unpack("<6I", bytes(uc.mem_read(esp + 4, 24)))
        cx, cy, cz = struct.unpack("<3f", bytes(uc.mem_read(args6[0], 12)))
        rect_detail.append({
            "abs_f": oracle.frame, "bf": bf(), "ret_off": ret - F_40CB50,
            "cx": cx, "cy": cy, "cz": cz,
            "w_bits": args6[1], "h_bits": args6[2], "ang_bits": args6[3],
            "w": struct.unpack("<f", struct.pack("<I", args6[1]))[0],
            "h": struct.unpack("<f", struct.pack("<I", args6[2]))[0],
            "ang": struct.unpack("<f", struct.pack("<I", args6[3]))[0],
            "arg5": args6[4], "arg6": args6[5],
        })

    oracle.m.watch(F_40C910, on_trigger)
    oracle.m.watch(F_40CB50, on_tick)
    oracle.m.watch(F_472E10, on_rng)
    oracle.m.watch(F_4457E0, on_rect)

    faults = []

    def on_unmapped(uc, access, address, size, value, ud):
        if len(faults) < 4:
            faults.append({"abs_frame": oracle.frame,
                           "eip": uc.reg_read(UC_X86_REG_EIP),
                           "addr": address, "size": size})
        return False

    oracle.uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED,
                       on_unmapped)

    print("PROBE youmu bomb: mode=%s tag=%s bf_bomb=%s total=%d detail=%d"
          % ("real-replay" if args.replay else "synthetic", tag,
             bomb_bf, total, args.detail_frames))
    oracle.run(frames=total)
    print("PROBE run done: frame=%s battle_first=%s trigger=%d tick=%d "
          "rng_bomb=%d rect_bomb=%d"
          % (oracle.frame, oracle.battle_first, len(trigger), len(bomb_ticks),
             len(rng_detail), rect_n[0]))

    def dump(name, rows):
        path = os.path.join(args.out, name)
        with open(path, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("WROTE", path, len(rows))

    dump("bomb_trigger-%s.jsonl" % tag, trigger)
    dump("bomb_ticks-%s.jsonl" % tag, bomb_ticks)
    dump("rng_bomb-%s.jsonl" % tag, rng_detail)
    dump("rect_bomb-%s.jsonl" % tag, rect_detail)
    dump("rng_per_frame-%s.jsonl" % tag,
         [{"abs_f": f, "total": v[0], "in_40cb50": v[1]}
          for f, v in sorted(rng_per_frame.items())])
    lanes = struct.unpack("<11f", bytes(oracle.uc.mem_read(0x4BBA54, 44)))
    summary = {
        "mode": "real-replay" if args.replay else "synthetic",
        "replay": args.replay, "stage": args.stage, "tag": tag,
        "bomb_bf_input": bomb_bf, "battle_first": oracle.battle_first,
        "frames_run": oracle.frame, "trigger_n": len(trigger),
        "tick_n": len(bomb_ticks), "rng_bomb_n": len(rng_detail),
        "rect_bomb_n": rect_n[0],
        "detail_frames": args.detail_frames,
        "unmapped_faults": faults,
        "lane_table_4bba54": list(lanes),
        "edge_mismatches": getattr(oracle, "replay_edge_mismatches", [])[:4],
        "crash_frame": oracle.crash_frame,
    }
    with open(os.path.join(args.out, "summary-%s.json" % tag), "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    return 0


def mode_watch(args):
    """0x4e0e08 族 singleton 写点取证：真实 boot 内谁写 0x4e0ef8/0x4e1150。

    unicorn mem-write 范围钩 [0x4e0e00,0x4e1200)：记录写者 EIP/返回地址/写入值；
    结束时 dump 槽值+vtable。只观测，不改任何状态（认证不消费本记录）。"""
    from unicorn import UC_HOOK_MEM_WRITE
    from unicorn.x86_const import UC_X86_REG_ESP as ESP
    os.makedirs(args.out, exist_ok=True)
    replay = build_synthetic_replay()
    stage = replay.stages[0]
    out_draws = os.path.join(args.out, "draws-watch-singletons.jsonl")
    oracle = BattleOracle(EXE, "artifacts/reference/raw", out_draws)
    oracle.replay = replay
    oracle.replay_stage = stage
    oracle.replay_stage_index = 0
    oracle._replay_file_name = "youmu_bomb_probe.rpy"
    oracle.difficulty, oracle.character, oracle.shot_type = 1, 3, 0
    oracle.target_frames = args.frames or 120

    writes = []

    def on_write(uc, access, address, size, value, ud):
        if len(writes) >= 400:
            return
        esp = uc.reg_read(ESP)
        try:
            ret = rd_u32(uc, esp)
        except Exception:
            ret = 0
        writes.append({"abs_frame": oracle.frame,
                       "eip": uc.reg_read(UC_X86_REG_EIP),
                       "addr": address, "size": size, "value": value,
                       "ret": ret})

    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, on_write,
                       begin=0x4E0E00, end=0x4E1200)
    oracle.run(frames=oracle.target_frames)

    def fname(eip):
        best = None
        for f in FN_TABLE:
            if f[1] <= eip < f[1] + f[2]:
                best = "%s+0x%x" % (f[0], eip - f[1])
        return best

    rows = []
    for w in writes:
        rows.append(dict(w, eip_hex=hex(w["eip"]), ret_hex=hex(w["ret"]),
                         eip_fn=fname(w["eip"]), ret_fn=fname(w["ret"])))
    slots = {}
    for va in (0x4E0E08, 0x4E0EF8, 0x4E1150):
        v = rd_u32(oracle.uc, va)
        slots[hex(va)] = {"ptr": hex(v),
                          "vtable": hex(rd_u32(oracle.uc, v)) if v else None,
                          "note": fname(v) if v else "NULL"}
    res = {"mode": "watch-singletons", "frames_run": oracle.frame,
           "writes": rows, "slots_after": slots}
    path = os.path.join(args.out, "singleton_writes.json")
    with open(path, "w") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("WROTE", path, "writes:", len(rows))
    print("SLOTS", json.dumps(slots, ensure_ascii=False))
    return 0


# 反编译函数边界（functions.json 提取，供 EIP 归属）。
FN_TABLE = [
    ("004460d0", 0x004460D0, 1012), ("00446f20", 0x00446F20, 968),
    ("00446000", 0x00446000, 208), ("00446870", 0x00446870, 0x2B0),
    ("00446fb0", 0x00446FB0, 0x2C0), ("004470110", 0x00470110, 0x180),
    ("0043a440", 0x0043A440, 0x180), ("0042b990", 0x0042B990, 0x2C0),
    ("00449070", 0x00449070, 0x120), ("00447890", 0x00447890, 0x180),
]


def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--disasm", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--watch", action="store_true", dest="watch")
    ap.add_argument("--replay")
    ap.add_argument("--stage", type=int, default=0)
    ap.add_argument("--frames", type=int)
    ap.add_argument("--bomb-bf", type=int)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--detail-frames", type=int, default=300)
    a, extra = ap.parse_known_args(argv)
    if a.disasm:
        return mode_disasm(a)
    if a.verify:
        return mode_verify(a)
    if a.watch:
        return mode_watch(a)
    if extra and not a.replay:
        # 旧用法兼容：700 140 → 合成模式位置参数。
        return mode_capture(argparse.Namespace(
            disasm=False, verify=False, replay=None, stage=0,
            frames=int(extra[0]) if len(extra) > 0 else None,
            bomb_bf=int(extra[1]) if len(extra) > 1 else None,
            out=a.out, detail_frames=a.detail_frames))
    return mode_capture(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
