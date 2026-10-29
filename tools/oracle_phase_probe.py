"""Passive frame-by-frame scene and replay-task qualification probe."""
import json
import sys
from oracle.battle_oracle import BattleOracle, GAMEINF, REPLAY_MGR, SCENE_CUR, SCENE_NEXT, main

original = BattleOracle.write_frame_line
def observed(self, out):
    original(self, out)
    if self.battle_first is None:
        return
    gi = self.rd32(GAMEINF)
    mgr = self.rd32(REPLAY_MGR)
    index = self.rd32(mgr + 0x218) if mgr else 0xffffffff
    task = self.rd32(mgr + 8) if mgr else 0
    row = {"frame": self.frame - self.battle_first, "scene": self.rd32(SCENE_CUR),
           "next_scene": self.rd32(SCENE_NEXT), "gameinf": gi,
           "game_flags": self.rd32(gi + 0x60) if gi else None,
           "life": self.rd32(0x004BE7F4), "replay_index": index,
           "task_flags": self.rd32(task + 4) if task else None,
           "replay_counter": self.rd32(mgr + 0x210) if mgr else None,
           "consumed": self.replay_consumed, "enemy_update": self.enemy_updates}
    if not hasattr(self, "_probe_stream"):
        self._probe_stream = open(self.diagnostic_dir + "/phase-probe.jsonl", "w")
    self._probe_stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    self._probe_stream.flush()
BattleOracle.write_frame_line = observed
sys.exit(main(sys.argv))
