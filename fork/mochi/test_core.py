"""Tests for core.py. Run:  python -m unittest fork\\mochi\\test_core.py -v"""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import core


def make_param_sfo(values):
    """Build a PARAM.SFO: header, index table, key table, data table."""
    keys = sorted(values)
    key_table = b""
    data_table = b""
    index = b""
    for key in keys:
        value = values[key]
        if isinstance(value, int):
            raw, fmt = struct.pack("<I", value), 0x0404
        else:
            raw, fmt = value.encode() + b"\0", 0x0204
        max_len = (len(raw) + 3) & ~3
        index += struct.pack("<HHIII", len(key_table), fmt, len(raw), max_len, len(data_table))
        key_table += key.encode() + b"\0"
        data_table += raw.ljust(max_len, b"\0")
    key_table = key_table.ljust((len(key_table) + 3) & ~3, b"\0")
    key_start = 20 + len(index)
    data_start = key_start + len(key_table)
    header = b"\0PSF" + struct.pack("<IIII", 0x101, key_start, data_start, len(keys))
    return header + index + key_table + data_table


def make_png(size, rgb):
    """A plain one-colour PNG."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    row = b"\0" + bytes(rgb) * size
    raw = zlib.compress(row * size)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def make_game(folder, title, serial, version="01.00", icon_rgb=None):
    folder = Path(folder)
    (folder / "sce_sys").mkdir(parents=True)
    (folder / "eboot.bin").write_bytes(b"\0" * 16)
    (folder / "sce_sys" / "param.sfo").write_bytes(
        make_param_sfo({"TITLE": title, "TITLE_ID": serial, "APP_VER": version, "ATTRIBUTE": 0}))
    if icon_rgb:
        (folder / "sce_sys" / "icon0.png").write_bytes(make_png(64, icon_rgb))
    return folder


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.presets = core.load_presets()

    def test_param_sfo_round_trip(self):
        path = self.tmp / "param.sfo"
        path.write_bytes(make_param_sfo({"TITLE": "Cloud Garden", "TITLE_ID": "CUSA00001", "ATTRIBUTE": 7}))
        values = core.read_param_sfo(path)
        self.assertEqual(values["TITLE"], "Cloud Garden")
        self.assertEqual(values["TITLE_ID"], "CUSA00001")
        self.assertEqual(values["ATTRIBUTE"], 7)

    def test_find_games_two_levels_down_sorted_by_title(self):
        make_game(self.tmp / "b" / "game", "Zebra Run", "CUSA00002")
        make_game(self.tmp / "a", "Apple Tree", "CUSA00001")
        (self.tmp / "not-a-game").mkdir()
        games = core.find_games([self.tmp])
        self.assertEqual([g.title for g in games], ["Apple Tree", "Zebra Run"])
        self.assertEqual(games[0].version, "01.00")

    def test_global_preset_keeps_other_settings(self):
        (self.tmp / "config.json").write_text(json.dumps({"GPU": {"window_width": 1600}, "General": {"volume_slider": 40}}))
        core.apply_global_preset(self.tmp, self.presets["performance"])
        config = core.read_json(self.tmp / "config.json")
        self.assertEqual(config["GPU"]["window_width"], 1600)
        self.assertEqual(config["General"]["volume_slider"], 40)
        self.assertTrue(config["Vulkan"]["pipeline_cache_enabled"])
        self.assertEqual(core.matching_preset(config, self.presets), "performance")

    def test_presets_are_distinguishable(self):
        for preset_id, preset in self.presets.items():
            self.assertEqual(core.matching_preset(preset["settings"], self.presets), preset_id)

    def test_game_preset_override_and_reset(self):
        self.assertEqual(core.game_preset(self.tmp, "CUSA00001", self.presets), "default")
        core.set_game_preset(self.tmp, "CUSA00001", self.presets["quality"], self.presets)
        self.assertEqual(core.game_preset(self.tmp, "CUSA00001", self.presets), "quality")
        # A key shadPS4 wrote itself must survive resetting the launcher's choice
        path = core.game_config_path(self.tmp, "CUSA00001")
        config = core.read_json(path)
        config["GPU"]["window_width"] = 1920
        core.write_json(path, config)
        core.set_game_preset(self.tmp, "CUSA00001", None, self.presets)
        self.assertEqual(core.game_preset(self.tmp, "CUSA00001", self.presets), "default")
        self.assertEqual(core.read_json(path), {"GPU": {"window_width": 1920}})

    def test_reset_removes_empty_game_file(self):
        core.set_game_preset(self.tmp, "CUSA00009", self.presets["balanced"], self.presets)
        core.set_game_preset(self.tmp, "CUSA00009", None, self.presets)
        self.assertFalse(core.game_config_path(self.tmp, "CUSA00009").exists())

    def test_game_folders_add_and_remove(self):
        core.add_game_folder(self.tmp, self.tmp / "games")
        core.add_game_folder(self.tmp, self.tmp / "games")  # no duplicates
        self.assertEqual(core.game_folders(self.tmp), [str(self.tmp / "games")])
        core.remove_game_folder(self.tmp, str(self.tmp / "games"))
        self.assertEqual(core.game_folders(self.tmp), [])

    def test_graphics_defaults_match_shadps4(self):
        # With an empty config.json every setting shows shadPS4's own default
        for section in core.graphics_state({}):
            for setting in section["settings"]:
                self.assertIsNotNone(setting["current"], setting["id"])

    def test_graphics_option_writes_exact_keys(self):
        (self.tmp / "config.json").write_text(json.dumps({"GPU": {"window_width": 1600}}))
        core.apply_graphics_option(self.tmp, "display_mode", 1)
        core.apply_graphics_option(self.tmp, "anisotropic_filtering", 5)
        config = core.read_json(self.tmp / "config.json")
        self.assertEqual(config["GPU"]["full_screen_mode"], "Fullscreen (Borderless)")
        self.assertTrue(config["GPU"]["full_screen"])
        self.assertEqual(config["GPU"]["anisotropic_filtering"], 16)
        self.assertEqual(config["GPU"]["window_width"], 1600)
        state = {s["id"]: s["current"] for section in core.graphics_state(config) for s in section["settings"]}
        self.assertEqual(state["display_mode"], 1)
        self.assertEqual(state["anisotropic_filtering"], 5)
        self.assertIsNone(state["window_size"])  # 1600 wide with the default 720 height isn't a listed size

    def test_graphics_marks_settings_presets_change(self):
        by_preset = {s["id"]: s["byPreset"] for section in core.graphics_state({}, self.presets)
                     for s in section["settings"]}
        self.assertTrue(by_preset["upscaling"])
        self.assertTrue(by_preset["shader_cache"])
        self.assertFalse(by_preset["display_mode"])
        self.assertFalse(by_preset["anisotropic_filtering"])

    def test_graphics_rejects_unknown_choices(self):
        with self.assertRaises(ValueError):
            core.apply_graphics_option(self.tmp, "no_such_setting", 0)
        with self.assertRaises(ValueError):
            core.apply_graphics_option(self.tmp, "vsync", 9)

    def test_graphics_options_are_distinct(self):
        for section in core.GRAPHICS_SECTIONS:
            for setting in section["settings"]:
                for i, option in enumerate(setting["options"]):
                    self.assertEqual(core.current_option(option["values"], setting), i, setting["id"])

    def test_presets_use_known_graphics_values(self):
        # Every preset value must be one a graphics option can show
        for preset in self.presets.values():
            for setting in (s for section in core.GRAPHICS_SECTIONS for s in section["settings"]):
                keys = {(g, k) for o in setting["options"] for g, values in o["values"].items() for k in values}
                if all(preset["settings"].get(g, {}).get(k) is not None for g, k in keys):
                    self.assertIsNotNone(core.current_option(preset["settings"], setting), setting["id"])

    def test_integrated_gpu_gets_performance(self):
        hw = core.Hardware(gpus=[{"name": "AMD Radeon(TM) Graphics", "hz": 180, "width": 1920, "height": 1080}], ram_gb=11.9)
        self.assertTrue(hw.integrated)
        self.assertEqual(core.recommend_preset(hw), "performance")
        self.assertEqual(len(core.hardware_warnings(hw)), 2)
        hw.gpus.append({"name": "NVIDIA GeForce RTX 4060", "hz": 144, "width": 2560, "height": 1440})
        self.assertFalse(hw.integrated)
        self.assertEqual(core.recommend_preset(hw), "balanced")

    def test_reference_pc_is_recognised(self):
        hw = core.Hardware(cpu="AMD Ryzen 5 5600G with Radeon Graphics", cores=6, threads=12, ram_gb=11.9,
                           gpus=[{"name": "AMD Radeon(TM) Graphics", "hz": 180, "width": 1920, "height": 1080}])
        self.assertTrue(hw.is_reference_pc)
        self.assertEqual(core.recommend_preset(hw), "performance")
        self.assertEqual(len(core.hardware_notes(hw)), 1)
        warnings = core.hardware_warnings(hw)
        self.assertIn("Vega 7", warnings[0])
        self.assertIn("11.9 GB", warnings[1])

    def test_basic_intel_laptop_gets_its_own_warnings(self):
        hw = core.Hardware(cpu="Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz", cores=4, threads=8, ram_gb=15.9,
                           gpus=[{"name": "Intel(R) UHD Graphics 620", "hz": 60, "width": 1920, "height": 1080}])
        self.assertFalse(hw.is_reference_pc)
        self.assertEqual(core.recommend_preset(hw), "performance")
        self.assertEqual(core.hardware_notes(hw), [])
        warnings = core.hardware_warnings(hw)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith("Intel(R) UHD Graphics 620 is much slower"))
        self.assertIn("low-power laptop CPU", warnings[1])

    def test_missing_gpu_driver_is_flagged(self):
        hw = core.Hardware(cpu="AMD Ryzen 5 5600G with Radeon Graphics", ram_gb=16,
                           gpus=[{"name": "Microsoft Basic Display Adapter", "hz": 60, "width": 1024, "height": 768}])
        self.assertIn("no graphics driver", core.hardware_warnings(hw)[0])

    def test_launch_command(self):
        cmd = core.launch_command(Path("C:/x/shadps4.exe"), game_path=Path("D:/g"), show_fps=True)
        self.assertEqual(cmd, [str(Path("C:/x/shadps4.exe")), "-g", str(Path("D:/g")), "--show-fps"])


if __name__ == "__main__":
    unittest.main()
