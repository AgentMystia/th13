#!/usr/bin/env python3
"""可执行正负控：0x405a20 灵界激活 + 0x405b80 恢复（真实零售函数）。

正控（必须全绿）：
  1. 运行 battle_oracle（真实 Replay 输入）后，0x461830 设备模型已按
     00460650/00460dd0 证据把 code1 预载名写入 0x4e3640+value*0x400，
     code2 把选中名写入 0x4e13c0（非空）。
  2. 以活状态直调 0x405a20（ECX=this、栈参=1）：返回、不 fault；
     DAT_004be830 |= 0x100、this+0x14 bit0、this+0x1c=0xffffffff、
     0x4e13c0 未被破坏；0x405a20 内 FUN_0043afc0 产生的 code9 请求名
     为 "<stage>_b.wav"（证明它消费的是真实模型曲名，不是旁路）。
  3. 以活状态直调 0x405b80（ECX=this、EDX=0）：返回、不 fault；
     DAT_004be830 bit0x100 清除、this+0x14 bit0 清除、0x4e13c0 保持非空。
负控（必须复现旧崩溃）：
  4. 清空 0x4e13c0、复位激活位后直调 0x405a20：必须在 0x405b1d
     （strchr('.') 返回 NULL 后的 `pcVar5[0]='b'`）fault；证明修复来自
     预载表设备状态而不是“跳过整函数/伪造曲名”。

证据写到 artifacts/verification/codex-recovery-20261029/fixes/oracle/。
用法（容器内 /work）：
    python3 tools/oracle_audio_control.py [--replay PATH] [--stage N]
                                          [--frames N] [--out PATH]
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn import UcError  # noqa: E402
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBP,  # noqa: E402
                               UC_X86_REG_ECX, UC_X86_REG_EDX,
                               UC_X86_REG_EIP, UC_X86_REG_ESP)
from oracle.battle_oracle import (BattleOracle, AUDIO_CURRENT_BGM,  # noqa: E402
                                  AUDIO_PRELOAD_SLOTS, AUDIO_PRELOAD_STRIDE,
                                  AUDIO_PRELOAD_TABLE)
from oracle.machine import RET_MAGIC  # noqa: E402
from oracle.replayfmt import ReplayFile  # noqa: E402

OUT_DIR = "artifacts/verification/codex-recovery-20261029/fixes/oracle"
TRANCE = 0x004C2164          # DAT_004c2164：灵界对象指针（0x405a20 this）
GAUGE = 0x004BE808           # DAT_004be808：灵界计（>=200 激活）
FLAGS = 0x004BE830           # DAT_004be830：bit0x100 灵界激活
ACTIVATE = 0x00405A20
RESTORE = 0x00405B80
CRASH_EIP = 0x00405B1D       # 空名 strchr('.') 返回 NULL 后的裸写


def rd32(oracle, address):
    return oracle.rd32(address)


def cstr(oracle, address, limit=256):
    return bytes(oracle.cstr(address, limit))


def snapshot(oracle, this):
    return {
        "e13c0": cstr(oracle, AUDIO_CURRENT_BGM),
        "be808": rd32(oracle, GAUGE),
        "be830": rd32(oracle, FLAGS),
        "this": [rd32(oracle, this + offset)
                 for offset in range(0, 0x40, 4)],
    }


def restore(oracle, this, state):
    oracle.uc.mem_write(AUDIO_CURRENT_BGM, state["e13c0"] + b"\0")
    oracle.wr32(GAUGE, state["be808"])
    oracle.wr32(FLAGS, state["be830"])
    for index, value in enumerate(state["this"]):
        oracle.wr32(this + index * 4, value)


def call_trance(oracle, address, this, arg=0, edx=0, timeout_ms=30_000):
    """在帧间隙直调 0x405a20/0x405b80（ECX=this；栈参 1 个；__fastcall）。"""
    uc = oracle.uc
    saved = {reg: uc.reg_read(reg) for reg in
             (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX,
              UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EIP)}
    esp = (0x002FF000 - 0x8000) & ~0xF
    uc.mem_write(esp, struct.pack("<I", RET_MAGIC))
    uc.mem_write(esp + 4, struct.pack("<I", arg & 0xFFFFFFFF))
    uc.mem_write(esp + 8, struct.pack("<I", 0xDEADBEEF))
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_EBP, 0x002FF000)
    uc.reg_write(UC_X86_REG_ECX, this)
    uc.reg_write(UC_X86_REG_EDX, edx)
    uc.reg_write(UC_X86_REG_EAX, 0)
    try:
        uc.emu_start(address, RET_MAGIC, timeout=timeout_ms * 1000)
        result = {"status": "ok",
                  "eax": "%08x" % uc.reg_read(UC_X86_REG_EAX)}
    except UcError as exc:
        result = {"status": "fault", "error": str(exc),
                  "eip": "%08x" % uc.reg_read(UC_X86_REG_EIP)}
    finally:
        for reg, value in saved.items():
            uc.reg_write(reg, value)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", default="games/replays/th13_01.rpy")
    parser.add_argument("--stage", type=int, default=0)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--out", default=os.path.join(
        OUT_DIR, "405a20-control.json"))
    args = parser.parse_args(argv)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    replay = ReplayFile().decode(open(args.replay, "rb").read())
    stage = replay.stages[args.stage]
    oracle = BattleOracle("games/th13/th13.exe", "artifacts/reference/raw",
                          os.path.join(os.path.dirname(args.out),
                                       "405a20-draws.jsonl"))
    oracle.replay, oracle.replay_stage = replay, stage
    oracle.replay_stage_index = args.stage
    oracle._replay_file_name = os.path.basename(args.replay)
    oracle.difficulty = replay.rank & 3
    oracle.character = replay.character & 3
    oracle.shot_type = replay.shot_type
    oracle.target_frames = args.frames
    oracle.run(frames=args.frames)

    evidence = {"replay": args.replay, "stage": args.stage,
                "frames_requested": args.frames, "frame": oracle.frame,
                "crash_frame": oracle.crash_frame,
                "audio_requests": oracle.audio_requests[:40],
                "audio_diagnostics": oracle.audio_diagnostics[:20],
                "preload": [cstr(oracle, AUDIO_PRELOAD_TABLE + index *
                                 AUDIO_PRELOAD_STRIDE).decode("latin1")
                            for index in range(AUDIO_PRELOAD_SLOTS)],
                "current_bgm": cstr(oracle, AUDIO_CURRENT_BGM).decode("latin1"),
                "trance_ptr": "%08x" % rd32(oracle, TRANCE),
                "gauge": rd32(oracle, GAUGE),
                "flags_before": "%08x" % rd32(oracle, FLAGS)}
    checks = {}
    this = rd32(oracle, TRANCE)
    if oracle.crash_frame is not None:
        evidence["verdict"] = "FAILED (oracle crashed)"
        checks["oracle_no_crash"] = False
    elif not this:
        evidence["verdict"] = "FAILED (DAT_004c2164 == 0)"
        checks["trance_object"] = False
    else:
        checks["oracle_no_crash"] = True
        checks["trance_object"] = True
        checks["preload_nonempty"] = bool(evidence["preload"][0])
        checks["current_bgm_nonempty"] = bool(evidence["current_bgm"])
        # 正控：激活（gauge 低于 200 时只抬阈值，不改曲名）。
        # param_1=0 是 0x4057f0 的手动灵界路径（bit1 不置位），因此
        # 0x405b80 的恢复分支不会经 0x444a10 重新激活；param_1=1 是
        # 死亡提交路径（bit1 置位），恢复时会重入 0x444a10→0x405a20，
        # 单列为零售链观察，不作为清除断言。
        forced = rd32(oracle, GAUGE) < 200
        if forced:
            oracle.wr32(GAUGE, 200)
        evidence["gauge_forced"] = forced
        before = snapshot(oracle, this)
        activation = call_trance(oracle, ACTIVATE, this, arg=0)
        after_activate = snapshot(oracle, this)
        evidence["activation"] = activation
        evidence["after_activate"] = {
            "be808": after_activate["be808"],
            "be830": "%08x" % after_activate["be830"],
            "this14": "%08x" % after_activate["this"][0x14 // 4],
            "this1c": "%08x" % after_activate["this"][0x1C // 4],
            "this20": "%08x" % after_activate["this"][0x20 // 4],
            "this24": "%08x" % after_activate["this"][0x24 // 4],
            "e13c0": after_activate["e13c0"].decode("latin1")}
        checks["activate_returned"] = activation["status"] == "ok"
        checks["activate_flag_100"] = bool(after_activate["be830"] & 0x100)
        checks["activate_this14_bit0"] = bool(
            after_activate["this"][0x14 // 4] & 1)
        checks["activate_this14_bit1_clear_manual"] = not (
            after_activate["this"][0x14 // 4] & 2)
        checks["activate_this1c_ffffffff"] = (
            after_activate["this"][0x1C // 4] == 0xFFFFFFFF)
        checks["activate_this20_24_zero"] = (
            after_activate["this"][0x20 // 4] == 0
            and after_activate["this"][0x24 // 4] == 0)
        checks["activate_name_preserved"] = (
            after_activate["e13c0"] == before["e13c0"])
        # 0x405a20 → FUN_0043afc0(name_b) → code9：请求名必须是 b 变体。
        variants = [row for row in oracle.audio_requests
                    if row[1] == 9 and row[0] == oracle.frame]
        evidence["variant_requests"] = variants[-4:]
        checks["activate_variant_b_wav"] = any(
            bytes.fromhex(row[3]).endswith(b"b.wav") for row in variants)
        # 恢复（0x405b80）。
        recovery = call_trance(oracle, RESTORE, this, arg=0)
        after_restore = snapshot(oracle, this)
        evidence["recovery"] = recovery
        evidence["after_restore"] = {
            "be808": after_restore["be808"],
            "be830": "%08x" % after_restore["be830"],
            "this14": "%08x" % after_restore["this"][0x14 // 4],
            "e13c0": after_restore["e13c0"].decode("latin1")}
        checks["restore_returned"] = recovery["status"] == "ok"
        checks["restore_flag_100_clear"] = not (
            after_restore["be830"] & 0x100)
        checks["restore_this14_bit0_clear"] = not (
            after_restore["this"][0x14 // 4] & 1)
        checks["restore_name_preserved"] = (
            after_restore["e13c0"] == before["e13c0"])
        checks["restore_gauge_consumed"] = after_restore["be808"] == 0
        # 死亡提交路径观察（param_1=1）：this+0x14 bit1 置位；0x405b80
        # 会经 0x444a10 重入 0x405a20（零售链），不是清除失败。
        oracle.wr32(GAUGE, 200)
        death = call_trance(oracle, ACTIVATE, this, arg=1)
        death_state = snapshot(oracle, this)
        death_recovery = call_trance(oracle, RESTORE, this, arg=0)
        death_after = snapshot(oracle, this)
        evidence["death_path"] = {
            "activate": death, "recovery": death_recovery,
            "this14_after_activate": "%08x" % death_state["this"][0x14 // 4],
            "be830_after_activate": "%08x" % death_state["be830"],
            "this14_after_recovery": "%08x" % death_after["this"][0x14 // 4],
            "be830_after_recovery": "%08x" % death_after["be830"]}
        checks["death_activate_bit1"] = bool(
            death_state["this"][0x14 // 4] & 2)
        checks["death_recovery_reenters_activation"] = bool(
            death_after["be830"] & 0x100)
        # 负控：恢复干净活状态后清空 0x4e13c0，必须复现 0x405b1d 崩溃。
        restore(oracle, this, before)
        oracle.uc.mem_write(AUDIO_CURRENT_BGM, b"\0")
        oracle.wr32(GAUGE, 200)
        negative = call_trance(oracle, ACTIVATE, this, arg=0)
        evidence["negative_empty_name"] = negative
        checks["negative_faults_at_405b1d"] = (
            negative["status"] == "fault"
            and int(negative.get("eip", "0"), 16) == CRASH_EIP)
        evidence["verdict"] = ("PASSED" if all(checks.values()) else "FAILED")
        # 负控已破坏活状态；不再恢复（进程内后续不再使用 oracle）。
        restore(oracle, this, before)
    evidence["checks"] = checks
    with open(args.out, "w") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=1)
    print("VERDICT", evidence["verdict"])
    for key in sorted(checks):
        print("  %-32s %s" % (key, "ok" if checks[key] else "FAIL"))
    print("WROTE", args.out)
    return 0 if evidence["verdict"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
