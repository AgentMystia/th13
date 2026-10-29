#!/usr/bin/env python3
"""Read-only retail probe: Youmu Z charge clock / fire clock (004460d0 chain).

Ground truth = the retail binary itself: capstone disassembly of the actual
instructions (disasm/) plus Unicorn full-chain execution (BattleOracle window)
with key-state injection through the TO-verified GetKeyboardState stub path.
The port is never consulted as truth.

Instruction-level facts (disasm/004460d0.txt, capstone v5.0.7):
  - 004460d0 receives the player in EAX (caller 00443240 @0x443bfd: mov eax, esi).
  - player+0x146a0 charge clock {prev,cur,frac,rate_ptr,flags}; +0x146c8 fire clock.
  - charge tick 00401400(ESI=&146a0) every focused Z-held frame; VM 0x12 created
    at charge==0 (0x46f820, handle -> +0x146b4), recycled at charge==0x3c.
  - focused release: charge 1..59 -> set(0); >=0x3c -> set(-0x28) + origin save
    (+0x146b8/bc/c0); charge -0x28..-1 -> 0x446000(charge+0x28) spawned with
    player+0x5b8 temporarily swapped to the origin anchor; tick; set(0) at end.
  - fire clock: press -> set(0) + same-frame 0x446000(0); counters 0..14 spawn,
    at 14 with Z held 004732c0(-14.0 @0x4af028) rewinds (rate-scaled).
  - 004460d0 non-active branch (phase +0x65c != 1) only clears +0xa9d8/+0xa9dc.
  - caller gate 00443240 @0x443bc7: dialog *(0x4c2190)+0x198==0, enemy mgr
    0x4c2188+0xb8!=0, *(0x4c2194)+0x60 & 0x4000==0 (trance), +0x690 > 0x13;
    else reset branch pins fire {frac=-1.0f,cur=-1,prev=-2} and charge
    {frac=0,cur=0,prev=-1} (capstone 0x443c3e..0x443cae: charge cur gets
    ebx=0, not the decomp's 0xffffffff), frees +0x146b4 via 0x46fb90.

Outputs (ignored artifacts dir):
  artifacts/youmu-lunatic-20261029/charge/disasm/*.txt + sites.json
  artifacts/youmu-lunatic-20261029/charge/frames-<exp>.jsonl
  artifacts/youmu-lunatic-20261029/charge/events-<exp>.jsonl
  artifacts/youmu-lunatic-20261029/charge/analysis-<exp>.json
  artifacts/youmu-lunatic-20261029/charge/summary-<exp>.json

Usage (container /work):
  python3 tools/youmu_charge_probe.py --disasm
  python3 tools/youmu_charge_probe.py --exp youmu_hold_release
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "artifacts/youmu-lunatic-20261029/charge"

# --- retail anchors (functions.json + capstone disasm, see module docstring) ---
PLAYER_UPDATE = 0x00443240      # 00443240 __fastcall(player=ECX, ...) gate + call
CHARGE_FN = 0x004460D0          # 004460d0 (player=EAX) fire/charge body
CLOCK_TICK = 0x00401400         # 00401400 clock tick  (ESI = clock)
CLOCK_SET = 0x004016C0          # 004016c0 clock set(EAX = clock, [esp+4] = value)
CLOCK_TICK_BY = 0x004732C0      # 004732c0 tick-by (ESI = clock, f32 [esp+4])
SPAWN_DISPATCH = 0x00446000     # 00446000([esp+4]=counter, EDI=player)
SPAWN = 0x00445C40              # 00445c40(record, counter, pos*)
VM_CREATE = 0x0046F820          # 0046f820 generic ANM VM create
VM_RECYCLE = 0x0046FC30         # 0046fc30([esp+4]=VM handle) from 004460d0
VM_FREE = 0x0046FB90            # 0046fb90 used by the caller reset branch

PLAYER_A = 0x004C2194           # 0x4000 trance-flag carrier (GameInf slot)
PLAYER_B = 0x004C22C4           # manager deref'd +0x10 for VM create
ENEMY_MGR = 0x004C2188
DIALOG_MGR = 0x004C2190
INPUT_HELD = 0x004E4C08         # key struct +0x218 (Z bit 0x01, Shift bit 0x08)
INPUT_PREV = 0x004E4C0C
INPUT_PRESSED = 0x004E4C14
INPUT_RELEASED = 0x004E4C18
DAT_POWER = 0x004BE7E8
DAT_POWER_STEP = 0x004BE7F0
DAT_FLAGS830 = 0x004BE830
DAT_CHARACTER = 0x004BE7B8
DIALOG_FLAG = 0x198             # *(DIALOG_MGR) + 0x198: dialog-active gate

# clock struct (00401400/004016c0 disasm): +0 prev, +4 cur, +8 frac,
# +0xc rate ptr (&DAT_004c0a28), +0x10 flags bit0
CLOCK_CHARGE = 0x146A0          # player-relative: focus Z charge clock
CLOCK_FIRE = 0x146C8            # player-relative: Z fire clock
POS = 0x5B8                     # float x/y/z
PHASE = 0x65C                   # 0 startup, 1 active, ...
FOCUS = 0x14840                 # input bit3 (Shift) latched by 00442960
CHARGE_VM = 0x146B4             # charge effect VM handle
ORIGIN = 0x146B8                # origin anchor saved at charge >= 0x3c

VK = {"Z": 0x5A, "SHIFT": 0x10, "RIGHT": 0x27}


def f32(raw):
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


# --------------------------------------------------------------------------
# disasm mode
# --------------------------------------------------------------------------
def pe_va_reader():
    from oracle.pe import PE
    pe = PE((ROOT / "games/th13/th13.exe").read_bytes())
    base = pe.image_base

    def va2off(va):
        rva = va - base
        for s in pe.sections:
            if s.va <= rva < s.va + s.raw_size:
                return s.raw_offset + (rva - s.va)
        raise ValueError("va %08x not in section range" % va)
    return pe, va2off


def disasm_mode():
    """Capstone dump of the actual retail instructions (ground truth)."""
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    pe, va2off = pe_va_reader()
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    targets = {
        "004460d0": (CHARGE_FN, 1012),
        "00401400": (CLOCK_TICK, 88),
        "004016c0": (CLOCK_SET, 61),
        "004732c0": (CLOCK_TICK_BY, 160),
        "00446000": (SPAWN_DISPATCH, 193),
        "00443240": (PLAYER_UPDATE, 2857),
    }
    out_dir = OUT / "disasm"
    out_dir.mkdir(parents=True, exist_ok=True)
    sites = []
    for name, (va, size) in targets.items():
        code = pe.data[va2off(va):va2off(va) + size]
        insns = list(md.disasm(code, va))
        lines = ["%08x  %-8s %s" % (i.address, i.mnemonic, i.op_str)
                 for i in insns]
        (out_dir / (name + ".txt")).write_text("\n".join(lines) + "\n")
        for idx, i in enumerate(insns):
            if i.mnemonic == "call" and i.op_str.startswith("0x"):
                ctx = " | ".join("%s %s" % (j.mnemonic, j.op_str)
                                 for j in insns[max(0, idx - 8):idx])
                sites.append({"fn": name, "at": i.address,
                              "target": i.op_str, "setup": ctx})
    (out_dir / "sites.json").write_text(
        json.dumps({"schema": "th13/youmu-charge-disasm/v1",
                    "call_sites": sites}, indent=1) + "\n")
    interesting = [s for s in sites if s["fn"] == "004460d0"]
    print(json.dumps({"disasm_files": sorted(p.name for p in out_dir.iterdir()),
                      "charge_fn_call_sites": interesting}, indent=1))
    return 0


# --------------------------------------------------------------------------
# behavior mode
# --------------------------------------------------------------------------
class ChargeProbe:
    """BattleOracle subclass injecting a battle-frame key script.

    frame_keys is a property: BattleOracle.run assigns it every frame; the
    getter computes the scripted VK set for the current battle frame, so the
    retail packaging chain (0x471620 -> GetKeyboardState stub -> 0x414f30
    edges) produces the input triple exactly as with real keys."""

    def __init__(self, exe, raw, out_path):
        from oracle.battle_oracle import BattleOracle
        probe = self

        class _Probe(BattleOracle):
            @property
            def frame_keys(inner):
                if getattr(inner, "battle_first", None) is None:
                    return frozenset()
                bf = inner.frame - inner.battle_first
                if probe.mode == "script":
                    return probe.script.get(bf, frozenset())
                return probe.dynamic_keys(bf)

            @frame_keys.setter
            def frame_keys(inner, value):
                pass  # run()'s per-frame assignments; script property rules

        self.bo = _Probe(exe, raw, out_path)
        self.script = {}
        self.mode = "script"
        self.frames = 0
        self.dialog_dip = False
        self.dyn = {}               # release state machine (live charge read)
        self.events = None
        self.frames_log = None
        self.player = None
        self.counts = {}
        self.battle_frames_seen = 0

    # ---------- hooks ----------
    # BattleOracle.run() performs the full install_overrides/init_ptd/
    # install_d3d/patch_globals_title/install_hooks sequence itself; the
    # probe only adds its passive observers plus the audio-sink preparation.
    def install(self):
        self._install_audio_sink()
        self._install_probe_hooks()

    def _install_audio_sink(self):
        """SE-manager no-op sink (audio-domain policy, cf. battle_oracle's
        0x405a20 override). The Youmu charge path 0x446174..0x4461be makes two
        virtual calls *(0x4e0ef8)+0x40 / *(0x4e1150)+0x40 with (obj, int arg)
        pushed and no caller cleanup (stdcall ret 8 shape); the singletons are
        constructed by the sound system this headless window stubs out, so
        [obj] is unmapped and the first charge frame crashes (bf23 observed).
        The sink object provides a valid vtable with ret 8 at +0x40; clock,
        spawn and VM semantics under test are untouched. Recorded in the
        summary env_stubs field."""
        bo = self.bo
        m = bo.m
        before = {hex(a): bo.rd32(a) for a in (0x004E0EF8, 0x004E1150)}
        ret8 = m.malloc(4)
        m.uc.mem_write(ret8, b"\xC2\x08\x00")
        vtbl = m.malloc(0x80)
        m.uc.mem_write(vtbl, b"\x00" * 0x80)
        m.write_u32(vtbl + 0x40, ret8)
        obj = m.malloc(0x10)
        m.write_u32(obj, vtbl)
        m.write_u32(0x004E0EF8, obj)
        m.write_u32(0x004E1150, obj)
        self.env_stubs = {"se_manager_sink": {
            "why": "charge-path SE virtual calls 0x446198/0x4461be hit "
                   "unconstructed sound singletons in the headless window",
            "before": before, "sink_obj": "%08x" % obj,
            "method": "vtable+0x40 = ret 8"}}

    def _cnt(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1

    def _emit(self, record):
        self.events.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _bf(self):
        if self.bo.battle_first is None:
            return None
        return self.bo.frame - self.bo.battle_first

    def _snapshot(self, player):
        bo = self.bo
        rd = bo.rd32

        def clock(base):
            def signed(v):
                return v - 0x100000000 if v >= 0x80000000 else v
            return {"prev": signed(rd(player + base)),
                    "cur": signed(rd(player + base + 4)),
                    "frac_bits": rd(player + base + 8),
                    "frac": f32(rd(player + base + 8)),
                    "rate_ptr": rd(player + base + 0xC),
                    "flags": rd(player + base + 0x10)}
        return {
            "player": player,
            "phase": rd(player + PHASE),
            "pos": [f32(rd(player + POS)), f32(rd(player + POS + 4)),
                    f32(rd(player + POS + 8))],
            "charge": clock(CLOCK_CHARGE),
            "fire": clock(CLOCK_FIRE),
            "charge_vm": rd(player + CHARGE_VM),
            "origin": [f32(rd(player + ORIGIN)), f32(rd(player + ORIGIN + 4)),
                       f32(rd(player + ORIGIN + 8))],
            "focus": rd(player + FOCUS),
            "input": {"held": rd(INPUT_HELD) & 0xFFFF,
                      "prev": rd(INPUT_PREV) & 0xFFFF,
                      "pressed": rd(INPUT_PRESSED) & 0xFFFF,
                      "released": rd(INPUT_RELEASED) & 0xFFFF},
            "power": rd(DAT_POWER), "power_step": rd(DAT_POWER_STEP),
            "flags830": rd(DAT_FLAGS830),
        }

    def _install_probe_hooks(self):
        from unicorn import UC_HOOK_CODE
        from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_ECX,
                                       UC_X86_REG_ESI, UC_X86_REG_ESP)
        bo = self.bo
        probe = self
        uc = bo.uc

        def in_window():
            bf = probe._bf()
            return bf is not None and 0 <= bf < probe.frames

        def on_player_update(uc_, addr, size, ud):
            if not in_window():
                return
            player = uc_.reg_read(UC_X86_REG_ECX)
            if probe.player is None:
                probe.player = player
                sys.stderr.write("PROBE player=%08x gA=%08x gB=%08x\n" % (
                    player, bo.rd32(PLAYER_A), bo.rd32(PLAYER_B)))
            if player != probe.player:
                return
            bf = probe._bf()
            snap = probe._snapshot(player)
            probe.frames_log.write(json.dumps(
                {"bf": bf, "kind": "player_update_entry", **snap},
                separators=(",", ":")) + "\n")
            probe.battle_frames_seen += 1
        uc.hook_add(UC_HOOK_CODE, on_player_update,
                    begin=PLAYER_UPDATE, end=PLAYER_UPDATE)

        def clock_tag(ptr):
            if probe.player is None:
                return "unknown"
            delta = ptr - probe.player
            if delta == CLOCK_CHARGE:
                return "charge@146a0"
            if delta == CLOCK_FIRE:
                return "fire@146c8"
            if 0 <= delta < 0x18000:
                return "player+%x" % delta
            return "ext-%08x" % ptr

        def on_clock_set(uc_, addr, size, ud):
            if not in_window():
                return
            esp = uc_.reg_read(UC_X86_REG_ESP)
            ptr = uc_.reg_read(UC_X86_REG_EAX)
            value = struct.unpack("<i", bytes(uc_.mem_read(esp + 4, 4)))[0]
            tag = clock_tag(ptr)
            probe._cnt("clock_set_" + tag)
            probe._emit({"bf": probe._bf(), "kind": "clock_set",
                         "clock": tag, "ptr": ptr, "value": value})
        uc.hook_add(UC_HOOK_CODE, on_clock_set, begin=CLOCK_SET, end=CLOCK_SET)

        def on_clock_tick(uc_, addr, size, ud):
            if not in_window():
                return
            ptr = uc_.reg_read(UC_X86_REG_ESI)
            tag = clock_tag(ptr)
            probe._cnt("clock_tick_" + tag)
            probe._emit({"bf": probe._bf(), "kind": "clock_tick",
                         "clock": tag, "ptr": ptr})
        uc.hook_add(UC_HOOK_CODE, on_clock_tick, begin=CLOCK_TICK, end=CLOCK_TICK)

        def on_clock_tick_by(uc_, addr, size, ud):
            if not in_window():
                return
            esp = uc_.reg_read(UC_X86_REG_ESP)
            ptr = uc_.reg_read(UC_X86_REG_ESI)
            arg = f32(struct.unpack("<I", bytes(uc_.mem_read(esp + 4, 4)))[0])
            tag = clock_tag(ptr)
            probe._cnt("clock_tick_by_" + tag)
            probe._emit({"bf": probe._bf(), "kind": "clock_tick_by",
                         "clock": tag, "ptr": ptr, "delta": arg})
        uc.hook_add(UC_HOOK_CODE, on_clock_tick_by, begin=CLOCK_TICK_BY,
                    end=CLOCK_TICK_BY)

        def on_spawn_dispatch(uc_, addr, size, ud):
            if not in_window():
                return
            esp = uc_.reg_read(UC_X86_REG_ESP)
            counter = struct.unpack("<i", bytes(uc_.mem_read(esp + 4, 4)))[0]
            ret = bo.rd32(esp)
            probe._cnt("spawn_dispatch")
            probe._emit({"bf": probe._bf(), "kind": "spawn_dispatch",
                         "counter": counter, "caller": ret,
                         "power": bo.rd32(DAT_POWER),
                         "power_step": bo.rd32(DAT_POWER_STEP),
                         "flags830": bo.rd32(DAT_FLAGS830)})
        uc.hook_add(UC_HOOK_CODE, on_spawn_dispatch,
                    begin=SPAWN_DISPATCH, end=SPAWN_DISPATCH)

        def on_spawn(uc_, addr, size, ud):
            if not in_window():
                return
            esp = uc_.reg_read(UC_X86_REG_ESP)
            record = bo.rd32(esp + 4)
            counter = struct.unpack("<i", bytes(uc_.mem_read(esp + 8, 4)))[0]
            pos_ptr = bo.rd32(esp + 0xC)
            pos = [f32(bo.rd32(pos_ptr)), f32(bo.rd32(pos_ptr + 4))]
            interval = struct.unpack("<b", bytes(uc_.mem_read(record, 1)))[0]
            phase = struct.unpack("<b", bytes(uc_.mem_read(record + 1, 1)))[0]
            anchor = struct.unpack("<b", bytes(uc_.mem_read(record + 0x20, 1)))[0]
            probe._cnt("spawn")
            probe._emit({"bf": probe._bf(), "kind": "spawn", "record": record,
                         "counter": counter, "interval": interval,
                         "phase": phase, "anchor": anchor,
                         "pos_arg": pos, "pos_ptr": pos_ptr})
        uc.hook_add(UC_HOOK_CODE, on_spawn, begin=SPAWN, end=SPAWN)

        charge_lo, charge_hi = CHARGE_FN, CHARGE_FN + 1012

        def on_vm_create(uc_, addr, size, ud):
            if not in_window():
                return
            esp = uc_.reg_read(UC_X86_REG_ESP)
            ret = bo.rd32(esp)
            if not charge_lo <= ret < charge_hi:
                return
            probe._cnt("charge_vm_create")
            probe._emit({"bf": probe._bf(), "kind": "charge_vm_create",
                         "eax": uc_.reg_read(UC_X86_REG_EAX),
                         "caller": ret})
        uc.hook_add(UC_HOOK_CODE, on_vm_create, begin=VM_CREATE, end=VM_CREATE)

        def on_vm_recycle(uc_, addr, size, ud):
            if not in_window():
                return
            esp = uc_.reg_read(UC_X86_REG_ESP)
            ret = bo.rd32(esp)
            if not charge_lo <= ret < charge_hi:
                return
            handle = bo.rd32(esp + 4)
            probe._cnt("charge_vm_recycle")
            probe._emit({"bf": probe._bf(), "kind": "charge_vm_recycle",
                         "handle": handle, "caller": ret})
        uc.hook_add(UC_HOOK_CODE, on_vm_recycle, begin=VM_RECYCLE,
                    end=VM_RECYCLE)

        # 004460d0 entry: player arrives in EAX (0x443bfd: mov eax, esi).
        def on_charge_entry(uc_, addr, size, ud):
            if not in_window():
                return
            player = uc_.reg_read(UC_X86_REG_EAX)
            bf = probe._bf()
            if probe.player is None:
                probe.player = player
            if player != probe.player:
                probe._emit({"bf": bf, "kind": "charge_entry_other_obj",
                             "eax": player})
                return
            probe._cnt("charge_entries")
            probe._emit({"bf": bf, "kind": "charge_entry"})
        uc.hook_add(UC_HOOK_CODE, on_charge_entry, begin=CHARGE_FN,
                    end=CHARGE_FN)

        # dialog_interrupt experiment: retail reset-branch drive. The
        # 00443240 gate reads *(DIALOG_MGR)+0x198; dipping it for two battle
        # frames (once the charge clock is ramping) routes the caller into
        # the clock-reset else branch (the branch the trance flag 0x4000
        # also takes).
        if getattr(probe, "dialog_dip_dynamic", False):
            def on_dialog_dip(uc_, addr, size, ud):
                if not in_window():
                    return
                mgr = bo.rd32(DIALOG_MGR)
                if not mgr:
                    return
                bf = probe._bf()
                if probe.dyn.get("dip_complete"):
                    return
                cur = probe._charge_cur()
                dips = probe.dyn.setdefault("dips", [])
                if probe.dyn.get("mode", "charge") == "charge" \
                        and cur is not None and cur >= 20:
                    if not dips or (len(dips) < 2 and bf == dips[-1] + 1):
                        bo.wr32(mgr + DIALOG_FLAG, 1)
                        dips.append(bf)
                        probe._emit({"bf": bf, "kind": "dialog_gate_set"})
                    if len(dips) >= 2:
                        probe.dyn["mode"] = "dip_done"
                elif probe.dyn.get("mode") == "dip_done" and bf > dips[-1]:
                    bo.wr32(mgr + DIALOG_FLAG, 0)
                    probe.dyn["mode"] = "charge"
                    probe.dyn["dip_complete"] = True
                    probe._emit({"bf": bf, "kind": "dialog_gate_cleared"})
            uc.hook_add(UC_HOOK_CODE, on_dialog_dip, begin=PLAYER_UPDATE,
                        end=PLAYER_UPDATE)

        # trance_interrupt experiment: same reset branch via the scene-flag
        # gate term *(0x4c2194)+0x60 & 0x4000 (00443240 0x443be5..0x443bf2).
        if getattr(probe, "trance_dip_dynamic", False):
            def on_trance_dip(uc_, addr, size, ud):
                if not in_window():
                    return
                carrier = bo.rd32(PLAYER_A)
                if not carrier:
                    return
                bf = probe._bf()
                if probe.dyn.get("dip_complete"):
                    return
                cur = probe._charge_cur()
                dips = probe.dyn.setdefault("dips", [])
                flags = bo.rd32(carrier + 0x60)
                if probe.dyn.get("mode", "charge") == "charge" \
                        and cur is not None and cur >= 20:
                    if not dips or (len(dips) < 2 and bf == dips[-1] + 1):
                        bo.wr32(carrier + 0x60, flags | 0x4000)
                        dips.append(bf)
                        probe._emit({"bf": bf, "kind": "trance_flag_set",
                                     "flags": "%08x" % (flags | 0x4000)})
                    if len(dips) >= 2:
                        probe.dyn["mode"] = "dip_done"
                elif probe.dyn.get("mode") == "dip_done" and bf > dips[-1]:
                    bo.wr32(carrier + 0x60, flags & ~0x4000)
                    probe.dyn["mode"] = "charge"
                    probe.dyn["dip_complete"] = True
                    probe._emit({"bf": bf, "kind": "trance_flag_cleared",
                                 "flags": "%08x" % (flags & ~0x4000)})
            uc.hook_add(UC_HOOK_CODE, on_trance_dip, begin=PLAYER_UPDATE,
                        end=PLAYER_UPDATE)

    # ---------- experiment drivers ----------
    # The 00443240 gate keeps 004460d0 off (and the caller reset branch pins
    # charge cur=-1) until the first stage enemy exists, so releases are
    # driven from the LIVE charge clock instead of fixed battle frames.
    def _charge_cur(self):
        if self.player is None:
            return None
        return self.bo.rd32(self.player + CLOCK_CHARGE + 4)

    def dynamic_keys(self, bf):
        Z, SH, RT = VK["Z"], VK["SHIFT"], VK["RIGHT"]
        names = {"Z": Z, "SHIFT": SH, "RIGHT": RT}
        mode = self.dyn.get("mode", "charge")
        cur = self._charge_cur()
        if mode in ("charge", "dip_done"):
            threshold = self.dyn.get("threshold", 62)
            if cur is not None and cur >= threshold:
                self.dyn.update(mode="released", release_bf=bf,
                                release_charge=cur)
                self._emit({"bf": bf, "kind": "script_release",
                            "charge": cur})
                return frozenset(names[k] for k in self.dyn["hold_after"])
            return frozenset(names[k] for k in self.dyn["hold_during"])
        if mode == "released":
            since = bf - self.dyn["release_bf"]
            if since == self.dyn.get("move_after", 10):
                self.dyn["mode"] = "refire"
                self.dyn["refire_bf"] = bf
                self._emit({"bf": bf, "kind": "script_refire"})
                return frozenset(names[k] for k in self.dyn["refire_keys"])
            return frozenset(names[k] for k in self.dyn["hold_after"])
        if mode == "refire":
            if bf - self.dyn["refire_bf"] >= self.dyn.get("refire_len", 22):
                self.dyn["mode"] = "done"
            return frozenset(names[k] for k in self.dyn["refire_keys"])
        return frozenset()

    def build_experiment(self, name):
        self.script = {}
        self.dialog_dip = False
        self.dyn = {}
        if name == "youmu_hold_release":
            # focused charge; release ONLY Z (Shift kept) once charge >= 62:
            # focused interrupt path 0x446208 with charge >= 0x3c -> set(-0x28)
            # + origin save, 40 tail frames (Shift only, stationary until the
            # tail ends), then unfocused Z refire with Right movement.
            self.mode = "dynamic"
            self.dyn = {"threshold": 62, "hold_during": ("Z", "SHIFT"),
                        "hold_after": ("SHIFT",), "move_after": 42,
                        "refire_keys": ("RIGHT", "Z"), "refire_len": 22}
            self.frames = 220
        elif name == "youmu_release_mid":
            # release ONLY Z at charge ~30 (< 0x3c): focused set(0), no tail
            self.mode = "dynamic"
            self.dyn = {"threshold": 30, "hold_during": ("Z", "SHIFT"),
                        "hold_after": ("SHIFT",), "move_after": 10 ** 6,
                        "refire_keys": (), "refire_len": 0}
            self.frames = 140
        elif name == "youmu_release_shift":
            # drop Shift (keep Z) at charge ~30: generic branch clears the
            # leftover charge and the fire clock starts the same frame
            self.mode = "dynamic"
            self.dyn = {"threshold": 30, "hold_during": ("Z", "SHIFT"),
                        "hold_after": ("Z",), "move_after": 10 ** 6,
                        "refire_keys": ("Z",), "refire_len": 40}
            self.frames = 160
        elif name == "reimu_hold":
            self.mode = "script"
            Z = VK["Z"]
            self.script = {bf: frozenset((Z,)) for bf in range(2, 100)}
            self.frames = 120
        elif name == "youmu_dialog_interrupt":
            # charge; at charge >= 20 dip the dialog gate for 2 frames while
            # still holding -> caller reset branch; then resume charge.
            self.mode = "dynamic"
            self.dyn = {"threshold": 10 ** 6, "hold_during": ("Z", "SHIFT"),
                        "hold_after": ("Z", "SHIFT"), "move_after": 10 ** 6,
                        "refire_keys": ("Z", "SHIFT"), "refire_len": 0}
            self.dialog_dip_dynamic = True
            self.frames = 160
        elif name == "youmu_trance_interrupt":
            # charge; at charge >= 20 set *(0x4c2194)+0x60 bit 0x4000 for two
            # frames (the trance/scene flag in the same gate) -> reset branch
            self.mode = "dynamic"
            self.dyn = {"threshold": 10 ** 6, "hold_during": ("Z", "SHIFT"),
                        "hold_after": ("Z", "SHIFT"), "move_after": 10 ** 6,
                        "refire_keys": ("Z", "SHIFT"), "refire_len": 0}
            self.trance_dip_dynamic = True
            self.frames = 160
        else:
            raise SystemExit("unknown experiment %r" % name)

    def run(self, name, frames_override=None):
        bo = self.bo
        self.build_experiment(name)
        if frames_override:
            self.frames = frames_override
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / ("events-%s.jsonl" % name)).open("w") as events, \
                (OUT / ("frames-%s.jsonl" % name)).open("w") as frames_log:
            self.events = events
            self.frames_log = frames_log
            self.install()
            bo.character = 0 if name == "reimu_hold" else 3
            bo.shot_type = 0
            bo.difficulty = 1
            bo.run(frames=self.frames)
        analysis = analyze(name)
        summary = {
            "experiment": name,
            "character": bo.character,
            "frames_requested": self.frames,
            "battle_frames_seen": self.battle_frames_seen,
            "crash_frame": bo.crash_frame,
            "battle_first_global": bo.battle_first,
            "player": "%08x" % (self.player or 0),
            "globals": {"004c2194": "%08x" % bo.rd32(PLAYER_A),
                        "004c22c4": "%08x" % bo.rd32(PLAYER_B),
                        "004c2188": "%08x" % bo.rd32(ENEMY_MGR),
                        "004c2190": "%08x" % bo.rd32(DIALOG_MGR)},
            "env_stubs": getattr(self, "env_stubs", {}),
            "counts": dict(sorted(self.counts.items())),
            "analysis": analysis,
        }
        return summary


# --------------------------------------------------------------------------
# machine analysis of the captured jsonl
# --------------------------------------------------------------------------
def load_jsonl(path):
    rows = []
    with open(path) as src:
        for line in src:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def analyze(name):
    """Compare the captured retail behavior against the disasm hypothesis.

    Anchors come from the recorded script transitions, not fixed frames:
    the 00443240 gate keeps charge cur=-1 until the first stage enemy, so
    every expected value is relative to the observed gate-open / release
    frames."""
    events = load_jsonl(OUT / ("events-%s.jsonl" % name))
    frames = load_jsonl(OUT / ("frames-%s.jsonl" % name))
    state = {r["bf"]: r for r in frames if r.get("bf") is not None}
    charge_state = {bf: r["charge"] for bf, r in state.items()}
    fire_state = {bf: r["fire"] for bf, r in state.items()}

    vm_create = [e["bf"] for e in events if e["kind"] == "charge_vm_create"]
    vm_recycle = [(e["bf"], e["handle"]) for e in events
                  if e["kind"] == "charge_vm_recycle"]
    charge_sets = [(e["bf"], e["value"]) for e in events
                   if e["kind"] == "clock_set" and e["clock"] == "charge@146a0"]
    fire_sets = [(e["bf"], e["value"]) for e in events
                 if e["kind"] == "clock_set" and e["clock"] == "fire@146c8"]
    fire_tick_by = [e for e in events if e["kind"] == "clock_tick_by"
                    and e["clock"] == "fire@146c8"]
    charge_ticks = [e["bf"] for e in events if e["kind"] == "clock_tick"
                    and e["clock"] == "charge@146a0"]
    dispatch = [(e["bf"], e["counter"]) for e in events
                if e["kind"] == "spawn_dispatch"]
    spawns = [e for e in events if e["kind"] == "spawn"]
    other_obj = [e for e in events if e["kind"] == "charge_entry_other_obj"]
    dips = [e for e in events if e["kind"].startswith("dialog_gate")]
    releases = [e for e in events if e["kind"] == "script_release"]

    checks = {}

    def check(key, ok, detail):
        checks[key] = {"ok": bool(ok), "detail": detail}
        return ok

    if name == "reimu_hold":
        counters = [c for bf, c in dispatch if bf >= 2]
        # caller reset branch pins charge {prev:-1,cur:0,frac:0} every gated
        # frame (capstone 0x443c9c..0x443cae: ebx=0), so the first gate-open
        # frame goes straight to the fire cycle 0..14 period 15
        if counters and counters[0] == 39:
            counters = counters[1:]
        expect = [i % 15 for i in range(len(counters))]
        check("fire_cycle_0_14_period15", counters == expect, counters[:20])
        check("fire_starts_at_counter0", bool(counters) and counters[0] == 0,
              counters[:4])
        check("no_charge_vm", not vm_create and not vm_recycle,
              {"create": vm_create, "recycle": vm_recycle})
        charge_vals = {charge_state[bf]["cur"] for bf in charge_state}
        check("charge_clock_never_positive",
              charge_vals <= {0, -1}, sorted(charge_vals))

    if name in ("youmu_hold_release", "youmu_release_mid",
                "youmu_release_shift", "youmu_dialog_interrupt"):
        # gate-open anchor: caller reset branch leaves charge cur=0, so the
        # first focused Z-held frame creates the VM and ticks to 1
        check("gate_open_vm_create", len(vm_create) >= 1, vm_create[:2])
        if vm_create:
            v0 = vm_create[0]
            # frames log = player-update ENTRY (before the same-frame tick):
            # cur(V)=0, cur(V+1)=1, ...
            ramp = [(bf, charge_state[bf]["cur"]) for bf in sorted(charge_state)
                    if v0 <= bf <= v0 + 30]
            ramp_ok = all(cur == bf - v0 for bf, cur in ramp)
            check("charge_ramp_cur_eq_bf_minus_vm0", ramp_ok, ramp[:10])
            check("pre_gate_charge_pinned",
                  all(charge_state[bf]["cur"] == 0
                      and charge_state[bf]["prev"] == -1
                      for bf in sorted(charge_state) if bf < v0),
                  "frames before %d pinned {prev:-1,cur:0}" % v0)

    if name == "youmu_hold_release":
        check("release_recorded", len(releases) == 1, releases)
        if releases:
            rel = releases[0]["bf"]
            check("release_charge_62", releases[0]["charge"] == 62,
                  releases[0])
            set40 = [e for e in charge_sets if e[1] == -40]
            check("release_set_minus40_next_frame",
                  len(set40) == 1 and set40[0][0] == rel, set40)
            origin0 = state.get(rel + 1, {}).get("origin")
            pos_rel = state.get(rel, {}).get("pos")
            check("origin_saved_from_pos_at_release",
                  origin0 is not None and pos_rel is not None
                  and origin0 == pos_rel,
                  {"origin": origin0, "pos@release": pos_rel})
            tail = [(bf, c) for bf, c in dispatch
                    if rel < bf <= rel + 41]
            tail_counters = [c for _, c in tail]
            tail_spawns = [e for e in spawns if rel < e["bf"] <= rel + 41]
            moved = state.get(rel + 50, {}).get("pos")
            check("tail_40_frames_counters_0_39",
                  tail_counters == list(range(40)), tail_counters)
            pos_matches = bool(origin0) and tail_spawns and all(
                e["pos_arg"] == origin0[:2] for e in tail_spawns)
            check("tail_spawn_pos_eq_origin_while_moving",
                  pos_matches and moved is not None
                  and moved[0] != origin0[0],
                  {"origin": origin0[:2],
                   "player_x@rel+50": moved[0] if moved else None,
                   "spawn_count": len(tail_spawns),
                   "mismatch_count": len([e for e in tail_spawns
                                          if e["pos_arg"] != origin0[:2]])})
            set0_end = [e for e in charge_sets
                        if e[1] == 0 and e[0] == rel + 40]
            check("tail_end_set0_at_rel_plus_40", len(set0_end) == 1,
                  set0_end)
            vm_rec = [bf for bf, h in vm_recycle]
            check("vm_recycled_at_post_tick_60",
                  len(vm_rec) == 1
                  and charge_state.get(vm_rec[0] + 1, {}).get("cur") == 60,
                  {"recycle_bf": vm_rec,
                   "cur@rec+1": charge_state.get(vm_rec[0] + 1, {}).get("cur")
                   if vm_rec else None})
            check("charge_counts_past_60_to_release",
                  all(charge_state[bf]["cur"] == bf - vm_create[0]
                      for bf in range(vm_create[0],
                                      releases[0]["bf"] + 1)
                      if bf in charge_state),
                  [(bf, charge_state[bf]["cur"]) for bf in
                   (vm_rec[0], vm_rec[0] + 1, releases[0]["bf"])
                   if bf in charge_state])
            # unfocused refire: fire set(0) then counters 0..14, wrap -14
            fire0 = [bf for bf, v in fire_sets
                     if v == 0 and bf > releases[0]["bf"]]
            refire = [c for bf, c in dispatch if bf > releases[0]["bf"] + 41]
            check("refire_fire_set0", len(fire0) == 1, fire0)
            check("refire_counters_start_0_and_cycle",
                  bool(refire) and refire[0] == 0,
                  refire[:18])
            check("fire_wrap_tick_by_minus14",
                  bool(fire_tick_by)
                  and all(e["delta"] == -14.0 for e in fire_tick_by),
                  [(e["bf"], e["delta"]) for e in fire_tick_by[:4]])

    if name == "youmu_release_mid":
        check("release_recorded", len(releases) == 1, releases)
        if releases:
            rel = releases[0]["bf"]
            check("release_charge_30", releases[0]["charge"] == 30,
                  releases[0])
            set0 = [e for e in charge_sets if e[1] == 0 and e[0] == rel]
            check("midcharge_release_set0_same_frame", len(set0) == 1, set0)
            check("no_vm_recycle_before_60", not vm_recycle, vm_recycle)
            after = [c for bf, c in dispatch if bf >= rel]
            check("no_spawn_after_release", not after, after)
            check("charge_stays_0",
                  all(charge_state[bf]["cur"] == 0
                      for bf in range(rel + 1,
                                      min(rel + 21, max(charge_state) + 1))
                      if bf in charge_state),
                  [(bf, charge_state[bf]["cur"]) for bf in
                   range(rel + 1, rel + 7) if bf in charge_state])
            check("fire_clock_untouched",
                  not [bf for bf, v in fire_sets if v == 0], fire_sets)

    if name == "youmu_release_shift":
        check("release_recorded", len(releases) == 1, releases)
        if releases:
            rel = releases[0]["bf"]
            set0 = [e for e in charge_sets if e[1] == 0 and e[0] == rel]
            check("unfocus_clears_charge_set0_same_frame", len(set0) == 1,
                  set0)
            fire0 = [bf for bf, v in fire_sets if v == 0 and bf == rel]
            first = [c for bf, c in dispatch if bf == rel]
            check("fire_starts_same_frame_counter0",
                  bool(fire0) and first == [0],
                  {"fire_set0": fire0, "dispatch@rel": first})
            counters = [c for bf, c in dispatch if bf >= rel]
            expect = [i % 15 for i in range(len(counters))]
            check("fire_cycle_0_14_period15", counters == expect,
                  counters[:20])

    if name in ("youmu_dialog_interrupt", "youmu_trance_interrupt"):
        dip_kinds = ("dialog_gate",) if name == "youmu_dialog_interrupt" \
            else ("trance_flag",)
        dips = [e for e in events
                if any(e["kind"].startswith(k) for k in dip_kinds)]
        sets = [e["bf"] for e in dips if e["kind"].endswith("_set")]
        if sets:
            # reset applies DURING the first dip frame (gate read after the
            # entry hook); the reset drops charge to 0, so a one-frame dip
            # cannot re-arm — the pinned values at dip+1 are the evidence.
            after_dip = sets[0] + 1
            fire21 = fire_state.get(after_dip, {})
            check("gate_reset_fire_cur_minus1_prev_minus2_frac_minus1",
                  fire21.get("cur") == -1 and fire21.get("prev") == -2
                  and fire21.get("frac") == -1.0,
                  {"fire@dip+1": fire21})
            ch = charge_state.get(after_dip, {})
            check("gate_reset_charge_cur0_prev_minus1_frac0",
                  ch.get("cur") == 0 and ch.get("prev") == -1
                  and ch.get("frac") == 0.0,
                  {"charge@dip+1": ch})
            check("gate_reset_clears_vm_handle",
                  state.get(after_dip, {}).get("charge_vm") == 0,
                  state.get(after_dip, {}).get("charge_vm"))
            post = [(bf, c) for bf, c in dispatch if bf > sets[0]]
            check("post_gate_no_spurious_spawn", not post, post[:6])

    first_contradiction = next(
        ({"check": k, **v} for k, v in checks.items() if not v["ok"]), None)
    blocked = None
    if name == "youmu_dialog_interrupt":
        blocked = ("mid-charge dialog-gate dip is not drivable in the "
                   "headless window: setting *(0x4c2190)+0x198 crashes the "
                   "frame walk at 0x428ae3 (dialog-flag consumers outside "
                   "the fire gate need a real dialog host). Reset-branch "
                   "semantics stand on capstone 0x443c09..0x443cec, the "
                   "pre-gate frame capture bf0..18 (same straight-line "
                   "writes every gated frame), and the trance-flag twin "
                   "experiment youmu_trance_interrupt.")
    return {"checks": checks, "first_contradiction": first_contradiction,
            "blocked": blocked,
            "anchors": {"vm_create": vm_create, "vm_recycle": vm_recycle,
                        "charge_ticks": len(charge_ticks),
                        "charge_sets": charge_sets,
                        "fire_sets": fire_sets,
                        "fire_tick_by": [(e["bf"], e["delta"])
                                         for e in fire_tick_by],
                        "dispatch": dispatch[:96], "spawns": len(spawns),
                        "other_obj_entries": len(other_obj),
                        "dialog_dips": dips,
                        "releases": releases}}


def aggregate():
    """Merge all experiment summaries + disasm facts into summary.json."""
    experiments = []
    for path in sorted(OUT.glob("summary-*.json")):
        if path.name == "summary.json":
            continue
        try:
            experiments.extend(json.loads(path.read_text())["experiments"])
        except Exception as exc:
            experiments.append({"experiment": path.stem, "error": str(exc)})
    sites = json.loads((OUT / "disasm/sites.json").read_text())["call_sites"]
    payload = {
        "schema": "th13/youmu-charge-probe/v1",
        "hypothesis": "H-YOUMU-CHARGE-CLOCK",
        "ground_truth": "retail th13.exe v1.00c (capstone disasm + unicorn "
                        "full-chain BattleOracle window); port not consulted",
        "disasm_call_sites_in_004460d0": [s for s in sites
                                          if s["fn"] == "004460d0"],
        "experiments": experiments,
        "verdicts": {
            "charge_clock_offsets": "player+0x146a0 {prev,cur,frac,rate_ptr,"
                                    "flags}; fire clock twin at +0x146c8",
            "focus_flag": "player+0x14840 = input bit3 (Shift), latched by "
                          "00442960 each frame",
            "charge_ramp": "tick +1/frame (00401400 ESI=&146a0) while "
                           "character==3 && focus && Z held",
            "vm_lifecycle": "0x46f820 script 0x12 created at charge==0 "
                            "(handle +0x146b4), recycled via 0x46fc30 at "
                            "post-tick 60",
            "release": "focused release charge 1..59 -> set(0); >=60 -> "
                       "set(-40) + origin save +0x146b8/bc/c0",
            "tail": "40 frames: 0x446000(charge+40) with player+0x5b8 "
                    "swapped to origin, then set(0)",
            "fire_clock": "set(0) on Z press + same-frame 0x446000(0); "
                          "counters 0..14 spawn; 004732c0(-14.0) wrap at 14",
            "interruptions": "trance flag dip verified mid-charge; dialog "
                             "dip BLOCKED headless (0x428ae3); non-active "
                             "branch (+0x65c!=1) clears only +0xa9d8/a9dc "
                             "(disasm 0x4464aa..0x4464c3)",
        },
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(json.dumps({"written": str(OUT / "summary.json"),
                      "experiments": [e.get("experiment") for e in
                                      experiments]}, indent=1))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disasm", action="store_true")
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--exp", default=None)
    parser.add_argument("--frames", type=int, default=None)
    args = parser.parse_args()
    if args.disasm:
        return disasm_mode()
    if args.aggregate:
        return aggregate()
    if not args.exp:
        parser.error("need --disasm, --aggregate or --exp NAME")
    probe = ChargeProbe("games/th13/th13.exe", "artifacts/reference/raw",
                        str(OUT / "oracle-draws.jsonl"))
    summary = probe.run(args.exp, frames_override=args.frames)
    summary_path = OUT / ("summary-%s.json" % args.exp)
    summary_path.write_text(json.dumps(
        {"schema": "th13/youmu-charge-probe/v1",
         "hypothesis": "H-YOUMU-CHARGE-CLOCK", "experiments": [summary]},
        indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return int(probe.bo.crash_frame is not None)


if __name__ == "__main__":
    raise SystemExit(main())
