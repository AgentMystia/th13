"""Entity snapshot comparison with explicit identity gates.

The retail pools and the web containers currently expose different identities:
enemy nodes are a linked-list order without a proven birth id, and the web
bullet/shot vectors compact on removal. This tool therefore never sorts by
coordinates or equates vector index with a retail slot. It compares only
already-proven keyed fields and reports the first unqualified identity as
BLOCKED instead of manufacturing a match.
"""
import json
import math
import struct
from pathlib import Path

SCHEMA = "th13-entity-compare-v1"


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def compare(native_path, web_path, limit=None, birth_path=None):
    native, web = load(native_path), load(web_path)
    # Retail includes the pre-input frame -1; Web collector starts after input0.
    # Match the explicitly labelled frames, never apply a numerical offset.
    native_by_frame = {row.get("frame"): row for row in native}
    native_frames = [row.get("frame") for row in native if row.get("frame", -1) >= 0]
    web_frames = [row.get("frame") for row in web]
    expected = list(range(len(web_frames)))
    if web_frames != expected or native_frames[:len(expected)] != expected:
        return {"schema": SCHEMA, "status": "BLOCKED", "frames": [{
            "status": "BLOCKED", "first_difference": {"field": "frame_coverage",
                "native": native_frames, "web": web_frames}}], "identity": {}}
    births = load(birth_path) if birth_path else []
    shot_slots = {}
    shot_sequence = 0
    bullet_slots = {}
    bullet_sequence = 0
    birth_index = 0
    rows = []
    for frame, right in enumerate(web):
        if limit is not None and frame >= limit:
            break
        left = native_by_frame.get(right.get("frame"))
        if left is None:
            rows.append({"frame": frame, "status": "BLOCKED", "first_difference": {
                "field": "frame", "native": "missing", "web": right.get("frame")}})
            break
        original = {item["slot"]: item for item in left.get("spirits", left.get("items", []))}
        port = {item["slot"]: item for item in right.get("spirits", [])}
        while birth_index < len(births) and births[birth_index]["frame"] <= left.get("frame", frame):
            event = births[birth_index]
            if event["kind"] == "player_shot":
                shot_sequence += 1
                shot_slots[event["slot"]] = shot_sequence
            elif event["kind"] == "bullet":
                bullet_sequence += 1
                bullet_slots[event["slot"]] = bullet_sequence
            birth_index += 1
        difference = None
        enemy_left = left.get("enemies", [])
        enemy_right = right.get("enemies", [])
        if all("serial" in e for e in enemy_left):
            native_enemies = {e["serial"]: e for e in enemy_left}
            web_enemies = {e["serial"]: e for e in enemy_right}
            if len(native_enemies) != len(enemy_left) or len(web_enemies) != len(enemy_right):
                difference = {"field": "enemies.identity", "native": "duplicate serial", "web": "duplicate serial"}
            elif native_enemies.keys() != web_enemies.keys():
                difference = {"field": "enemies.serials", "native": sorted(native_enemies), "web": sorted(web_enemies)}
            else:
                for serial in native_enemies:
                    ne, we = native_enemies[serial], web_enemies[serial]
                    for nk, wk in (("44", "x_bits"), ("48", "y_bits"), ("trace_id", "trace_id")):
                        if ne[nk] != we[wk]:
                            difference = {"field": f"enemies[{serial}].{wk}", "native": ne[nk], "web": we[wk]}
                            break
                    if difference:
                        break
                    # Port life is float; original inner+3f4c is signed int.
                    # Exact integral values can be compared semantically, while
                    # fractional/overflow values are a real mismatch.
                    value = struct.unpack('<f', struct.pack('<I', we['life_bits']))[0]
                    native_life = struct.unpack('<i', struct.pack('<I', ne['3f4c']))[0]
                    if not math.isfinite(value) or value != native_life:
                        difference = {"field": f"enemies[{serial}].life", "native": native_life, "web": value}
                        break
        if birth_path and not difference:
            native_shots = {shot_slots[s["slot"]]: s for s in left.get("shots", []) if s["slot"] in shot_slots}
            web_shots = {s["generation"]: s for s in right.get("shots", [])}
            if len(native_shots) != len(left.get("shots", [])):
                difference = {"field": "shots.identity", "native": "missing birth event", "web": None}
            elif native_shots.keys() != web_shots.keys():
                difference = {"field": "shots.generations", "native": sorted(native_shots), "web": sorted(web_shots)}
            else:
                for generation in native_shots:
                    for nk, wk in (("2c", "x_bits"), ("30", "y_bits")):
                        if native_shots[generation][nk] != web_shots[generation][wk]:
                            difference = {"field": f"shots[{generation}].{wk}",
                                          "native": native_shots[generation][nk], "web": web_shots[generation][wk]}
                            break
                    if difference:
                        break
        if birth_path and not difference:
            native_bullets = {bullet_slots[b["slot"]]: b for b in left.get("bullets", []) if b["slot"] in bullet_slots}
            web_bullets = {b["generation"]: b for b in right.get("bullets", [])}
            if len(native_bullets) != len(left.get("bullets", [])):
                difference = {"field": "bullets.identity", "native": "missing birth event", "web": None}
            elif native_bullets.keys() != web_bullets.keys():
                difference = {"field": "bullets.generations", "native": sorted(native_bullets), "web": sorted(web_bullets)}
            else:
                for generation in native_bullets:
                    for nk, wk in (("b70", "x_bits"), ("b74", "y_bits"), ("b8c", "angle_bits"), ("bbe", "state")):
                        if native_bullets[generation][nk] != web_bullets[generation][wk]:
                            difference = {"field": f"bullets[{generation}].{wk}",
                                          "native": native_bullets[generation][nk], "web": web_bullets[generation][wk]}
                            break
                    if difference:
                        break
        if not difference and original.keys() != port.keys():
            difference = {"field": "spirits.slots", "native": sorted(original), "web": sorted(port)}
        elif not difference:
            for slot in original:
                for native_key, web_key in (("14", "x_bits"), ("18", "y_bits")):
                    if original[slot][native_key] != port[slot][web_key]:
                        difference = {"field": f"spirits[{slot}].{web_key}",
                                      "native": original[slot][native_key], "web": port[slot][web_key]}
                        break
                if difference:
                    break
        rows.append({"frame": left.get("frame", frame), "status": "DIFFERENT" if difference else "COMPARED_FIELDS_MATCH",
                     "first_difference": difference,
                     "native_counts": {k: len(left.get(k, [])) for k in ("enemies", "bullets", "shots", "spirits")},
                     "web_counts": {k: len(right.get(k, [])) for k in ("enemies", "bullets", "shots", "spirits")}})
    return {"schema": SCHEMA, "status": "BLOCKED", "frames": rows,
            "identity": {"enemy": "retail-11e4-serial-if-present",
                          "bullet": "retail-slot-vs-web-generation-unproven",
                          "shot": "retail-slot-vs-web-compaction-unproven",
                          "item": "retail-spirit-pool-only"}}


def first_difference(native_path, web_path, birth_path=None):
    for row in compare(native_path, web_path, birth_path=birth_path)["frames"]:
        if row["first_difference"]:
            return row
    return {"status": "BLOCKED", "reason": "no spirit difference; other entity identities remain unproven"}
