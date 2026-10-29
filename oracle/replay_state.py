"""Raw, post-frame replay observations; addresses are not portability claims.

The offsets follow 00449070 (restored globals), 00422b20 (enemy life and
damage), and the existing battle shot probe. Float values are emitted as
their original little-endian u32 bits. The frame before the first manual replay
consume is -1; the current harness's phase is not certified as retail.
"""
import struct

SCHEMA = "th13-state-raw-v2"
GLOBAL_ADDRESSES = (*range(0x004BE7B8, 0x004BE818, 4), 0x004BE830,
                    0x004DC31C, 0x004DC320, 0x004DC324, 0x004DC328)
CORE = (0x004BE7B8, 0x004BE7C0, 0x004BE7C4, 0x004BE7C8,
        0x004BE7D0, 0x004BE7D4, 0x004BE7DC, 0x004BE7E4,
        0x004BE7E8, 0x004BE7EC, 0x004BE7F0, 0x004BE7F4,
        0x004BE7F8, 0x004BE7FC, 0x004BE800, 0x004BE804,
        0x004BE808, 0x004BE810, 0x004BE814)


def semantic_sample(oracle, frame):
    read = oracle.rd32
    player = read(0x004C22C4)
    return {"schema": "th13-replay-core-v2", "frame": frame,
            # The stage counter is live state, not the immutable replay slot.
            # It advances during the transition before the next stage loads.
            "stage": read(0x004BE81C),
            "globals": {"%08x" % address: read(address) for address in CORE},
            "input": {name: read(address) for name, address in (
                ("held", 0x004E4C08), ("pressed", 0x004E4C14),
                ("released", 0x004E4C18))},
            "player": ({"x_bits": read(player + 0x5B8),
                        "y_bits": read(player + 0x5BC)} if player else None),
            "rng": {"game_seed": read(0x004DC324) & 0xFFFF,
                    "game_index": read(0x004DC328),
                    "visual_seed": read(0x004DC31C) & 0xFFFF,
                    "visual_index": read(0x004DC320)}}


def sample(oracle, frame):
    read = oracle.rd32
    mem = oracle.uc.mem_read
    player = read(0x004C22C4)
    ctl = read(0x004C2188)
    item_mgr = read(0x004C22A4)
    bullet_mgr = read(0x004C2174)
    gameinf = read(0x004C2194)
    enemies = []
    if ctl:
        node = read(ctl + 0xB0)
        seen = set()
        while node:
            if node in seen or len(seen) >= 20000:
                raise ValueError("enemy list cyclic or exceeds 20000 nodes")
            seen.add(node)
            base, node = read(node), read(node + 4)
            if base:
                vm = base + 0x11EC
                enemies.append({"slot": len(enemies),
                    "serial": read(base + 0x11E4),
                    "trace_id": read(base + 0x11E8), **{
                    "%x" % offset: read(vm + offset)
                    for offset in (0x44, 0x48, 0x290, 0x3F4C)}})
    shots = []
    if player:
        pool = bytes(mem(player + 0x6A0, 256 * 0x9C))
        for slot in range(256):
            start = slot * 0x9C
            if struct.unpack_from("<I", pool, start + 0x70)[0]:
                shots.append({"slot": slot, **{
                    "%x" % offset: struct.unpack_from("<I", pool, start + offset)[0]
                    for offset in (0x2C, 0x30, 0x70)}})
    items = []
    if item_mgr:
        pool = bytes(mem(item_mgr + 0x14, 256 * 0x88))
        for slot in range(256):
            start = slot * 0x88
            if struct.unpack_from("<I", pool, start)[0]:
                items.append({"slot": slot, **{
                    "%x" % offset: struct.unpack_from("<I", pool, start + offset)[0]
                    for offset in (0x14, 0x18)}})
    bullets = []
    if bullet_mgr:
        # 0040d920 constructs 2001 slots, but 0040d170 marks the last
        # (+0x974b0e) state 6 as a sentinel, not an active bullet.
        pool = bytes(mem(bullet_mgr + 0x90, 0x7D1 * 0x135C))
        for slot in range(0x7D0):
            start = slot * 0x135C
            state = struct.unpack_from("<H", pool, start + 0xBBE)[0]
            if state:
                bullets.append({"slot": slot, "bbe": state, **{
                    "%x" % offset: struct.unpack_from("<I", pool, start + offset)[0]
                    for offset in (0xB70, 0xB74, 0xB78, 0xB7C, 0xB80,
                                   0xB84, 0xB88, 0xB8C, 0xBB4)},
                    "1354": struct.unpack_from("<H", pool, start + 0x1354)[0],
                    "1356": struct.unpack_from("<H", pool, start + 0x1356)[0]})
    damage_registrations = []
    if player:
        pool = bytes(mem(player + 0xA9E4, 256 * 0x9C))
        for slot in range(256):
            start = slot * 0x9C
            if struct.unpack_from("<I", pool, start + 0x10)[0] & 1:
                damage_registrations.append({"slot": slot, **{
                    "%x" % offset: struct.unpack_from("<I", pool, start + offset)[0]
                    for offset in (0x10, 0x14, 0x18, 0x1C, 0x24, 0x28, 0x2C, 0x30, 0x34,
                                   0x70, 0x74, 0x78, 0x84, 0x88, 0x8C, 0x90)}})
    return {"schema": SCHEMA, "frame": frame, "phase": "after-title-frame",
            "replay_consumed": oracle.replay_consumed,
            "current_stage": read(0x004BE81C),
            "scene": {"current": read(0x004DCC20),
                      "next": read(0x004DCC24),
                      "gameinf_pointer": gameinf,
                      "gameinf_flags": read(gameinf + 0x60) if gameinf else None},
            "input": {name: read(address) for name, address in (
                ("held", 0x004E4C08), ("pressed", 0x004E4C14),
                ("released", 0x004E4C18))},
            "globals": {"%08x" % address: read(address)
                        for address in GLOBAL_ADDRESSES},
            "player": ({"%x" % offset: read(player + offset)
                        for offset in (0x5B8, 0x5BC, 0x5C4, 0x5C8)}
                       if player else None),
            "enemies": enemies, "shots": shots, "spirits": items,
            "bullets": bullets, "damage_registrations": damage_registrations}
