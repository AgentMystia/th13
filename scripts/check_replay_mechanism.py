"""Fixed retail Replay boundary experiments; not a full-world certificate."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oracle.machine import Machine, RET_MAGIC
from oracle.replayfmt import ReplayFile, xor_decrypt, zun_inflate, HEADER_SIZE, TOP_SIZE
from unicorn.x86_const import UC_X86_REG_EIP


def experiment(path):
    raw = path.read_bytes()
    replay = ReplayFile().decode(raw)
    body = xor_decrypt(raw[HEADER_SIZE:HEADER_SIZE + replay.comp_size], 0x5c, 0xe1, 0x400)
    body = xor_decrypt(body, 0x7d, 0x3a, 0x100)
    plain = zun_inflate(body, replay.uncomp_size)
    stage = replay.stages[0]
    header = bytes(plain[TOP_SIZE:TOP_SIZE + 0xc4])
    machine = Machine(str(ROOT / 'games/th13/th13.exe'))
    uc = machine.uc
    manager = machine.malloc(0x320)
    block = machine.malloc(0xc4 + len(stage.input) + len(stage.events))
    uc.mem_write(manager, bytes(0x320))
    uc.mem_write(block, header + stage.input + stage.events)
    write = machine.write_u32
    read = machine.read_u32
    write(0x4c22c8, manager)
    write(0x4be81c, 1)
    write(manager + 0x10, 1)
    write(manager + 0x100, block)
    write(manager + 0xf0, block + 0xc4)
    write(manager + 0xf8, block + 0xc4 + len(stage.input))
    machine.call(0x449070)
    if uc.reg_read(UC_X86_REG_EIP) != RET_MAGIC:
        raise RuntimeError('retail restore did not return')
    if bytes(uc.mem_read(0x4be7b8, 0x58)) != header[0x14:0x6c]:
        raise RuntimeError('retail initial-state byte restore differs')
    if read(0x4dc324) & 0xffff != stage.stamp or read(0x4dc31c) & 0xffff != stage.stamp:
        raise RuntimeError('retail stage seed restore differs')
    observed = []
    write(manager + 0x218, 1)
    write(manager + 0x104, 0)
    write(0x4c2194, 1)
    for frame in range(stage.frame_count):
        expected = stage.frame(frame)
        if expected == (0xffff, 0xffff, 0xffff):
            break
        machine.call(0x448200, regs={'esi': manager})
        if uc.reg_read(UC_X86_REG_EIP) != RET_MAGIC:
            raise RuntimeError('retail input consumer did not return')
        actual = tuple(read(address) for address in (0x4e4c08, 0x4e4c14, 0x4e4c18))
        if actual != expected or read(manager + 0x104) != frame + 1:
            raise RuntimeError('six-byte input or cursor differs at frame %d' % frame)
        observed.append(struct.pack('<HHH', *actual))
    if not observed:
        raise RuntimeError('no original input consumption')
    # Reconstruct the edge path with the retail calculator itself, not PlayerModel.
    uc.mem_write(0x4e49f0, bytes(0x234))
    previous = 0
    for frame in range(len(observed)):
        held, pressed, released = stage.frame(frame)
        write(0x4e4c08, held)
        write(0x4e4c0c, previous)
        machine.call(0x414f30, regs={'ecx': 0x4e49f0})
        if (read(0x4e4c14), read(0x4e4c18)) != (pressed, released):
            raise RuntimeError('retail derived edge differs at frame %d' % frame)
        previous = held
    return dict(replay=str(path.relative_to(ROOT)), sha256=hashlib.sha256(raw).hexdigest(),
                stage=stage.number, seed=stage.stamp, input_frames=len(observed),
                consumed_input_sha256=hashlib.sha256(b''.join(observed)).hexdigest(),
                restore_exact=True, input_consumer_exact=True, edge_calculator_exact=True)


def main():
    rows = [experiment(ROOT / 'games/replays' / name) for name in ('th13_01.rpy', 'th13_udSr01.rpy')]
    payload = dict(schema='th13/replay-mechanism/v1', experiments=rows,
                   qualification='isolated-retail-boundaries-only',
                   not_certified=['loader integration', 'game tick input sampling phase', 'natural Stage1 clear', 'framebuffer'])
    if os.environ.get('VERIFY_RESULT_PATH'):
        destination = Path(os.environ['VERIFY_RESULT_PATH'])
        evidence = destination.with_suffix('.experiment.json')
        evidence.write_text(json.dumps(payload, indent=2) + '\n')
        receipt = dict(schema=1, run_id=os.environ['VERIFY_RUN_ID'], check_id=os.environ['VERIFY_CHECK_ID'],
                       identity=os.environ['VERIFY_IDENTITY'], type='TOOL_PASS', passed=True,
                       summary='Retail restore, input consumer and derived-edge experiments; no world fidelity certification',
                       ignored=[], evidence=[])
        with destination.open('x') as output:
            json.dump(receipt, output)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
