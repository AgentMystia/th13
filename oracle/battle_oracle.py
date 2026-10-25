"""对局窗「真机全链路」绘制 oracle（hud-capture-plan §1 方案落地，M1.1）。

与窗C（title_oracle --window c）的区别：不经菜单（boot→标题态建立即直投
场景 7），战斗装载线程体 0x42b990 **整体补调**（窗C 为切片驱动——切片态
在战斗第 250 tick 撞 ECL 弹道 VM 创建返 0 崩溃，f1003 前沿）。装载等待环
处置（plan §5.1 三对策的实证组合）：
  ① 预开 BATTLE_PREOPEN 六档（窗C 十五轮验证的清单）+ 装载后 load_finish
     （0x46e510 循环完成全部待装载槽——整体驱动无需切片中就绪）；
  ② drive 前直写 anm_mgr+0x20 = -1（0x42b990:46-53 头部等待环
     `while (mgr+0x20 > -1)` 的零迭代条件）；
  ③ loader_draining=True 复用 TO 的 Sleep 桩（0x42b990:222 第二等待环
     `while (DAT_004e14c0 != 0) Sleep(0x10)` 的排空语义）。

帧序 = title_frame（TO 同款 0x45d2f0 真实帧序：UP 刷→视口→update walk→
thread_bodies→BeginScene→清批→fog→draw walk→UP 刷→End/Present→捕获）。
战斗装载分两段自然执行：线程体（0x42b990：子系统创建族+score 应用+任务
注册）与 0x42c550 首帧第二段（DAT_004c2168==0 分支：stage ctx/front.anm/
结算覆盖层等）——后者由调度器自跑，无需补调。

对局参数预置（plan §1.1，等价 0x42b990 里菜单确认路径写下的字段）：
  DAT_004be7c4=难度、DAT_004be7b8=角色、DAT_004be81c=1、DAT_004be820=1、
  DAT_004c22dc=0x4bb56c（stage1 表）→ wr32(0x4dc6a0+0x584, 7) 请求场景 7。
时机：标题态建立后（scene current==4 首帧）即投递——4→7 转场为窗C 实证
路径，跳过菜单交互（难度/角色走预置而非菜单写）。

自证四件套（hud-capture-plan §2.2）：同参数两跑 sha256 一致 / 窗内每帧
ops>0 / 0x418e00 每帧恰一次（双跑防护，§1.5）/ 战斗段帧数=目标值。

用法（容器内 /work）：
    python3 -m oracle.battle_oracle [exe] [raw_dir] [out_jsonl]
        [--frames N=1200] [--difficulty 1] [--character 0] [--observe]
默认输出 artifacts/oracle-draws/st01-draws-full.jsonl（窗=战斗首帧起
--frames 帧，帧号重编 0..N-1；战斗首帧=场景 current==7 且 ops>0 的首帧）。
"""
import json
import os
import struct
import sys
import time

from unicorn import UcError, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_EDI,
                               UC_X86_REG_ESP, UC_X86_REG_EIP, UC_X86_REG_ECX,
                               UC_X86_REG_ESI)

from .title_oracle import (TitleOracle, SCENE_MGR, ANM_OPEN, ANM_MGR_CTOR,
                           BATTLE_PREOPEN)

SCENE_NEXT = SCENE_MGR + 0x584        # 场景 next（投递 7）
SCENE_CUR = SCENE_MGR + 0x580
GAMEINF = 0x004C2194                  # GameInf 指针（0x42c490 建 scene7 时写）
DAT_DIFFICULTY = 0x004BE7C4
DAT_CHARACTER = 0x004BE7B8
DAT_SHOT_TYPE = 0x004BE7BC
DAT_SPELL = 0x004BE7D4
DAT_81C = 0x004BE81C
DAT_820 = 0x004BE820
DAT_STAGETABLE = 0x004C22DC
STAGE1_TABLE = 0x004BB56C
BATTLE_THREAD = 0x0042B990            # 战斗装载线程体（0x42bf90 thunk 目标）
ENEMY_UPDATE = 0x00418E00             # ECL 敌机逻辑（每帧恰一次自证）
MGR_LOADING = 0x20                    # anm mgr+0x20：装载等待环计数（>-1 忙）

# ---- 原版 Replay 恢复/消费链锚点（artifacts/decomp/jp/functions/*.c）----
# 身份（反编译实证；修正旧实现的锚点误认）：
#   0x004C2198 = ReplayManager **壳对象**：+0 起为回放文件名缓冲
#     （0x4525b0:165-170 / 0x44c570:40-46 把选中 .rpy 文件名逐字节拷入；
#      0x448c40:25-29 把它当文件名字符串源读）——不是数据管理器。
#   0x004C22C8 = 数据管理器全局（0x447900:22/179 写、0x447d60:68 清；
#     由 0x42b990→0x447ed0→0x447900 构造）。
#   0x448c40:75-78 的 stage 槽号 = stage 头 +0x00 关卡号（u16），非数组下标；
#     消费期 DAT_004be81c = 选中关卡号 = 头 +0x00（0x4525b0:160-161）。
REPLAY_MGR = 0x004C22C8               # DAT_004c22c8：回放数据管理器全局
REPLAY_SHELL = 0x004C2198             # DAT_004c2198：壳对象（文件名缓冲）
DAT_REPLAY_FLAG = 0x004BE830         # 回放/状态位域（|= 写入点穷举：
                                       # 0x42bfa0:76 |=2 仅 DAT_004dcc24==0xc
                                       # 关间场景；:101 |=1 非 10/b 场景；
                                       # 0x42a370:36 |=4；0x405a20:28 |=0x100。
                                       # 零售首关回放（scene 0xd）不置位——
                                       # 位形 0，0x42b990:178 走 0x447ed0 装载
                                       # 链；关间位形 3。本窗位形 2 的等价
                                       # 选择与主动分歧登记见装备段注释）
REPLAY_RESTORE = 0x00449070           # 关卡初值恢复（stage 头 +0x14 起 0x58
                                       # 字节 → 0x4be7b8；头 +0x02 u16 种子 →
                                       # 0x4dc324/0x4dc31c）
REPLAY_CONSUME = 0x00448200           # 每帧六字节消费（ESI=管理器：产
                                       # 0x4e4c08 态 /0x4e4c14 按下沿 /0x4e4c18
                                       # 释放沿，内调 0x414f30 重算沿）
REPLAY_INIT_STATE = 0x004BE7B8        # 恢复目标：0x449070:43-49 复制的 22 dwords
INPUT_HELD = 0x004E4C08               # 回放当前帧输入态（u16 位域）
INPUT_PRESSED = 0x004E4C14            # 按下沿（0x448200:33 写）
INPUT_RELEASED = 0x004E4C18           # 释放沿（0x448200:34 写）

# replay u16 输入位（replay.md §5）→ VK 集（键态桩路径；位打包/边沿/连打
# 由真机 0x471620/0x4711a0/0x474fb0 完成——TO 单帧注入自证法已验证）。
REPLAY_INPUT_VK = (
    (0x01, 0x5A),   # Z
    (0x02, 0x58),   # X（炸弹/取消）
    (0x08, 0x10),   # Shift（低速）
    (0x10, 0x26),   # ↑
    (0x20, 0x28),   # ↓
    (0x40, 0x25),   # ←
    (0x80, 0x27),   # →
)
PLAYER_ANM = {0: "pl00.anm", 1: "pl01.anm", 2: "pl02.anm", 3: "pl03.anm"}


class BattleOracle(TitleOracle):
    def __init__(self, exe_path, raw_dir, out_path):
        super().__init__(exe_path, raw_dir, out_path)
        self.window = "battle"
        self.input_script = {}             # 冒烟窗无输入（--replay 接 u16 流）
        self.difficulty = 1                # th13_01 = Normal
        self.character = 0                 # th13_01 = ReimuA
        self.shot_type = 0
        self.replay = None                 # ReplayFile（--replay）
        self.replay_stage = None           # ReplayStage
        self._replay_file_name = ""        # --replay 文件名（壳 0x4c2198 语义）
        self.battle_requested = False
        self.battle_loader_started = False
        self.battle_first = None
        self.crash_frame = None
        self.enemy_updates = 0             # 当帧 0x418e00 入口计数
        self.enemy_update_audit = []       # (帧, 计数) 异常记录
        self.gameover_first = None
        self.enemy_update_zero_frames = 0
        self.target_frames = 1200
        self.family_ops = {}               # 纹理名→首现帧（捕获面证据）

    def install_overrides(self):
        super().install_overrides()
        # 拆除事件窗时代的内部地址覆写——全链路（真调度器）语境下的误伤件：
        # render_oracle.py:835 把 0x46f820 桩为「死亡炸裂环弹返 0」（事件窗
        # 里它只被 ECL 561 路径以 6 参调用），但 0x46f820 本体是**通用 ANM
        # VM 创建**——真机链路里敌弹 appearance/一切按 id 建 VM 都走它，被
        # 桩后首个敌弹即 0x46f820 返 0 → 0x4125b0 EAX=0 裸写崩（窗C f1003
        # 与本窗 f471 同根，寄存器轨迹实证：调用方 eax=有效档，入口 eax=0）。
        self._lift_override(0x0046F820, "通用 VM 创建（事件窗曾误桩为死亡环弹）")
        # 同族误桩：0x462450（ANM 脚本启动/步进）与 0x461cb0（按弹种建弹体
        # ANM）——事件窗注记「合成 mgr 无实数据」的前提在全链路不成立；
        # 0x462450 被桩直接致 0xab VM 脚本不跑 → 子 VM 172/173/175 永不生成
        # → 0x42b030（进度条 VM 更新，per-frame 多调用者）裸写崩。
        self._lift_override(0x00462450, "ANM 脚本启动（事件窗合成 mgr 前提失效）")
        self._lift_override(0x00461CB0, "按弹种建弹体 ANM（同上）")
        # 敌机伤害/死亡链不能沿用无输入窗的空转桩。
        self._lift_override(0x00419A90, "敌机击杀链（有输入窗必需）")
        # BGM 变体启动（0x405a20）：战斗 f200 起把曲名 '.' 位替换 'b' 变体并
        # 0x43afc0 播放——曲名表由音频系统解析 thbgm.fmt 产生（桩环境缺席，
        # 名字空 → strchr('.') 返 0 → 0x405b1d 裸写崩，hud-capture-plan §5.2
        # 「曲名表读取」预言项）。音频域不在门禁内：按其 <200 帧早退分支语义
        # 桩为 ret -1（登记：跳过 DAT_004be830|=0x100 与 [esi+0x14] 位写——
        # 音频标志面，窗口若在此分歧再启用真表解析）。
        self.m.override_call(0x00405A20, lambda mm: 0xFFFFFFFF, ret_pop=4)
        # 帧率步进 0x476e00（x87 读 D3D caps [esi+0xc]+0x90——假设备该页未
        # 映射）：纯等待/跳帧语义（void），GameOver 入口 0x43f570 链首次触达
        # （M1.3b f1202 崩点）。空转桩（无状态写，时序域不在门禁内）。
        self.m.override_call(0x00476E00, lambda mm: 0)

    def _lift_override(self, address, why):
        """摘除 override_call 的内部地址覆写（真机链路恢复原生执行）。"""
        m = self.m
        if address in getattr(m, "_overrides", {}):
            del m._overrides[address]
        watchers = getattr(m, "_watchers", {}).get(address)
        if watchers and getattr(m, "_on_override", None) in watchers:
            watchers.remove(m._on_override)
        sys.stderr.write("LIFT-OVERRIDE %08x (%s)\n" % (address, why))

    def install_hooks(self):
        super().install_hooks()
        # 原版回放消费相位取证：0x448200 每次入口计数（真机里它由
        # 0x448e40 thunk 从 update 任务队列进入；本窗由 consume_replay_frame
        # 帧首直调一次，计数与 replay_consumed 逐帧对照）。
        def on_replay_consume(uc, addr, size, ud):
            self.replay_consumed += 1
        self.uc.hook_add(UC_HOOK_CODE, on_replay_consume,
                         begin=REPLAY_CONSUME, end=REPLAY_CONSUME)
        # 双跑防护（plan §1.5）：0x418e00 每帧恰一次——路径 A 下它由 0x418d30
        # 注册的任务 thunk 驱动；手工驱动或双注册会破坏事件流等价性。
        def on_enemy_update(uc, addr, size, ud):
            self.enemy_updates += 1
        self.uc.hook_add(UC_HOOK_CODE, on_enemy_update,
                         begin=ENEMY_UPDATE, end=ENEMY_UPDATE)

        # 诊断（M1.1 冒烟期）：装载线程体关键位点取证。
        def on_loader_fail(uc, addr, size, ud):
            sys.stderr.write("[loader] FAIL-EXIT hit (frame=%d)\n"
                             % self.frame)
        self.uc.hook_add(UC_HOOK_CODE, on_loader_fail,
                         begin=0x0042BE39, end=0x0042BE39)
        self.loader_anm_opens = 0

        def on_anm_open(uc, addr, size, ud):
            self.loader_anm_opens += 1
        self.uc.hook_add(UC_HOOK_CODE, on_anm_open,
                         begin=0x0046DF40, end=0x0046DF40)
        self.c550_entries = 0

        def on_battle_main(uc, addr, size, ud):
            self.c550_entries += 1
        self.uc.hook_add(UC_HOOK_CODE, on_battle_main,
                         begin=0x0042C550, end=0x0042C550)
        # 0x448ec0 真机触达防护（P2-2 + P2·EC0 相位修正 + 授权修复的输入
        # 面补全）：0x42c550:80（scene7+0x14==0 首帧）与 :169（+0x14==0x1e
        # 且 +0x60&0x800 第二段——P3-3 未实测面）在本窗由调度器真跑；
        # 0x448ec0 回放分支把当前游标←初始、帧计数←0（0x44902c/0x44902f/
        # 0x449032——反编译 00448ec0.c:52-54）并**清零输入三元组**
        # （0x448f18-0x448f3c：0x4e4c04/08/0c/10/14/18/20 全清）——对零售
        # （启动先于首消费）两者都是重置；对本窗（帧首已消费 ≥1 帧）是
        # **回滚+输入面破坏**：游标退回之外，prev（0x4e4c0c）被清零会使
        # 下一帧的 0x414f30 沿重算产生幻影按下沿（实测：bf=1 pressed=1
        # vs 文件沿 0）。零售里首消费发生在启动清零**之后**（walk 内），
        # 清零永不破坏已消费状态——本窗的消费在帧首（title_frame 前），
        # 清零在 walk 内发生即破坏。防护须双点：
        #   入口点 0x448ec0（执行前）快照输入三元组（0x4e4c08 cur /
        #   0x4e4c0c prev——0x4e4c14/18 由 0x414f30 从三元组重算，无需单独存）；
        #   回滚写后 0x449035（唯一路径，objdump 实证）重推游标（初始+
        #   n*6）与帧计数（n）并恢复快照的 cur/prev——完整消除回滚+输入
        #   面破坏；0x448ec0 其余副作用（玩家对象+0x14840←头+0x6c、
        #   0x447850 位置复位——真值源=stage 头）保留为零售正当语义。
        # 未实测声明：第二段（0x1e）触发窗 +0x60&0x800 的位置态未知，
        # 防护按"任何 0x448ec0 触达"统一处理（入口快照幂等）。
        self.replay_ec0_hits = 0
        self._replay_ec0_input_snapshot = None

        def on_replay_ec0_entry(uc, addr, size, ud):
            if getattr(self, "replay_stage", None) is None:
                return
            mgr = self.rd32(REPLAY_MGR)
            if not mgr or self.rd32(mgr + 0x10) != 1:
                return
            self._replay_ec0_input_snapshot = (
                self.rd32(INPUT_HELD), self.rd32(0x004E4C0C))

        def on_replay_ec0(uc, addr, size, ud):
            # 钩子相位=0x449035（回滚写后）：本体回滚与输入清零已生效。
            # 到达该地址仅可能来自 mgr+0x10==1 分支（0x448f4a jne
            # 0x448fd3 / 0x448fd6 jne 0x44905e 唯一路径）。
            if getattr(self, "replay_stage", None) is None:
                return
            mgr = self.rd32(REPLAY_MGR)
            if not mgr or self.rd32(mgr + 0x10) != 1:
                return    # 仅回放分支（0x448ec0:39-40）受防护
            consumed = getattr(self, "replay_consumed", 0)
            self.replay_ec0_hits += 1
            if consumed:
                number = self.replay_stage.number
                base = mgr + (number * 5 + 0x19) * 8
                self.wr32(base + 4, self.rd32(base) + 6 * consumed)
                self.wr32(base + 0x14, consumed)
                snapshot = getattr(self, "_replay_ec0_input_snapshot", None)
                if snapshot is not None:
                    # 恢复被启动清零破坏的 cur/prev（0x448f18-3c 清零面）：
                    # 本窗首消费先于 scene7 首帧（帧首直调），零售的
                    # "启动清零先于首消费"次序在本窗倒置——恢复快照即
                    # 恢复倒置前的等价状态（游标/计数/输入三面同拍）。
                    self.wr32(INPUT_HELD, snapshot[0])
                    self.wr32(0x004E4C0C, snapshot[1])
                sys.stderr.write(
                    "REPLAY-EC0-GUARD hit=%d frame=%d consumed=%d "
                    "(cursor/frame-counter/input restored after 0x448ec0 "
                    "rollback)\n" % (self.replay_ec0_hits, self.frame,
                                    consumed))
        self.uc.hook_add(UC_HOOK_CODE, on_replay_ec0_entry,
                         begin=0x00448EC0, end=0x00448EC0)
        self.uc.hook_add(UC_HOOK_CODE, on_replay_ec0,
                         begin=0x00449035, end=0x00449035)
        # 尾部完成位取证：0x42bf17（插值结构重置）/0x42bf52（清装载完成位）
        self.tail_hits = {}

        def on_tail(uc, addr, size, ud):
            self.tail_hits[addr] = self.tail_hits.get(addr, 0) + 1
        for tail_addr in (0x0042BF17, 0x0042BF52, 0x0042BF90):
            self.uc.hook_add(UC_HOOK_CODE, on_tail,
                             begin=tail_addr, end=tail_addr)
        # M1.1 取证：首帧段链（0x425170 stage ctx）与 VM 创建/查找需求。
        from unicorn.x86_const import UC_X86_REG_EBX as _EBX
        self.vm_creates = []        # (帧, script, caller)
        self.ctx_slot = None        # 0x42b030 读取的 ANM 槽（EBX+0x11c）

        def on_vm_create(uc, addr, size, ud):
            esp = uc.reg_read(UC_X86_REG_EAX)  # 占位：参数经寄存器约定
            self.vm_creates.append((self.frame, -1, 0))
        self.uc.hook_add(UC_HOOK_CODE, on_vm_create,
                         begin=0x0046F820, end=0x0046F820)

        def on_stage_ctx_init(uc, addr, size, ud):
            sys.stderr.write("[stage-ctx] 0x425170 enter frame=%d\n"
                             % self.frame)
        self.uc.hook_add(UC_HOOK_CODE, on_stage_ctx_init,
                         begin=0x00425170, end=0x00425170)

        def on_b030(uc, addr, size, ud):
            ebx = uc.reg_read(_EBX)
            slot = self.rd32(ebx + 0x11C) if ebx else -1
            if self.ctx_slot is None or slot != self.ctx_slot:
                self.ctx_slot = slot
                sys.stderr.write("[b030] slot=%d frame=%d\n"
                                 % (slot, self.frame))
        self.uc.hook_add(UC_HOOK_CODE, on_b030,
                         begin=0x0042B030, end=0x0042B030)
        # 0x46f820 VM 创建取证：入口 (ESI=出参, EAX=档, 栈=脚本 id) 与出口
        # VM 值；0x4125b0 入口 EAX（裸写受害者）。
        from unicorn.x86_const import (UC_X86_REG_ESI as _ESI,
                                       UC_X86_REG_EAX as _EAX,
                                       UC_X86_REG_ESP as _ESP)
        self.vm_trace = []

        def on_f820(uc, addr, size, ud):
            esp = uc.reg_read(_ESP)
            self.vm_trace.append(["in", self.frame, uc.reg_read(_ESI),
                                  uc.reg_read(_EAX), self.rd32(esp + 4)])
            if len(self.vm_trace) > 64:
                self.vm_trace.pop(0)
        self.uc.hook_add(UC_HOOK_CODE, on_f820,
                         begin=0x0046F820, end=0x0046F820)

        def on_125b0(uc, addr, size, ud):
            eax = uc.reg_read(_EAX)
            if not eax and self.vm_trace:
                sys.stderr.write(
                    "[125b0] VM=0 frame=%d trace=%s\n"
                    % (self.frame,
                       [(t[0], hex(t[3]), t[4]) for t in self.vm_trace[-6:]]))
        self.uc.hook_add(UC_HOOK_CODE, on_125b0,
                         begin=0x004125B0, end=0x004125B0)
        # 池收支计数：0x46f820 创建 / 0x46fc30 回收 / 0x4706c0 分配（空闲表
        # 空走 malloc 路径时采样调用者）。
        self.vm_created = 0
        self.vm_recycled = 0
        self.vm_pool_mallocs = 0

        def on_f820_count(uc, addr, size, ud):
            self.vm_created += 1
        self.uc.hook_add(UC_HOOK_CODE, on_f820_count,
                         begin=0x0046F820, end=0x0046F820)

        def on_fc30(uc, addr, size, ud):
            self.vm_recycled += 1
        self.uc.hook_add(UC_HOOK_CODE, on_fc30,
                         begin=0x0046FC30, end=0x0046FC30)

        def on_4706c0(uc, addr, size, ud):
            mgr = self.rd32(0x004DC688)
            if mgr and not self.rd32(mgr + 0xB77B24):
                self.vm_pool_mallocs += 1
                if self.vm_pool_mallocs % 50 == 1:
                    esp = uc.reg_read(_ESP)
                    sys.stderr.write("[pool] malloc-path #%d frame=%d "
                                     "caller=%08x\n" % (
                                         self.vm_pool_mallocs, self.frame,
                                         self.rd32(esp)))
        self.uc.hook_add(UC_HOOK_CODE, on_4706c0,
                         begin=0x004706C0, end=0x004706C0)
        # 结算/GameOver 覆盖层入口取证：调用者 + GameInf 模式/进度态。
        def on_f570(uc, addr, size, ud):
            if self.gameover_first is None:
                self.gameover_first = self.frame
            esp = uc.reg_read(_ESP)
            gameinf = self.rd32(GAMEINF)
            sys.stderr.write(
                "[f570] frame=%d caller=%08x g74=%d be81c=%d be7c8=%08x "
                "be830=%08x\n"
                % (self.frame, self.rd32(esp),
                   self.rd32(gameinf + 0x74) if gameinf else -1,
                   self.rd32(DAT_81C), self.rd32(0x004BE7C8),
                   self.rd32(0x004BE830)))
        self.uc.hook_add(UC_HOOK_CODE, on_f570,
                         begin=0x0043F570, end=0x0043F570)
        # 自机弹链取证：0x40e4d0（自机弹创建/绘制遍历）每帧调用计数。
        self.shot_calls = 0

        def on_shots(uc, addr, size, ud):
            self.shot_calls += 1
        self.uc.hook_add(UC_HOOK_CODE, on_shots,
                         begin=0x0040E4D0, end=0x0040E4D0)
        # 指令环缓冲（TH13_TRACE=1 取证跑）：崩溃前指令轨迹回放。
        if os.environ.get("TH13_TRACE"):
            import collections
            from unicorn.x86_const import (UC_X86_REG_EAX as _T_EAX,
                                           UC_X86_REG_EBX as _T_EBX,
                                           UC_X86_REG_ESI as _T_ESI)
            self._trace_ring = collections.deque(maxlen=3000)

            def on_code(uc, addr, size, ud):
                self._trace_ring.append(
                    (addr, uc.reg_read(_T_EAX), uc.reg_read(_T_EBX),
                     uc.reg_read(_T_ESI)))
            self.uc.hook_add(UC_HOOK_CODE, on_code)

            def dump_ring():
                names = self.func_names
                out = []
                for a, eax, ebx, esi in self._trace_ring:
                    out.append("%08x%s eax=%08x ebx=%08x esi=%08x"
                               % (a, "(%s)" % names[a] if a in names else "",
                                  eax, ebx, esi))
                path = "artifacts/tmp-scratch/battle-crash-trace.txt"
                with open(path, "w") as trace_file:
                    trace_file.write("\n".join(out))
                sys.stderr.write("[trace] %d instrs -> %s\n"
                                 % (len(out), path))
            self._dump_ring = dump_ring
        else:
            self._dump_ring = None

    def maybe_request_battle(self):
        """标题态建立（scene current==4）首帧：预置对局参数 + 投递场景 7。

        --replay 模式（授权修复 task-configure-20261025-oracle-replay-window-
        repair，proposal_sha256 d69bf872…）：标题态只做**菜单语义预置**
        （难度/角色/符卡禁用/关卡号/关卡表——0x42bd4c 读 DAT_004c22dc
        建装载 stage 表，缺失即崩）；回放装备本体（mgr+0x10=1 + 槽位/
        游标族 + 0x449070 恢复）移至 battle_loader_body 完成点——装载器
        以零售位形 0 走 0x447ed0 链重建 mgr（0x42b990:175 (be830&2)==0
        分支），标题态预装备的 mgr 会被该链重建（0x448264 [ecx+4] 未
        映射读，form-0 实验实证）。零售证据：首关回放（0x4525b0 case 3
        → scene 0xd）不置任何 be830 位（位形 0），装载走 0x447ed0→
        0x447900 case 2/1；旧位形 2（|=2）是已撤回的主动分歧（其
        0x42b990 else 分支跳过 0x447ed0 → 0x424fd0 以未构造的 EBX=
        stage-ctx 写 +0x14c → 0x424fe4 冷启动崩，0042b990.c:175-191 +
        00424fd0.c:15-20 反编译链）。"""
        if self.battle_requested:
            return
        if self.rd32(SCENE_CUR) != 4:
            return
        # __new__ 构造的最小测试副本可能未带 replay 成员（默认非回放）。
        if getattr(self, "replay_stage", None) is not None:
            self.wr32(DAT_DIFFICULTY, self.difficulty)
            self.wr32(DAT_CHARACTER, self.character)
            self.wr32(DAT_SHOT_TYPE, self.shot_type)
            self.wr32(DAT_SPELL, 0xFFFFFFFF)
            self.wr32(DAT_81C, 1)
            self.wr32(DAT_820, 1)
            self.wr32(DAT_STAGETABLE, STAGE1_TABLE)
        else:
            self.wr32(DAT_DIFFICULTY, self.difficulty)
            self.wr32(DAT_CHARACTER, self.character)
            self.wr32(DAT_SHOT_TYPE, self.shot_type)
            # 0x451450:323：普通开局必须禁用符卡练习（ECL [-9907]）。
            self.wr32(DAT_SPELL, 0xFFFFFFFF)
            self.wr32(DAT_81C, 1)
            self.wr32(DAT_820, 1)
            self.wr32(DAT_STAGETABLE, STAGE1_TABLE)
        self.wr32(SCENE_NEXT, 7)
        self.battle_requested = True
        sys.stderr.write("BATTLE-REQUEST frame=%d diff=%d char=%d\n"
                         % (self.frame, self.rd32(DAT_DIFFICULTY),
                            self.rd32(DAT_CHARACTER)))

    def _equip_replay_manager(self):
        """原版回放管理器装备（0x447900 case 1 语义，只搭数据不猜字段）。

        身份与方向（全部反编译实证）：
          0x004C2198 = ReplayManager 壳（文件名缓冲，0x4525b0:165-170 拷入
            选中 .rpy 文件名）——本窗不经过菜单，仍按零售把文件名写进壳，
            保持壳语义完整；数据管理器全局是 0x004C22C8（本方法全程只用
            后者，壳不再被误用为管理器指针）。
          mgr+0x1c → 0x74 **顶层头独立块**（0x447900:43-51 分配、0x448c40:67
            从解压流 +0x74 起 malloc 拷贝；case 1 装备序 0x447900:197-199 从
            它的 +0x5c/0x60/0x64 **读出写全局**——方向是顶层头→全局，不是
            把 python 字段写进任何头；顶层头必须原样字节，stage 头块严禁
            别名（+0x5c..+0x6b 落在 0x449070 恢复窗口 [头+0x14,头+0x6c) 内，
            别名+字段写=恢复源污染，th13_01/udSr01 真机复刻实证差
            +0x5c/+0x64）。
          stage 槽号 = stage 头 +0x00 关卡号（0x448c40:75 `*psVar8`），非
            python 数组下标；消费期 DAT_004be81c = 关卡号（0x4525b0:160-161
            选中+1；0x44c570 槽扫描同源）。本窗按 stage.number 装备槽。
          base(idx) = mgr+(idx*5+0x19)*8 游标族：
            base+0 初始游标（头+0xc8）、base+4 当前游标、base+8 尾界
            （头+0xc8+帧数*6）、base+0xc 事件游标、base+0x10 头指针
            （与 mgr+0xd8+idx*0x28 同址）、base+0x14 帧计数。
          DAT_004be830 |= 2（0x42bfa0:76，DAT_004dcc24==0xc 场景）：零售
            回放由它分流 0x42b990:178 走 0x449070 恢复分支；本窗直投
            scene7 无场景机置位，按零售语义预置（battle_loader_body 真跑
            0x42b990 时即走回放分支，不与 0x42bfa0:56 的 `&=0xfffffffc`
            冲突——该清位发生在 scene7 建立期、本窗预置在其后）。
        0x449070 全真机执行恢复：stage 头+0x14 起 0x58 字节 → 0x4be7b8、
        头+0x02 u16 种子 → 0x4dc324/0x4dc31c；逐位断言（头为唯一真值源）。"""
        replay = self.replay
        stage = self.replay_stage
        if not stage.header or len(stage.header) != 0xC4:
            self.fail("replay stage header missing (decoder must keep the "
                      "full 0xc4 raw header)")
        if not replay.top_header or len(replay.top_header) != 0x74:
            self.fail("replay top header missing (decoder must keep the "
                      "raw 0x74 top header)")
        # 数据管理器（0x447900:16 的 800 字节）与三块数据缓冲
        mgr = self.game_malloc(0x320)
        self.uc.mem_write(mgr, b"\x00" * 0x320)
        top_block = self.game_malloc(0x74)
        self.uc.mem_write(top_block, bytes(replay.top_header))
        stage_block = self.game_malloc(0xC4 + len(stage.input)
                                       + max(len(stage.events), 1))
        self.uc.mem_write(stage_block, bytes(stage.header) + bytes(stage.input)
                          + bytes(stage.events))
        # 槽号 = stage 头 +0x00 关卡号（0x448c40:75）；顶层头独立块（0x448c40:67）
        stage_number = stage.number
        self.wr32(mgr + 0x1C, top_block)              # 顶层头槽（0x447900:51）
        self.wr32(mgr + 0x20 + stage_number * 4, stage_block)  # stage 头槽
        base = mgr + (stage_number * 5 + 0x19) * 8    # 游标族
        self.wr32(base, stage_block + 0xC4)           # 初始游标（头+0xc8）
        self.wr32(base + 8, stage_block + 0xC4 + stage.frame_count * 6)
        self.wr32(mgr + 0xD8 + stage_number * 0x28, stage_block)  # 头指针
        self.wr32(mgr + 0x218, 0xFFFFFFFF)  # 激活索引（0x447900:227 装备置 -1）
        self.wr32(mgr + 0x10, 1)            # 回放模式（0x448ec0:39/0x449070:33 判据）
        # 全局：管理器（0x447900:22/179）、壳文件名（0x4525b0:165-170 语义）、
        # 关卡号族（0x4525b0:156-161：be81c=be820=关卡号、关卡表按关卡号步进）
        self.wr32(REPLAY_MGR, mgr)
        file_name = ("replay/" + self._replay_file_name).encode("ascii",
                                                                "replace") + b"\0"
        shell_name = self.game_malloc(len(file_name))
        self.uc.mem_write(shell_name, file_name)
        self.wr32(REPLAY_SHELL, shell_name)
        self.wr32(DAT_81C, stage_number)
        self.wr32(DAT_820, stage_number)
        self.wr32(DAT_STAGETABLE, 0x004BB4F0 + stage_number * 0x7C)
        # 回放模式位（授权修复 task-configure-20261025-oracle-replay-
        # window-repair，撤回旧位形 2 分歧）：
        #   零售位形写入点穷举（全部 |= 形式）：0x42bfa0:76 |=2（仅
        #   DAT_004dcc24==0xc 关间场景）、:101 |=1（非 10/b 场景）、
        #   0x42a370:36 |=4、0x405a20:28 |=0x100。
        #   **首关回放**（菜单 0x4525b0 case 3 → scene 0xd → 0x42b990）
        #   无任何代码置位：零售首关位形=0 → 0x42b990:178 走 0x447ed0
        #   装载链（0x447900 case 2 装载 + case 1 装备/恢复一体）、
        #   :197 (0&9)=0 → 0x418d30 ECL 构造。
        #   本窗（修复后）保持位形 0：装载器走零售 0x447ed0 链重建 mgr
        #   （旧 |=2 位形使 :175 else 分支跳过 0x447ed0 → 0x424fd0 以
        #   未构造 EBX 写 +0x14c → 0x424fe4 冷启动崩，反编译
        #   0042b990.c:175-191 + 00424fd0.c:15-20）。装备（mgr+0x10=1 +
        #   槽位/游标族 + 0x449070 恢复）由 battle_loader_body 完成点
        #   后置执行；0x447900 case 1 的 :209-220（be7d4/be830 符卡位
        #   调整）未复刻，仍为登记待证面。
        # 原版恢复（0x449070 全真机执行：case 1 读 mgr+0x10==1、按
        # DAT_004be81c 槽号读 base 族做游标初始化/帧计数 -1、种子与
        # 0x58 字节恢复）
        self.drive(REPLAY_RESTORE, timeout_ms=60_000)
        restored = bytes(self.uc.mem_read(REPLAY_INIT_STATE, 0x58))
        expected = bytes(stage.header[0x14:0x6C])
        if restored != expected:
            self.fail("retail 0x449070 initial-state restore differs "
                      "(got %s want %s)" % (restored.hex()[:32],
                                            expected.hex()[:32]))
        seed = self.rd32(0x004DC324) & 0xFFFF
        if seed != stage.stamp:
            self.fail("retail seed restore differs: 0x4dc324&0xffff=%d "
                      "stage.stamp=%d" % (seed, stage.stamp))
        # 消费启动（0x448ec0:41-54，scene7+0x14==0 首帧执行；mgr+0x10==1
        # 回放分支）：激活索引=DAT_004be81c、当前游标←初始、事件游标←尾界、
        # 帧计数=0。0x449070 装备态帧计数=-1 会被 0x448200:14 的负值拦截
        # 清零——启动写是原版解除拦截的同拍语义。真机 0x42c550 同帧稍后
        # 触达 0x448ec0 时对已消费游标是回滚（P2-2），由 install_hooks 的
        # on_replay_ec0 防护消除——见 run 的启动点与防护注释。
        self.wr32(mgr + 0x218, stage_number)
        self.wr32(base + 4, self.rd32(base))
        self.wr32(base + 0xC, self.rd32(base + 8))
        self.wr32(base + 0x14, 0)
        # 待证面登记（P3-5）：0x448200:35 内调 FUN_00414f30(0x4e49f0)——
        # 0x414f30 读 +0x218 当帧态、维护 +0x94 连打计数、写 +0x224/+0x228
        # 沿；本窗 0x4e49f0 由零售 0x439ee0→0x471620 每帧真打包（帧首
        # 消费时 +0x94 计数可能携带上帧残留——与机制脚本的每实验清零
        #（scripts/check_replay_mechanism.py:63 mem_write 0x234）口径不同）。
        # 按 00448200.c 字节序：held/pressed/released 三 u16 由 :25/:33/:34
        # 直写文件值且先于 :35 的 0x414f30 调用——沿断言面无污染路径；
        # 但 0x414f30 还写 +0x220/+0x230（超时沿，0x414f30:26-31），其
        # 消费方若受计数历史影响属战斗窗未实测面，如实登记。
        sys.stderr.write("REPLAY-EQUIP frame=%d stage=%d slot=%d seed=%d "
                         "frames=%d header_sha256=%.16s\n"
                         % (self.frame, stage.number, stage_number,
                            stage.stamp, stage.frame_count,
                            __import__("hashlib").sha256(
                                bytes(stage.header)).hexdigest()))

    def consume_replay_frame(self):
        """每战斗帧一次原版 0x448200 消费（ESI=回放管理器）+ 相位取证。

        0x448200 反编译语义（artifacts/decomp/jp/functions/00448200.c）：
          - DAT_004c2194==0（无对局）→ 输入清零返 1（不消费）；
          - 激活索引（mgr+0x218）<0 → 输入清零 +0x210 自增（未装备）；
          - 帧计数（base+0x14）<0 → 输入清零（装备态，0x448ec0 启动解除）；
          - 帧计数 ≥ 头内帧数（*(stage头+4)）→ 输入清零（EOF 后零输入）；
          - 否则读 6 字节：[0]=态→0x4e4c08、[2]=按下沿→0x4e4c14、
            [4]=释放沿→0x4e4c18，三字全 F（0xffff）→ EOF 停（0x43e5a0）；
            内调 0x414f30（0x4e49f0 键位结构）重算沿，当前游标 +6，
            帧计数 +1，mgr+0x210 全局帧计数 +1（每 0x1e 步进事件游标）。
        相位证明：本方法与 0x418e00（敌机逻辑）同帧执行——消费计数
        self.replay_consumed 与每帧 0x448200 hook 计数一致，且消费产出
        的沿与 stage 文件帧逐字节相等（0x414f30 幂等性）。"""
        mgr = self.rd32(REPLAY_MGR)
        if not mgr:
            return
        battle_f = self.frame - (self.battle_first or 0)
        self.drive(REPLAY_CONSUME, regs={"esi": mgr}, timeout_ms=60_000)
        held = self.rd32(INPUT_HELD) & 0xFFFF
        pressed = self.rd32(INPUT_PRESSED) & 0xFFFF
        released = self.rd32(INPUT_RELEASED) & 0xFFFF
        stage = self.replay_stage
        if 0 <= battle_f < stage.frame_count:
            want_held, want_pressed, want_released = stage.frame(battle_f)
            if (held, pressed, released) != (want_held, want_pressed,
                                             want_released):
                if len(self.replay_edge_mismatches) < 8:
                    self.replay_edge_mismatches.append(
                        (battle_f, hex(held), hex(pressed), hex(released),
                         hex(want_held), hex(want_pressed),
                         hex(want_released)))
        # 相位取证（缺口 b 修正）：样本不只记自驱直调计数——
        # (帧, 0x448200 累计入口数, 上一帧 0x418e00 敌机逻辑次数,
        #  本帧敌机逻辑前置计数)。成对断言（缺口 a）：每帧消费恰一次，
        # 且消费先于本帧 0x418e00（真机 update walk 内 0x448e40 任务先于
        # 敌机逻辑任务执行——0x447900:221 注册的回放消费任务与
        # 0x418d30 注册的敌机任务同链，次序按零售回放窗输入先行的
        # 语义保持；绝对次序无法从 0x470990 的优先级插入单独证明，
        # 如实登记为"帧内先行"等价而非链内次序证明）。
        self.replay_phase_samples.append(
            (self.frame, self.replay_consumed,
             getattr(self, "enemy_updates", 0)))
        if len(self.replay_phase_samples) >= 2:
            previous = self.replay_phase_samples[-2]
            if previous[1] + 1 != self.replay_consumed:
                if len(self.replay_edge_mismatches) < 8:
                    self.replay_edge_mismatches.append(
                        (battle_f, "phase", "consume_count",
                         str(previous[1]), str(self.replay_consumed),
                         "", ""))
        if self._replay_log is not None:
            self._replay_log.write(
                "bf=%d held=%04x pressed=%04x released=%04x cursor=%d\n"
                % (battle_f, held, pressed, released,
                   self.rd32(mgr + 0x210)))

    def battle_loader_body(self):
        """0x42b990 整体补调（plan §1.2）：_beginthreadex 桩返 0 的替代。

        屏障感知：TO 的 0x418d30 入口钩子（emu_stop，窗C 切片遗产）会把
        整体驱动在 ECL mgr 创建处刹停——此处按 battle_loader_step 同款
        「屏障原子执行到返回地址再续驱」处理（不切片，其余整体跑完）。"""
        if self.battle_loader_started or self.battle_loader_done:
            return
        if not self.rd32(GAMEINF):
            return
        self.battle_loader_started = True
        self.phase = "battle_loader"
        # ① 预开六档（窗C 十五轮验证清单，plXX 按角色定档——窗C 硬编码
        # pl01 系其窗选角色 1；0x46df40 在 0x180 预置下非阻塞）
        preopen = [(slot, PLAYER_ANM.get(self.character, "pl01.anm")
                    if name == "pl01.anm" else name)
                   for slot, name in BATTLE_PREOPEN]
        for slot, name in preopen:
            nb = self.game_malloc(0x40)
            self.uc.mem_write(nb, name.encode("ascii") + b"\0")
            self.drive(ANM_OPEN, regs={"eax": slot, "ecx": nb},
                       timeout_ms=300_000)
        self.load_finish()
        # ② 头部等待环零迭代：直写 mgr+0x20 = -1（装载全完成态）
        self.wr32(self.anm_mgr + MGR_LOADING, 0xFFFFFFFF)
        # ③ 整体驱动（Sleep 桩经 loader_draining 排空 0x4e14c0；0x418d30
        #    屏障刹停时原子执行后续驱）
        from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX,
                                       UC_X86_REG_ECX, UC_X86_REG_EDX,
                                       UC_X86_REG_ESI, UC_X86_REG_EBP,
                                       UC_X86_REG_EIP, UC_X86_REG_ESP)
        from .machine import RET_MAGIC
        from .title_oracle import LOADER_STACK, BARRIER_ADDR
        esp = LOADER_STACK
        self.uc.mem_write(esp, struct.pack("<I", RET_MAGIC))
        self.uc.reg_write(UC_X86_REG_ESP, esp)
        self.uc.reg_write(UC_X86_REG_EBP, esp + 0x400)
        for reg in (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
                    UC_X86_REG_EDX, UC_X86_REG_ESI):
            self.uc.reg_write(reg, 0)
        self.loader_draining = True
        eip = BATTLE_THREAD
        barriers = 0
        try:
            while True:
                self.uc.emu_start(eip, RET_MAGIC, timeout=600_000_000)
                now = self.uc.reg_read(UC_X86_REG_EIP)
                if now == BARRIER_ADDR and not self._barrier_atomic:
                    esp_now = self.uc.reg_read(UC_X86_REG_ESP)
                    ret_to = self.rd32(esp_now)
                    barriers += 1
                    self._barrier_atomic = True
                    try:
                        self.uc.emu_start(now, ret_to, timeout=600_000_000)
                    finally:
                        self._barrier_atomic = False
                    now = self.uc.reg_read(UC_X86_REG_EIP)
                    if now != ret_to:
                        self.fail("battle loader barrier diverged at %08x"
                                  % now)
                    eip = ret_to
                    continue
                break
        finally:
            self.loader_draining = False
        scene7 = self.rd32(GAMEINF)
        sys.stderr.write(
            "BATTLE-LOADER eip_end=%08x barriers=%d tail=%s scene7+0x60=%08x "
            "dcec8=%d\n" % (
                self.uc.reg_read(UC_X86_REG_EIP), barriers,
                {hex(k): v for k, v in self.tail_hits.items()},
                self.rd32(scene7 + 0x60) if scene7 else 0,
                self.rd32(0x004DCEC8)))
        if self.uc.reg_read(UC_X86_REG_EIP) != RET_MAGIC:
            self.fail("battle loader stopped mid-body at %08x"
                      % self.uc.reg_read(UC_X86_REG_EIP))
        self.battle_loader_done = True
        # 回放装备后置（授权修复，task-configure-20261025-oracle-replay-
        # window-repair）：装载器以零售位形 0 走 0x447ed0 链重建 mgr
        # （0x447900 case 0 普通装载 + 26 槽），标题态预装备会被该链
        # 重建覆盖（0x448264 [ecx+4] 未映射读，form-0 实验实证）——
        # 装备移至装载完成点：mgr+0x10=1 + stage 头槽/游标族 +
        # 0x449070 恢复（种子/0x58 初值逐位断言内建）。此后 scene7
        # 首帧 0x42c550:80 触达 0x448ec0 回放分支（mgr+0x10==1 判据），
        # EC0 防护（0x449035 钩，已批 review 修复相位）消除回滚。
        if getattr(self, "replay_stage", None) is not None:
            self._equip_replay_manager()
        self.load_finish()      # 线程体自开的档（st01 名单等）补完成
        # 契约档位（plan §5.2）：**有 score 路径**——开场线程 0x449b00 置位
        # DAT_004dcc30=1（score 系统初始化完成语义，与存档文件存在与否无涉：
        # seam miss scoreth13.dat 后仍置位，f3 实测）。0x42c550 首帧段/0x425170
        # 依赖该位创建 0xab 进度 VM 并即时调 0x42b030（其 per-frame 调用者
        # 0x4057f0/0x42d3c0/0x43d530 等亦然）——无 score 档在 f510 裸写崩
        # （窗C 清零该位属十五轮前旧权宜，其窗终 f1002 未达此界）。
        # 特殊档绑定（0x180 跳过语义的完成回填镜像）：装载体各子系统的
        # 0x46df40 开档走非阻塞路径，异步完成回填（子系统自读 26 槽表填
        # 自有档位）在桩环境不发生——按真机完成语义手工绑定，否则首个
        # 敌弹 appearance VM 创建以空档调用 0x46f820（0x40dfe3 读
        # [0x4c2174]+0x9752b4，窗C f1003 崩溃与本窗 f471 同根）。
        bullet_mgr = self.rd32(0x004C2174)
        bullet_file = self.rd32(self.anm_mgr + 0xB77B34 + 7 * 4)  # slot 7
        if bullet_mgr and bullet_file:
            self.wr32(bullet_mgr + 0x9752B4, bullet_file)
        front_inf = self.rd32(0x004C22D4)
        if front_inf and not self.rd32(front_inf + 0x10) and bullet_file:
            self.wr32(front_inf + 0x10, bullet_file)   # 0x44aa20 敌弹批档源
        sys.stderr.write("SPECIAL-SLOTS bullet=%08x front+0x10=%08x\n"
                         % (self.rd32(bullet_mgr + 0x9752B4)
                            if bullet_mgr else 0,
                            self.rd32(front_inf + 0x10)
                            if front_inf else 0))

    # battle_loader_done 语义位（TitleOracle._loader_state 不同源，独立标志）
    @property
    def battle_loader_done(self):
        return getattr(self, "_battle_loader_done", False)

    @battle_loader_done.setter
    def battle_loader_done(self, value):
        self._battle_loader_done = value

    def thread_bodies(self):
        """boot 期沿用 TO 的开场/标题装载体补调；场景 7 请求投递后不再回头
        ——TO 的 title_enable 块（开场计数达 300 时卸载 sig.anm/使能标题任务）
        会在战斗期对已废弃对象误触发（f180 实证：0x46e620 卸载砸穿战斗态）。"""
        if not self.battle_requested:
            super().thread_bodies()
            self.maybe_request_battle()
            return
        if not self.battle_loader_done:
            self.battle_loader_body()

    # ---------- 主流程 ----------
    def _mapped(self, addr):
        try:
            self.uc.mem_read(addr, 1)
            return True
        except Exception:
            return False

    def _shot_probe_frame(self, battle_f):
        """每帧落一行：原始调用计数、神灵池占用、敌机life快照。"""
        if not (0 <= battle_f <= 340):
            return
        import struct as _struct
        mgr = self.rd32(0x004C22A4)
        pool_live = 0
        pool_xy = []
        if mgr:
            for i in range(256):
                node = mgr + 0x14 + i * 0x88
                if self.rd32(node):
                    pool_live += 1
                    if len(pool_xy) < 4:
                        pool_xy.append("(%d,%d)" % (
                            _struct.unpack("<f", bytes(self.uc.mem_read(
                                node + 0x14, 4)))[0],
                            _struct.unpack("<f", bytes(self.uc.mem_read(
                                node + 0x18, 4)))[0]))
        enemies = []
        ctl = self.rd32(0x004C2188)
        if ctl:
            node = self.rd32(ctl + 0xB0)
            for _ in range(200):
                if not node:
                    break
                base = self.rd32(node)
                node = self.rd32(node + 4)
                if not base:
                    continue
                vm = base + 0x11EC
                life = _struct.unpack("<i", bytes(self.uc.mem_read(
                    vm + 0x3F4C, 4)))[0]
                x = _struct.unpack("<f", bytes(self.uc.mem_read(
                    vm + 0x44, 4)))[0]
                y = _struct.unpack("<f", bytes(self.uc.mem_read(
                    vm + 0x48, 4)))[0]
                dmg = _struct.unpack("<i", bytes(self.uc.mem_read(
                    vm + 0x290, 4)))[0]
                enemies.append("%x:L%d@(%d,%d)d%d"
                               % (base & 0xFFFFF, life, x + 320, y + 16, dmg))
                if len(enemies) >= 6:
                    break
                # life 写入哨兵：每个新敌机登记一次（M1.3a 伤害写入者定位）
                if not hasattr(self, "_life_watched"):
                    self._life_watched = set()
                la = vm + 0x3F4C
                if la not in self._life_watched and len(self._life_watched) < 40:
                    self._life_watched.add(la)

                    def on_life_write(uc, access, address, size, value, ud,
                                      _la=la):
                        eip = uc.reg_read(UC_X86_REG_EIP)
                        self._shot_probe.write(
                            "LIFEWRITE bf=%d addr=0x%08x size=%d "
                            "value=0x%x EIP=0x%08x\n"
                            % (self.frame - (self.battle_first or 0), _la,
                               size, value & 0xFFFFFFFF, eip))
                        return True
                    self.uc.hook_add(UC_HOOK_MEM_WRITE, on_life_write,
                                     begin=la, end=la)
        if battle_f == 1:
            for nm, a in (("be810_raw", 0x004BE810),
                          ("be7c0_raw", 0x004BE7C0),
                          ("dcc30_raw", 0x004DCC30),
                          ("be814", 0x004BE814), ("be844", 0x004BE844),
                          ("be7b8_char", 0x004BE7B8),
                          ("be7bc_shot", 0x004BE7BC)):
                self._shot_probe.write("STATE bf=1 %s=0x%08x (%d)\n"
                                       % (nm, self.rd32(a), self.rd32(a)))
            gi = self.rd32(0x004C2194)
            if gi:
                self._shot_probe.write(
                    "STATE bf=1 gameinf+0x60..0x90: %s\n" % " ".join(
                        "%x:%x" % (o, self.rd32(gi + o))
                        for o in range(0x60, 0x94, 4)))
        if battle_f in (30, 65, 100, 150):
            ctx = self.rd32(0x004C22C4)
            lines = []
            if ctx:
                for i in range(256):
                    node = ctx + 0x6A0 + i * 0x9C
                    if not self.rd32(node + 0x70):
                        continue
                    rec = self.rd32(node + 0x94)
                    fn = self.rd32(rec + 0x2C) if rec else 0
                    dmg = _struct.unpack("<f", bytes(self.uc.mem_read(
                        rec + 0x18, 4)))[0] if rec else 0.0
                    opt = self.rd32(rec + 0x20) if rec else -1
                    x = _struct.unpack("<f", bytes(self.uc.mem_read(
                        node + 0x2C, 4)))[0]
                    y = _struct.unpack("<f", bytes(self.uc.mem_read(
                        node + 0x30, 4)))[0]
                    lines.append("#%d rec=%08x fn2c=%08x dmg=%.1f opt=%d "
                                 "pos=(%.0f,%.0f)"
                                 % (i, rec, fn, dmg, opt, x + 320, y + 16))
                    if len(lines) >= 6:
                        break
            self._shot_probe.write("SHOTS bf=%d: %s\n"
                                   % (battle_f, " | ".join(lines) or "none"))
        self._shot_probe.write(
            "bf=%d calls=%d hits=%d pool=%d[%s] | %s | chain=%s | "
            "power=%d step=%d flags830=%x st81c=%d\n"
            % (battle_f,
               self._s22_calls - self._last_s22_calls,
               self._s22_hits - self._last_s22_hits,
               pool_live, ",".join(pool_xy), " ".join(enemies),
               " ".join("%x:%d" % (a, self._chain[a] - self._chain_last[a])
                        for a in sorted(self._chain)),
               self.rd32(0x004BE7E8), self.rd32(0x004BE7F0),
               self.rd32(0x004BE830), self.rd32(0x004BE81C)))
        self._last_s22_calls = self._s22_calls
        self._last_s22_hits = self._s22_hits
        self._chain_last = dict(self._chain)

    def _write_state_sample(self, battle_f):
        ctl = self.rd32(0x004C2188)
        player = self.rd32(0x004C22C4)
        row = {
            "schema": "th13-state-raw-v1",
            "frame": battle_f,
            "player_position": (list(struct.unpack(
                "<2f", bytes(self.uc.mem_read(player + 0x5B8, 8))))
                if player else None),
            "power": self.rd32(0x004BE7E8),
            "power_step": self.rd32(0x004BE7F0),
            "spell_id": struct.unpack("<i", bytes(
                self.uc.mem_read(DAT_SPELL, 4)))[0],
            "keys": sorted(self.frame_keys),
            "raw_globals": {"%08x" % address: self.rd32(address)
                            for address in (0x004BE7C0, 0x004BE7C8,
                                            0x004BE810, 0x004BE814)},
            "raw_enemy_manager": {"%x" % offset: self.rd32(ctl + offset)
                                  for offset in (0x24, 0x28, 0x2C)}
                                  if ctl else None,
        }
        self._state_log.write(json.dumps(row, ensure_ascii=True,
                                         separators=(",", ":")) + "\n")

    def run(self, frames=1200):
        self.install_overrides()
        self.init_ptd()
        self.install_d3d()
        self.patch_globals_title()
        self.install_hooks()

        d3d9_slot = next(slot for slot, imp in self.m.import_stubs.items()
                         if imp.name.lower() == "direct3dcreate9")
        d3d9_obj = self.drive(d3d9_slot, args=[0x20], timeout_ms=10_000)
        self.wr32(0x004DC6A4, d3d9_obj)
        if self.drive(0x0045DC80, timeout_ms=30_000) != 0:
            self.fail("device init failed")
        buf = self.game_malloc(0xF53C48)
        self.uc.mem_write(buf, b"\x00" * 0xF53C48)
        self.drive(ANM_MGR_CTOR, args=[buf], timeout_ms=60_000)
        self.wr32(0x004DC688, buf)
        self.anm_mgr = buf
        if self.drive(0x0043A440, timeout_ms=120_000) != 0:
            self.fail("scene task registration failed")

        out = open(self.out_path, "wb")
        t0 = time.time()
        battle_out = 0
        model = None
        model_misses = []
        self._state_log = None
        if os.environ.get("TH13_STATE_LOG"):
            self._state_log = open(
                "artifacts/tmp-scratch/battle-state-raw.jsonl", "w")
        # 可选原始调用计数与敌机life写点；不能由计数推定子系统身份。
        self._shot_probe = None
        if os.environ.get("TH13_SHOT_PROBE"):
            self._shot_probe = open(
                "artifacts/tmp-scratch/battle-shot-probe.txt", "w")
            self._s22_calls = 0
            self._s22_hits = 0
            self._last_s22_calls = 0
            self._last_s22_hits = 0

            def on_22b20(uc, addr, size, ud):
                self._s22_calls += 1
            self.uc.hook_add(UC_HOOK_CODE, on_22b20,
                             begin=0x00422B20, end=0x00422B20)

            # 自机弹链活性矩阵（M1.3a）：生成链/更新链/交互链逐帧计数。
            self._chain = {a: 0 for a in (
                0x00460D0, 0x00446000, 0x00445C40, 0x00445FA0,
                0x0040E630, 0x00446F20, 0x00422CD0, 0x00446870,
                0x00406580, 0x004065A0, 0x0040DB10,
                0x00446CB0, 0x00447590, 0x004474A0, 0x00447510)}
            self._chain_last = dict(self._chain)
            self._retaddr_seen = set()

            def on_chain(uc, addr, size, ud):
                self._chain[addr] += 1
                if addr in (0x00446000, 0x00446870) and addr not in \
                        self._retaddr_seen:
                    self._retaddr_seen.add(addr)
                    ret = self.rd32(uc.reg_read(UC_X86_REG_ESP))
                    sys.stderr.write(
                        "RETADDR %08x called from %08x (bf=%d)\n"
                        % (addr, ret,
                           self.frame - (self.battle_first or 0)))
            for a in self._chain:
                self.uc.hook_add(UC_HOOK_CODE, on_chain, begin=a, end=a)

        if self.replay_stage:
            from .replay_model import PlayerModel
            sht = "pl%02d%s.sht" % (self.replay.character,
                                    "a" if self.replay.shot_type == 0 else "b")
            sht_path = os.path.join(self.raw_dir, sht)
            if not os.path.isfile(sht_path):
                self.fail("replay needs %s in raw dir" % sht)
            model = PlayerModel(open(sht_path, "rb").read())
            if os.environ.get("TH13_POS_LOG"):
                self._pos_log = open(
                    "artifacts/tmp-scratch/battle-player-pos.txt", "w")
        # 回放消费取证（TH13_REPLAY_LOG=1）：每战斗帧一行——原版消费器
        # 产出的 0x4e4c08/14/18 与文件帧逐字节对照 + 采样相位
        # （0x448200 每帧恰一次、时序=每帧 title_frame 之前）。
        self._replay_log = None
        self.replay_consumed = 0        # 0x448200 实际消费帧数
        self.replay_phase_samples = []  # (帧, 消费计数) 相位证据
        self.replay_edge_mismatches = []  # 原版沿 vs 文件沿（必须恒空）
        if self.replay_stage is not None and os.environ.get("TH13_REPLAY_LOG"):
            self._replay_log = open(
                "artifacts/tmp-scratch/battle-replay-consume.txt", "w")
        try:
            for self.frame in range(0, frames + 900):
                if (self.replay_stage is not None
                        and self.battle_first is not None):
                    if getattr(self, "_replay_first_pending", False):
                        # 零售 scene7+0x14==0 首帧的 0x448ec0 启动写
                        #（0x42c550:80；激活索引/当前游标/事件游标/帧计数）
                        # 已在装备段预置——此标志保证启动先于首次消费的
                        # 次序可见。真机 0x42c550 同帧触达 0x448ec0 时，
                        # 对已消费 ≥1 帧的游标是**回滚不是幂等**（P2-2
                        # 修正：旧"幂等"声明不成立）——回滚由 install_hooks
                        # 的 0x448ec0 触达防护（on_replay_ec0 重推
                        # 游标/帧计数）消除；此处只清标志。
                        self._replay_first_pending = False
                    # 原版消费路径：0x448200（ESI=回放管理器）每帧一次，
                    # 时序=本帧 title_frame（0x45d2f0 真实帧序的 update
                    # walk）之前——0x447900:221 把 0x448e40（→0x448200）
                    # 注册为 update 任务（0x470ff0→0x470990 插 sched+0x14
                    # 链），真机即每帧 update walk 中消费；单线程等价=帧首
                    # 直调一次。产出 0x4e4c08/0x4e4c14/0x4e4c18 后由
                    # 0x414f30（0x448200:35 内调）重算沿。
                    self.consume_replay_frame()
                elif self.replay_stage is not None:
                    # 战斗首帧前（标题/装载段）不消费——0x448200:14 的
                    # DAT_004c2194==0 分支即原版的"无对局不消费"语义。
                    pass
                if self.replay_stage is not None and self.battle_first is not None:
                    state = self.rd32(INPUT_HELD)
                    self.frame_keys = frozenset(
                        vk for bit, vk in REPLAY_INPUT_VK if state & bit)
                elif self.replay_stage is not None:
                    self.frame_keys = frozenset()
                else:
                    self.frame_keys = frozenset()
                self.enemy_updates = 0
                try:
                    self.title_frame(out)
                except (SystemExit, UcError) as exc:
                    self.crash_frame = self.frame
                    try:
                        eip = self.uc.reg_read(
                            __import__("unicorn.x86_const",
                                       fromlist=["UC_X86_REG_EIP"])
                            .UC_X86_REG_EIP)
                        esp = self.uc.reg_read(
                            __import__("unicorn.x86_const",
                                       fromlist=["UC_X86_REG_ESP"])
                            .UC_X86_REG_ESP)
                        ret_chain = [self.rd32(esp + i * 4) for i in range(4)]
                        sys.stderr.write(
                            "CRASH frame=%d: %r phase=%s EIP=%08x ESP=%08x "
                            "stack=%s\n" % (self.frame, exc, self.phase, eip,
                                            esp,
                                            ["%08x" % v for v in ret_chain]))
                        if self._dump_ring:
                            self._dump_ring()
                    except Exception as diag_error:
                        sys.stderr.write("CRASH frame=%d: %r (diag %r)\n"
                                         % (self.frame, exc, diag_error))
                    break
                if self.enemy_updates > 1:
                    self.enemy_update_audit.append(
                        (self.frame, self.enemy_updates))
                if self._shot_probe is not None and self.battle_first is not None:
                    self._shot_probe_frame(self.frame - self.battle_first)
                if (self.battle_first is None and self.rd32(SCENE_CUR) == 7
                        and self.per_frame_ops[-1]):
                    # scene7 首帧（本帧 title_frame 已完成）。零售次序
                    #（0x42c550:67/80）：scene7+0x14==0 帧 = 0x448ec0 启动 +
                    # update walk 消费文件帧 0。本窗消费发生在帧首
                    # （title_frame 之前），本帧已无法回溯插入——首个消费
                    # 帧定为下一帧（battle_first=self.frame+1），窗口帧号
                    # 重编后战斗帧 0 的输入=文件帧 0（相位成对保持；
                    # 绝对时轴差一帧=捕获起点差异，如实登记于输出面）。
                    # 窗口行数补齐（授权修复）：cut_battle_window 从检测
                    # 帧（battle_first-1，scene7 首帧本体）起算——零售首
                    # 战斗帧=scene7 首帧，本窗的检测帧即其对应物；该帧
                    # 无消费（零售在其 walk 内消费文件帧 0，本窗消费移至
                    # 帧首而该帧帧首先于 battle_first 置位——绝对差一帧
                    # 的登记面），其余帧 bf(N)↔file(N) 全对齐（600 帧窗
                    # 实测 edge_mismatches=0）。
                    self.battle_first = self.frame + 1
                    self._replay_first_pending = True
                    sys.stderr.write("BATTLE-FIRST frame=%d ops=%d "
                                     "consume_start=%d\n"
                                     % (self.frame, self.per_frame_ops[-1],
                                        self.battle_first))
                if self.battle_first is not None:
                    battle_out += 1
                    if self.enemy_updates == 0:
                        self.enemy_update_zero_frames += 1
                    if self._state_log is not None:
                        self._write_state_sample(self.frame - self.battle_first)
                    # 逐帧玩家轨迹落盘（死亡时间线离线分析：冻结段=死亡，
                    # 跳变回出生点=复活）。
                    if model is not None and getattr(self, "_pos_log", None):
                        player = self.rd32(0x004C22C4)
                        if player:
                            rx = struct.unpack(
                                "<f", bytes(self.uc.mem_read(
                                    player + 0x5B8, 4)))[0]
                            ry = struct.unpack(
                                "<f", bytes(self.uc.mem_read(
                                    player + 0x5BC, 4)))[0]
                            self._pos_log.write(
                                "%d %.3f %.3f\n"
                                % (self.frame - self.battle_first, rx, ry))
                    # M1.2 闭环验证：真玩家管理器轨迹 vs 事件窗验证过的
                    # python PlayerModel（同一输入流步进）——首分歧帧+偏差。
                    # 输入源=本帧原版 0x448200 消费产出（0x4e4c08），
                    # 不再旁路读文件（与真机消费同拍）。
                    if model is not None:
                        battle_f = self.frame - self.battle_first
                        state = self.rd32(INPUT_HELD) & 0xFFFF
                        model.step_move(state)
                        player = self.rd32(0x004C22C4)
                        if player:
                            rx = struct.unpack(
                                "<f", bytes(self.uc.mem_read(
                                    player + 0x5B8, 4)))[0]
                            ry = struct.unpack(
                                "<f", bytes(self.uc.mem_read(
                                    player + 0x5BC, 4)))[0]
                            if (abs(rx - model.x) > 0.02
                                    or abs(ry - model.y) > 0.02):
                                if len(model_misses) < 8:
                                    model_misses.append(
                                        (battle_f, round(rx, 3), round(ry, 3),
                                         round(model.x, 3),
                                         round(model.y, 3)))
                    if battle_out % 120 == 0:
                        sys.stderr.write(
                            "battle +%d (f%d) ops=%d 418e00=%d vm %d/%d "
                            "poolmalloc=%d bfile=%08x in=%08x shots=%d "
                            "(%.1fs)\n"
                            % (battle_out, self.frame, self.per_frame_ops[-1],
                               self.enemy_updates, self.vm_created,
                               self.vm_recycled, self.vm_pool_mallocs,
                               (lambda m: self.rd32(m + 0x9752B4)
                                if m else 0)(self.rd32(0x004C2174)),
                               self.rd32(0x004E49F0), self.shot_calls,
                               time.time() - t0))
                        t0 = time.time()
                    if battle_out >= frames:
                        break
        finally:
            out.close()
            if getattr(self, "_pos_log", None):
                self._pos_log.close()
            if getattr(self, "_state_log", None):
                self._state_log.close()
            if getattr(self, "_replay_log", None):
                self._replay_log.close()
        self.model_misses_out = model_misses
        self.replay_qualification = dict(
            consumed=self.replay_consumed,
            frames_requested=frames,
            edge_mismatches=list(self.replay_edge_mismatches),
            phase_samples=list(self.replay_phase_samples[:4])
            + list(self.replay_phase_samples[-4:]))
        return 0

    # ---------- 截窗与自证 ----------
    def cut_battle_window(self):
        """流式截窗；短窗与崩溃保留诊断数据，但不能作为成功产物。"""
        import tempfile
        first = None
        count = total_ops = min_ops = max_ops = 0
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=os.path.dirname(self.out_path) or ".",
                prefix=".battle-window-", delete=False) as output:
            temporary = output.name
            try:
                with open(self.out_path, "rb") as source:
                    for line in source:
                        row = json.loads(line)
                        frame = row["frame"]
                        if self.crash_frame is not None and frame >= self.crash_frame:
                            break
                        if first is None:
                            # 窗口起点=检测帧（battle_first-1，scene7 首帧
                            # 本体——零售首战斗帧对应物；授权修复：该帧无
                            # 消费属已登记绝对差一面，其余帧 bf(N)↔file(N)）
                            if not row["ops"] or frame < (self.battle_first or 0) - 1:
                                continue
                            first = frame
                        if frame != first + count:
                            self.fail("non-contiguous battle frame %d, expected %d"
                                      % (frame, first + count))
                        row["frame"] = count
                        n = len(row["ops"])
                        min_ops = min(min_ops, n) if count else n
                        max_ops = max(max_ops, n)
                        total_ops += n
                        for op in row["ops"]:
                            tex = op.get("tex")
                            name = tex[2] if tex else "(null)"
                            self.family_ops.setdefault(name, count)
                        output.write(json.dumps(row, ensure_ascii=True,
                                                separators=(",", ":")).encode("ascii")
                                     + b"\n")
                        count += 1
                        if count >= self.target_frames:
                            break
                if first is None:
                    self.fail("no battle frame with ops>0")
            except BaseException:
                os.unlink(temporary)
                raise
        os.replace(temporary, self.out_path)
        return {"battle_first_global": self.battle_first,
                "frames": count, "requested_frames": self.target_frames,
                "crash": self.crash_frame,
                "min_ops": min_ops, "max_ops": max_ops, "total_ops": total_ops,
                "gameover_first_global": getattr(self, "gameover_first", None),
                "enemy_update_zero_frames": getattr(self, "enemy_update_zero_frames", 0),
                "enemy_update_anomalies": self.enemy_update_audit[:8]}


def capture_exit_code(stats):
    # 回放沿失配（原版消费产出 vs 文件帧）与 crash/短窗/0x418e00 异常
    # 同级：带错位的捕获不能以 exit 0 自称完整（edge_mismatches 由
    # consume_replay_frame 逐帧对照 0x414f30 重算沿产出）。
    return int(stats["crash"] is not None
               or stats["frames"] != stats["requested_frames"]
               or bool(stats["enemy_update_anomalies"])
               or bool(stats.get("edge_mismatches")))


def main(argv):
    flags = {}
    positional = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--frames":
            i += 1
            flags["--frames"] = int(argv[i])
        elif arg == "--difficulty":
            i += 1
            flags["--difficulty"] = int(argv[i])
        elif arg == "--character":
            i += 1
            flags["--character"] = int(argv[i])
        elif arg == "--observe":
            flags["--observe"] = True
        elif arg == "--replay":
            i += 1
            flags["--replay"] = argv[i]
        elif arg == "--replay-stage":
            i += 1
            flags["--replay-stage"] = int(argv[i])
        else:
            positional.append(arg)
        i += 1
    exe = positional[0] if positional else "games/th13/th13.exe"
    raw_dir = positional[1] if len(positional) > 1 else "artifacts/reference/raw"
    out_path = (positional[2] if len(positional) > 2 else
                "artifacts/oracle-draws/st01-draws-full.jsonl")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if os.path.exists(out_path):
        import stat
        if not stat.S_ISREG(os.stat(out_path).st_mode):
            sys.stderr.write("battle-oracle output must be a regular file: %s\n"
                             % out_path)
            return 2

    oracle = BattleOracle(exe, raw_dir, out_path)
    if "--replay" in flags:
        from .replayfmt import ReplayFile
        replay_path = flags["--replay"]
        data = open(replay_path, "rb").read()
        oracle.replay = ReplayFile().decode(data)
        oracle._replay_file_name = os.path.basename(replay_path)
        stage_index = flags.get("--replay-stage", 0)
        if stage_index >= len(oracle.replay.stages):
            sys.stderr.write("--replay-stage out of range\n")
            return 2
        oracle.replay_stage = oracle.replay.stages[stage_index]
        oracle.difficulty = oracle.replay.rank & 3
        oracle.character = oracle.replay.character & 3
        oracle.shot_type = oracle.replay.shot_type
        oracle.target_frames = frames = flags.get(
            "--frames", min(1200, oracle.replay_stage.frame_count))
        sys.stderr.write("REPLAY %s rank=%d char=%d stage=%d frames=%d\n"
                         % (replay_path, oracle.difficulty,
                            oracle.character,
                            oracle.replay_stage.number,
                            oracle.replay_stage.frame_count))
    else:
        oracle.difficulty = flags.get("--difficulty", 1)
        oracle.character = flags.get("--character", 0)
        oracle.target_frames = frames = flags.get("--frames", 1200)
    oracle.observe = "--observe" in flags
    started = time.time()
    oracle.run(frames=frames)
    stats = oracle.cut_battle_window()
    qualification = getattr(oracle, "replay_qualification", None)
    if qualification is not None:
        # 回放沿失配进入退出码判定（capture_exit_code 同源）。
        stats["edge_mismatches"] = qualification["edge_mismatches"]
    print("battle-oracle battle_first=%s frames=%d crash=%s "
          "ops[min=%d max=%d total=%d] elapsed %.1fs" % (
              stats["battle_first_global"], stats["frames"], stats["crash"],
              stats["min_ops"], stats["max_ops"], stats["total_ops"],
              time.time() - started))
    if stats["enemy_update_anomalies"]:
        print("0x418e00 anomalies (frame,count): %s"
              % stats["enemy_update_anomalies"])
    if getattr(oracle, "_model_misses_shown", None) is None:
        oracle._model_misses_shown = True
    # run() 内收集的 model_misses 经属性透出（闭包变量在 run 返回后失效）
    for miss in getattr(oracle, "model_misses_out", [])[:8]:
        print("PLAYER-MODEL MISS battle_f=%d real=(%.3f,%.3f) model=(%.3f,%.3f)"
              % miss)
    families = sorted(oracle.family_ops.items(), key=lambda kv: kv[1])
    print("capture families (tex → first battle frame):")
    for name, at in families[:24]:
        print("  f%-5d %s" % (at, name))
    code = capture_exit_code(stats)
    print("CAPTURE status=%s requested_frames=%d captured_frames=%d crash=%s"
          % ("failed" if code else "complete", stats["requested_frames"],
             stats["frames"], stats["crash"]))
    if qualification is not None:
        print("REPLAY-QUALIFICATION consumed=%d frames_requested=%d "
              "edge_mismatches=%d phase=%s"
              % (qualification["consumed"], qualification["frames_requested"],
                 len(qualification["edge_mismatches"]),
                 qualification["phase_samples"][:2]))
        for miss in qualification["edge_mismatches"]:
            print("REPLAY-EDGE-MISS battle_f=%d got=(%s,%s,%s) "
                  "want=(%s,%s,%s)" % miss)
    print("SCENARIO gameover_first_global=%s enemy_update_zero_frames=%d "
          "replay_fidelity=unverified stage1_clear=unverified"
          % (stats["gameover_first_global"], stats["enemy_update_zero_frames"]))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
