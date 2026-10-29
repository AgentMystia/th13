import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from oracle.replay_entities import compare, first_difference


class ReplayEntityTests(unittest.TestCase):
  def test_entity_comparison_refuses_unproven_identity(self):
    with TemporaryDirectory() as root:
        native = Path(root) / "native.jsonl"
        web = Path(root) / "web.jsonl"
        row = {"frame": 0, "enemies": [{"slot": 0}], "bullets": [{"slot": 1}], "shots": [], "items": []}
        native.write_text(json.dumps(row) + "\n")
        web.write_text(json.dumps(row) + "\n")
        result = compare(native, web)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["frames"][0]["status"], "COMPARED_FIELDS_MATCH")
        self.assertTrue(first_difference(native, web)["reason"])


  def test_spirit_slot_and_raw_bits_are_compared(self):
    with TemporaryDirectory() as root:
        native = Path(root) / "native.jsonl"
        web = Path(root) / "web.jsonl"
        native.write_text(json.dumps({"frame": 0, "items": [{"slot": 7, "14": 42, "18": 19}]}) + "\n")
        web.write_text(json.dumps({"frame": 0, "spirits": [{"slot": 7, "x_bits": 43, "y_bits": 19}]}) + "\n")
        first = first_difference(native, web)
        self.assertEqual(first["frame"], 0)
        self.assertEqual(first["first_difference"], {"field": "spirits[7].x_bits", "native": 42, "web": 43})


  def test_shot_identity_follows_observed_birth_order_not_slot_order(self):
    with TemporaryDirectory() as root:
        native, web, events = (Path(root) / name for name in ("native", "web", "events"))
        native.write_text(json.dumps({"frame": 0, "shots": [
            {"slot": 254, "2c": 2, "30": 4}, {"slot": 255, "2c": 1, "30": 3}]}) + "\n")
        web.write_text(json.dumps({"frame": 0, "shots": [
            {"generation": 1, "x_bits": 1, "y_bits": 3},
            {"generation": 2, "x_bits": 2, "y_bits": 4}]}) + "\n")
        events.write_text('\n'.join(json.dumps(x) for x in [
            {"frame": 0, "kind": "player_shot", "sequence": 0, "slot": 255},
            {"frame": 0, "kind": "player_shot", "sequence": 1, "slot": 254}]) + '\n')
        self.assertIsNone(compare(native, web, birth_path=events)["frames"][0]["first_difference"])


  def test_enemy_serial_survives_list_reordering_and_life_negative_control(self):
    import struct
    def fbits(value): return struct.unpack('<I', struct.pack('<f', value))[0]
    with TemporaryDirectory() as root:
        native, web = (Path(root) / name for name in ('native', 'web'))
        enemy = lambda serial, life: {"serial": serial, "trace_id": 0, "44": 10, "48": 20, "3f4c": life}
        n = {"frame": 0, "enemies": [enemy(2, 30), enemy(1, 40)]}
        w = {"frame": 0, "enemies": [
            {"serial": 1, "trace_id": 0, "x_bits": 10, "y_bits": 20, "life_bits": fbits(40)},
            {"serial": 2, "trace_id": 0, "x_bits": 10, "y_bits": 20, "life_bits": fbits(31)}]}
        native.write_text(json.dumps(n) + '\n'); web.write_text(json.dumps(w) + '\n')
        first = first_difference(native, web)
        self.assertEqual(first['first_difference'], {"field": "enemies[2].life", "native": 30, "web": 31.0})

  def test_common_missing_frame_is_not_complete(self):
    with TemporaryDirectory() as root:
        native, web = (Path(root) / name for name in ('native', 'web'))
        rows = '\n'.join(json.dumps({'frame': i}) for i in (0, 2)) + '\n'
        native.write_text(rows); web.write_text(rows)
        result = compare(native, web)
        self.assertEqual(result['frames'][0]['first_difference']['field'], 'frame_coverage')

  def test_shot_without_birth_event_is_blocked(self):
    with TemporaryDirectory() as root:
        native, web, events = (Path(root) / name for name in ('native', 'web', 'events'))
        native.write_text(json.dumps({'frame': 0, 'shots': [{'slot': 12}]}) + '\n')
        web.write_text(json.dumps({'frame': 0, 'shots': []}) + '\n')
        events.write_text('')
        result = compare(native, web, birth_path=events)
        self.assertEqual(result['frames'][0]['first_difference']['field'], 'shots.identity')

if __name__ == '__main__':
    unittest.main()
