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

from unicorn import UcError, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_EDI

from .title_oracle import (TitleOracle, SCENE_MGR, ANM_OPEN, ANM_MGR_CTOR,
                           BATTLE_PREOPEN)

SCENE_NEXT = SCENE_MGR + 0x584        # 场景 next（投递 7）
SCENE_CUR = SCENE_MGR + 0x580
GAMEINF = 0x004C2194                  # GameInf 指针（0x42c490 建 scene7 时写）
DAT_DIFFICULTY = 0x004BE7C4
DAT_CHARACTER = 0x004BE7B8
DAT_81C = 0x004BE81C
DAT_820 = 0x004BE820
DAT_STAGETABLE = 0x004C22DC
STAGE1_TABLE = 0x004BB56C
BATTLE_THREAD = 0x0042B990            # 战斗装载线程体（0x42bf90 thunk 目标）
ENEMY_UPDATE = 0x00418E00             # ECL 敌机逻辑（每帧恰一次自证）
MGR_LOADING = 0x20                    # anm mgr+0x20：装载等待环计数（>-1 忙）


class BattleOracle(TitleOracle):
    def __init__(self, exe_path, raw_dir, out_path):
        super().__init__(exe_path, raw_dir, out_path)
        self.window = "battle"
        self.input_script = {}             # 冒烟窗无输入（M1.2 接 u16 流）
        self.difficulty = 1                # th13_01 = Normal
        self.character = 0                 # th13_01 = ReimuA
        self.battle_requested = False
        self.battle_loader_started = False
        self.battle_first = None
        self.crash_frame = None
        self.enemy_updates = 0             # 当帧 0x418e00 入口计数
        self.enemy_update_audit = []       # (帧, 计数) 异常记录
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
        # BGM 变体启动（0x405a20）：战斗 f200 起把曲名 '.' 位替换 'b' 变体并
        # 0x43afc0 播放——曲名表由音频系统解析 thbgm.fmt 产生（桩环境缺席，
        # 名字空 → strchr('.') 返 0 → 0x405b1d 裸写崩，hud-capture-plan §5.2
        # 「曲名表读取」预言项）。音频域不在门禁内：按其 <200 帧早退分支语义
        # 桩为 ret -1（登记：跳过 DAT_004be830|=0x100 与 [esi+0x14] 位写——
        # 音频标志面，窗口若在此分歧再启用真表解析）。
        self.m.override_call(0x00405A20, lambda mm: 0xFFFFFFFF, ret_pop=4)
        # 待复评（随 M1.2/M2 按需拆除，先登记）：0x419a90（击杀/掉落/计分
        # 链——有输入窗需要真实掉落）、0x461cb0/0x462450（弹体视觉 ANM
        # 挂接/步进——「合成 mgr 无实数据」的前提在全链路不成立）。

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
        """标题态建立（scene current==4）首帧：预置对局参数 + 投递场景 7。"""
        if self.battle_requested:
            return
        if self.rd32(SCENE_CUR) != 4:
            return
        self.wr32(DAT_DIFFICULTY, self.difficulty)
        self.wr32(DAT_CHARACTER, self.character)
        self.wr32(DAT_81C, 1)
        self.wr32(DAT_820, 1)
        self.wr32(DAT_STAGETABLE, STAGE1_TABLE)
        self.wr32(SCENE_NEXT, 7)
        self.battle_requested = True
        sys.stderr.write("BATTLE-REQUEST frame=%d diff=%d char=%d\n"
                         % (self.frame, self.difficulty, self.character))

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
        # ① 预开六档（窗C 十五轮验证清单；0x46df40 在 0x180 预置下非阻塞）
        for slot, name in BATTLE_PREOPEN:
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
        try:
            for self.frame in range(0, frames + 900):
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
                if (self.battle_first is None and self.rd32(SCENE_CUR) == 7
                        and self.per_frame_ops[-1]):
                    self.battle_first = self.frame
                    sys.stderr.write("BATTLE-FIRST frame=%d ops=%d\n"
                                     % (self.frame, self.per_frame_ops[-1]))
                if self.battle_first is not None:
                    battle_out += 1
                    if battle_out % 120 == 0:
                        sys.stderr.write(
                            "battle +%d (f%d) ops=%d 418e00=%d vm %d/%d "
                            "poolmalloc=%d bfile=%08x (%.1fs)\n"
                            % (battle_out, self.frame, self.per_frame_ops[-1],
                               self.enemy_updates, self.vm_created,
                               self.vm_recycled, self.vm_pool_mallocs,
                               (lambda m: self.rd32(m + 0x9752B4)
                                if m else 0)(self.rd32(0x004C2174)),
                               time.time() - t0))
                        t0 = time.time()
                    if battle_out >= frames:
                        break
        finally:
            out.close()
        return 0

    # ---------- 截窗与自证 ----------
    def cut_battle_window(self):
        """窗=战斗首帧起固定帧数（场景 current==7 且 ops>0），帧号重编。"""
        rows = [json.loads(l) for l in
                open(self.out_path, "rb").read().splitlines()]
        first = next((i for i, r in enumerate(rows)
                      if r["ops"] and i >= (self.battle_first or 0)), None)
        if first is None:
            self.fail("no battle frame with ops>0")
        end = first + self.target_frames
        if self.crash_frame is not None:
            end = min(end, self.crash_frame)
        window = rows[first:end]
        for i, row in enumerate(window):
            row["frame"] = i
            for op in row["ops"]:
                tex = op.get("tex")
                name = tex[2] if tex else "(null)"
                if name not in self.family_ops:
                    self.family_ops[name] = i
        with open(self.out_path, "wb") as f:
            for row in window:
                f.write(json.dumps(row, ensure_ascii=True,
                                   separators=(",", ":")).encode("ascii")
                        + b"\n")
        ops = [len(r["ops"]) for r in window]
        return {"battle_first_global": self.battle_first,
                "frames": len(window), "crash": self.crash_frame,
                "min_ops": min(ops) if ops else 0,
                "max_ops": max(ops) if ops else 0, "total_ops": sum(ops),
                "enemy_update_anomalies": self.enemy_update_audit[:8]}


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
        else:
            positional.append(arg)
        i += 1
    exe = positional[0] if positional else "games/th13/th13.exe"
    raw_dir = positional[1] if len(positional) > 1 else "artifacts/reference/raw"
    out_path = (positional[2] if len(positional) > 2 else
                "artifacts/oracle-draws/st01-draws-full.jsonl")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    oracle = BattleOracle(exe, raw_dir, out_path)
    oracle.difficulty = flags.get("--difficulty", 1)
    oracle.character = flags.get("--character", 0)
    oracle.observe = "--observe" in flags
    oracle.target_frames = frames = flags.get("--frames", 1200)
    started = time.time()
    oracle.run(frames=frames)
    stats = oracle.cut_battle_window()
    print("battle-oracle battle_first=%s frames=%d crash=%s "
          "ops[min=%d max=%d total=%d] elapsed %.1fs" % (
              stats["battle_first_global"], stats["frames"], stats["crash"],
              stats["min_ops"], stats["max_ops"], stats["total_ops"],
              time.time() - started))
    if stats["enemy_update_anomalies"]:
        print("0x418e00 anomalies (frame,count): %s"
              % stats["enemy_update_anomalies"])
    families = sorted(oracle.family_ops.items(), key=lambda kv: kv[1])
    print("capture families (tex → first battle frame):")
    for name, at in families[:24]:
        print("  f%-5d %s" % (at, name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
