#!/usr/bin/env python3
"""Read-only 446870/446f20 registration observation for Marisa replay."""
import json
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oracle.battle_oracle import BattleOracle, capture_exit_code
from oracle.replayfmt import ReplayFile
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EIP


class Probe(BattleOracle):
    def install_hooks(self):
        super().install_hooks()
        def observe(uc, address, size, data):
            frame = self.frame - self.battle_first if self.battle_first is not None else -1
            if not 84 <= frame <= 92:
                return
            player = self.rd32(0x4c22c4)
            if not player:
                return
            esp = uc.reg_read(UC_X86_REG_ESP)
            values = []
            for slot in range(256):
                base = player + 0xa9e4 + 0x9c * slot
                flags = self.rd32(base + 0x10)
                if not flags & 1:
                    continue
                readf = lambda off: struct.unpack('<f', bytes(uc.mem_read(base + off, 4)))[0]
                values.append({'slot': slot, 'flags': flags,
                    'center': [readf(0x2c), readf(0x30)], 'size': [readf(0x24), readf(0x28)],
                    'angle': readf(0x1c), 'previous': self.rd32(base + 0x70),
                    'current': self.rd32(base + 0x74), 'interval': self.rd32(base + 0x90),
                    'damage': self.rd32(base + 0x84)})
            arg = self.rd32(esp + 4)
            self.out.write(json.dumps({'frame': frame, 'event': 'query',
                'arg1': arg, 'enemy': list(struct.unpack('<2f', bytes(uc.mem_read(arg, 8)))),
                'registrations': values}) + '\n')
        self.uc.hook_add(UC_HOOK_CODE, observe, begin=0x446870, end=0x446870)
        def write(uc, access, address, size, value, data):
            frame = self.frame - self.battle_first if self.battle_first is not None else -1
            if not 88 <= frame <= 90:
                return
            player = self.rd32(0x4c22c4)
            if not player or address != player + 0xa9e4 + 2 * 0x9c + 0x24:
                return
            self.out.write(json.dumps({'frame': frame, 'event': 'size-write',
                'eip': uc.reg_read(UC_X86_REG_EIP), 'value': value & 0xffffffff}) + '\n')
        self.uc.hook_add(UC_HOOK_MEM_WRITE, write)


def main():
    output = Path('artifacts/th13-longrun-20261029/marisa-registration'); output.mkdir(parents=True, exist_ok=True)
    probe = Probe('games/th13/th13.exe', 'artifacts/reference/raw', str(output / 'draws.jsonl'))
    probe.replay = ReplayFile().decode(Path('games/replays/th13_udAs01.rpy').read_bytes())
    probe.replay_stage = probe.replay.stages[0]; probe._replay_file_name = 'th13_udAs01.rpy'
    probe.difficulty = probe.replay.rank & 3; probe.character = probe.replay.character & 3
    probe.shot_type = probe.replay.shot_type; probe.target_frames = 95
    with (output / 'registrations.jsonl').open('w') as handle:
        probe.out = handle; probe.run(95)
    stats = probe.cut_battle_window(); stats['edge_mismatches'] = probe.replay_qualification['edge_mismatches']
    print(json.dumps(stats)); return capture_exit_code(stats)


if __name__ == '__main__':
    raise SystemExit(main())
