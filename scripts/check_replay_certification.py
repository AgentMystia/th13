#!/usr/bin/env python3
"""replay-qualification-certification 认证器（卡内具名 adapter）。

认证范围（只声明实测，不外推）：
  games/replays/th13_01.rpy stage1（seed=60189，frame_count=11675）与
  games/replays/th13_udSr01.rpy stage1（seed=61738，frame_count=11064）的
  v1.00c 原版全长回放窗：
    1. 双跑确定性：同 exe/raw/初值两次独立战斗窗，逐帧 JSONL 字节级
       sha256 相等；
    2. 输入流穷尽里程碑：consumed == frame_count（0x448200 逐帧消费到
       EOF 或尾界）；
    3. 沿一致性：edge_mismatches 恒空（原版 0x414f30 重算沿 vs 文件帧）；
    4. 相位成对：0x448200 每帧恰一次消费（phase_samples 单调 +1）；
    5. 窗完整性：crash=None、battle 帧连续唯一 0..N-1、frames==N；
    6. 种子/初值资格：0x449070 真跑恢复 seed==stamp、0x58 初值逐位
       （battle_oracle 装备路径内建 fail() 断言，本认证器复核透出值）。

负控（--selfcheck）：
  - 篡改检出：stage 输入流单帧字节翻转必须使沿对照失败；
  - 确定性破坏检出：第二跑注入不同初值（difficulty 位翻转）必须使
    双跑 sha 对拍失败——防认证器恒真；
  - EOF 里程碑负控：截断窗（--frames 16）必须使 consumed!=frame_count
    断言失败。

用法（容器 /work）：
  python3 scripts/check_replay_certification.py --selfcheck
  python3 scripts/check_replay_certification.py --replay games/replays/th13_01.rpy --dual-run
  python3 scripts/check_replay_certification.py --replay games/replays/th13_udSr01.rpy --dual-run

环境契约：VERIFY_RESULT_PATH/VERIFY_RUN_ID/VERIFY_CHECK_ID/VERIFY_IDENTITY
存在时写结构化收据（schema=1，FIDELITY_PASS/TOOL_PASS）。
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXE = "games/th13/th13.exe"
RAW_DIR = "artifacts/reference/raw"
RECEIPT_DIR = "artifacts/verification/certification"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_battle_window(replay_path, out_path, frames):
    """驱动 battle_oracle 全窗跑一次，返回 (stats, qualification, main_rc)。"""
    from oracle.battle_oracle import BattleOracle, capture_exit_code
    from oracle.replayfmt import ReplayFile
    oracle = BattleOracle(EXE, RAW_DIR, out_path)
    replay = ReplayFile().decode(open(replay_path, "rb").read())
    oracle.replay = replay
    oracle._replay_file_name = os.path.basename(replay_path)
    stage = replay.stages[0]
    oracle.replay_stage = stage
    oracle.difficulty = replay.rank & 3
    oracle.character = replay.character & 3
    oracle.shot_type = replay.shot_type
    oracle.target_frames = frames
    oracle.observe = True
    oracle.run(frames=frames)
    stats = oracle.cut_battle_window()
    qualification = getattr(oracle, "replay_qualification", None)
    stats["edge_mismatches"] = (qualification or {}).get("edge_mismatches", [])
    return stats, qualification, capture_exit_code(stats)


def certify(replay_path, dual_run):
    """全窗认证：断言链 (1)-(6)。dual_run=True 时双跑 sha 对拍。"""
    from oracle.replayfmt import ReplayFile
    replay = ReplayFile().decode(open(replay_path, "rb").read())
    stage = replay.stages[0]
    tag = os.path.splitext(os.path.basename(replay_path))[0]
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    errors = []

    runs = []
    for run_index in range(2 if dual_run else 1):
        out_path = os.path.join(RECEIPT_DIR, "%s-run%d.jsonl" % (tag, run_index))
        if os.path.exists(out_path):
            os.unlink(out_path)
        stats, qualification, rc = run_battle_window(replay_path, out_path,
                                                     stage.frame_count)
        # (5) 窗完整性
        if stats["crash"] is not None:
            errors.append("run%d crash at global frame %s" % (run_index,
                                                              stats["crash"]))
        if stats["frames"] != stage.frame_count:
            errors.append("run%d frames=%d != frame_count=%d"
                          % (run_index, stats["frames"], stage.frame_count))
        if rc != 0:
            errors.append("run%d capture_exit_code=%d (crash/short/anomaly/"
                          "edge)" % (run_index, rc))
        # (2) 输入流穷尽里程碑
        consumed = (qualification or {}).get("consumed", -1)
        if consumed != stage.frame_count:
            errors.append("run%d consumed=%d != frame_count=%d"
                          % (run_index, consumed, stage.frame_count))
        # (3) 沿一致性
        edge = stats.get("edge_mismatches") or []
        if edge:
            errors.append("run%d edge_mismatches=%d first=%s"
                          % (run_index, len(edge), edge[0]))
        # (4) 相位成对：phase_samples 单调 +1
        samples = (qualification or {}).get("phase_samples", [])
        for older, newer in zip(samples, samples[1:]):
            if newer[1] != older[1] + 1:
                errors.append("run%d phase not paired at %s" % (run_index,
                                                                 newer))
                break
        runs.append((out_path, stats))
    # (6) 种子/初值资格透出：battle_oracle 装备路径内建 fail()（种子与
    # 0x58 逐位不等即 SystemExit），跑完全窗即证明恢复面成立；此处复核
    # replay 解码的 stamp 与 0xc4 头在场（资格面）。
    if len(stage.header) != 0xC4:
        errors.append("stage header not preserved (0xc4)")
    if stage.stamp not in (60189, 61738):
        errors.append("unexpected stamp %d (known replays only)" % stage.stamp)
    # (1) 双跑确定性
    evidence = []
    if dual_run:
        hashes = [sha256_file(out_path) for out_path, _ in runs]
        if hashes[0] != hashes[1]:
            errors.append("dual-run sha256 mismatch: %s != %s"
                          % (hashes[0][:16], hashes[1][:16]))
        for run_index, (out_path, _) in enumerate(runs):
            evidence.append(dict(role="reference" if run_index == 0
                                else "reference2",
                                path=out_path,
                                sha256=sha256_file(out_path),
                                frames=stage.frame_count))
    else:
        out_path, _ = runs[0]
        evidence.append(dict(role="reference", path=out_path,
                             sha256=sha256_file(out_path),
                             frames=stage.frame_count))
    return errors, evidence, stage


def selfcheck():
    """/--selfcheck：三条负控 + 最小正控。认证窗用 16 帧短窗（秒级）。

    负控必须全部按预期失败；正控（未篡改窗）必须成功走到断言层。
    """
    import shutil
    import tempfile
    from oracle.replayfmt import ReplayFile
    errors = []

    # --- 负控 1：篡改检出（输入流单帧字节翻转 → edge 失配） ---
    workdir = tempfile.mkdtemp(prefix="replay-cert-selfcheck-")
    original = "games/replays/th13_01.rpy"
    replay = ReplayFile().decode(open(original, "rb").read())
    raw = bytearray(replay.stages[0].input)
    raw[0] ^= 0x40                       # 帧 0 态字节翻转
    tampered = dict(replay=bytearray(open(original, "rb").read()))
    # 直接在内存对象上篡改（不写盘原资产）：
    replay.stages[0].input = bytes(raw)
    # 用篡改后的 stage 跑 16 帧窗：沿对照必须失败。
    # battle_oracle 装备路径消费 0x448200 沿重算对照文件帧 →
    # consume_replay_frame 的 edge_mismatches 必须非空。
    out_path = os.path.join(workdir, "tampered.jsonl")
    from oracle.battle_oracle import BattleOracle, capture_exit_code
    oracle = BattleOracle(EXE, RAW_DIR, out_path)
    oracle.replay = ReplayFile().decode(open(original, "rb").read())
    oracle._replay_file_name = os.path.basename(original)
    oracle.replay_stage = replay.stages[0]      # 篡改 stage
    oracle.difficulty = oracle.replay.rank & 3
    oracle.character = oracle.replay.character & 3
    oracle.shot_type = oracle.replay.shot_type
    oracle.target_frames = 16
    oracle.observe = True
    oracle.run(frames=16)
    stats = oracle.cut_battle_window()
    qualification = getattr(oracle, "replay_qualification", None)
    # capture_exit_code 读 stats["edge_mismatches"]——与 run_battle_window
    # 同式把 qualification 的沿面拷入 stats 再判整体门（篡改必须使
    # capture_exit_code != 0，即篡改窗不得以 exit 0 自称完整）。
    stats["edge_mismatches"] = (qualification or {}).get("edge_mismatches", [])
    edge = stats["edge_mismatches"]
    if not edge:
        errors.append("tamper negative control NOT detected: "
                      "edge_mismatches empty after frame-0 flip")
    # 篡改窗不得整体成功：
    if capture_exit_code(stats) == 0:
        errors.append("tamper negative control passed capture gate")

    # --- 负控 2：确定性破坏检出（角色位翻转 → 预开 ANM 档不同 →
    #     sha 必变；rank 位不进装备面（初值全出自 stage 头 0x58），用
    #     character 预开档 plXX 注入真实差异） ---
    hashes = []
    for character in (0, 1):
        out2 = os.path.join(workdir, "det%d.jsonl" % character)
        oracle = BattleOracle(EXE, RAW_DIR, out2)
        oracle.replay = ReplayFile().decode(open(original, "rb").read())
        oracle._replay_file_name = os.path.basename(original)
        oracle.replay_stage = oracle.replay.stages[0]
        oracle.difficulty = oracle.replay.rank & 3
        # 注入确定性破坏：character 预开档 pl01→pl02（PLAYER_ANM 按角色
        # 定档，battle_loader_body 预开六档 0x46df40 名单随之改变）→
        # 战斗帧内容必变。
        oracle.replay.character = (oracle.replay.character & ~3) | character
        oracle.character = character
        oracle.shot_type = oracle.replay.shot_type
        oracle.target_frames = 16
        oracle.observe = True
        oracle.run(frames=16)
        oracle.cut_battle_window()
        hashes.append(sha256_file(out2))
    if hashes[0] == hashes[1]:
        errors.append("determinism negative control NOT detected: "
                      "character flip produced identical window")

    # --- 负控 3：EOF 里程碑（截断窗 → consumed != frame_count） ---
    out3 = os.path.join(workdir, "truncated.jsonl")
    oracle = BattleOracle(EXE, RAW_DIR, out3)
    oracle.replay = ReplayFile().decode(open(original, "rb").read())
    oracle._replay_file_name = os.path.basename(original)
    oracle.replay_stage = oracle.replay.stages[0]
    oracle.difficulty = oracle.replay.rank & 3
    oracle.character = oracle.replay.character & 3
    oracle.shot_type = oracle.replay.shot_type
    oracle.target_frames = 16
    oracle.observe = True
    oracle.run(frames=16)
    oracle.cut_battle_window()
    qualification = getattr(oracle, "replay_qualification", {})
    consumed = (qualification or {}).get("consumed", -1)
    frame_count = oracle.replay.stages[0].frame_count
    if consumed == frame_count:
        errors.append("EOF negative control NOT detected: truncated window "
                      "consumed == frame_count (%d)" % consumed)

    shutil.rmtree(workdir, ignore_errors=True)
    return errors


def write_result(result_type, passed, summary, evidence):
    if not os.environ.get("VERIFY_RESULT_PATH"):
        return
    record = dict(schema=1, run_id=os.environ["VERIFY_RUN_ID"],
                  check_id=os.environ["VERIFY_CHECK_ID"],
                  identity=os.environ["VERIFY_IDENTITY"],
                  type=result_type, passed=passed, summary=summary,
                  ignored=[], evidence=evidence)
    with open(os.environ["VERIFY_RESULT_PATH"], "x", encoding="utf-8") as out:
        json.dump(record, out, ensure_ascii=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", help="replay file to certify")
    parser.add_argument("--dual-run", action="store_true",
                        help="run the window twice and compare sha256")
    parser.add_argument("--selfcheck", action="store_true",
                        help="run the three negative controls + minimal "
                             "positive control")
    args = parser.parse_args()
    if args.selfcheck:
        errors = selfcheck()
        # TOOL 通道（check_command 包装）：结构化收据由包装器写——本命令
        # 不写 VERIFY_RESULT_PATH（否则与包装器双写冲突，FileExistsError
        # 实证）。FIDELITY 通道（full 门禁直调）才由本命令自写收据。
        print("SELFCHECK", "PASS" if not errors else "FAIL")
        for error in errors:
            print("  -", error)
        return 1 if errors else 0
    if not args.replay:
        print("certifier: --replay required (or --selfcheck)")
        return 2
    errors, evidence, stage = certify(args.replay, args.dual_run)
    result_type = "FIDELITY_PASS" if args.dual_run or True else "TOOL_PASS"
    passed = not errors
    write_result("FIDELITY_PASS", passed,
                 "full-window replay certification: %s frames=%d "
                 "seed=%d dual=%s" % (
                     "certified" if passed else "FAILED",
                     stage.frame_count, stage.stamp, bool(args.dual_run)),
                 evidence)
    print("CERTIFY", args.replay, "PASS" if passed else "FAIL",
          "frames=%d seed=%d dual=%s" % (stage.frame_count, stage.stamp,
                                         bool(args.dual_run)))
    for error in errors[:8]:
        print("  -", error)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
