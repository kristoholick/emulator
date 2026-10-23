"""Mochi's logic: hardware detection, presets, shadPS4 config files and game
discovery. No UI code here, so it can be tested on its own."""

import json
import os
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

LAUNCHER_DIR = Path(__file__).resolve().parent
PRESETS_DIR = LAUNCHER_DIR / "presets"
SETTINGS_FILE = Path(os.environ.get("MOCHI_SETTINGS", LAUNCHER_DIR / "settings.json"))
DEFAULT_EXE = LAUNCHER_DIR.parent.parent / "Build" / "x64-Clang-Release" / "shadps4.exe"
PRESET_ORDER = ["performance", "balanced", "quality"]

# Words that only appear in dedicated (non-integrated) GPU names
DEDICATED_GPU_HINTS = ("geforce", "rtx", "gtx", "quadro", "radeon rx", "radeon pro", "arc a", "arc b")


# ---- Hardware ------------------------------------------------------------------

@dataclass
class Hardware:
    cpu: str = "Unknown CPU"
    cores: int = 0
    threads: int = 0
    ram_gb: float = 0.0
    gpus: list = field(default_factory=list)  # [{"name", "hz", "width", "height"}]

    @property
    def main_gpu(self):
        dedicated = [g for g in self.gpus if is_dedicated(g["name"])]
        return (dedicated or self.gpus or [{"name": "Unknown GPU", "hz": 0, "width": 0, "height": 0}])[0]

    @property
    def integrated(self):
        return not is_dedicated(self.main_gpu["name"])


def is_dedicated(gpu_name):
    name = gpu_name.lower()
    return any(hint in name for hint in DEDICATED_GPU_HINTS)


_HW_SCRIPT = (
    "$c = Get-CimInstance Win32_Processor | Select-Object -First 1;"
    "$g = @(Get-CimInstance Win32_VideoController);"
    "$m = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
    "[pscustomobject]@{cpu=$c.Name.Trim(); cores=$c.NumberOfCores; threads=$c.NumberOfLogicalProcessors;"
    " ram=$m; gpus=@($g | ForEach-Object { [pscustomobject]@{name=$_.Name; hz=$_.CurrentRefreshRate;"
    " width=$_.CurrentHorizontalResolution; height=$_.CurrentVerticalResolution} })}"
    " | ConvertTo-Json -Depth 3 -Compress"
)


def detect_hardware():
    """Ask Windows (CIM) for CPU, RAM, GPU and display info. Takes about a second."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _HW_SCRIPT],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data = json.loads(out.stdout)
    return Hardware(
        cpu=data["cpu"],
        cores=data["cores"] or 0,
        threads=data["threads"] or 0,
        ram_gb=round((data["ram"] or 0) / 1024**3, 1),
        gpus=[{"name": g["name"], "hz": g["hz"] or 0, "width": g["width"] or 0, "height": g["height"] or 0}
              for g in data["gpus"]],
    )


def recommend_preset(hw):
    """Integrated graphics are the bottleneck for PS4 emulation, so they get Performance."""
    return "performance" if hw.integrated else "balanced"


def hardware_warnings(hw):
    warnings = []
    if hw.integrated:
        warnings.append("Integrated graphics: expect low frame rates in demanding 3D games.")
    if hw.ram_gb and hw.ram_gb < 15:
        warnings.append(f"{hw.ram_gb:g} GB RAM is tight; 16 GB or more (dual channel) helps a lot.")
    return warnings


# ---- Presets -------------------------------------------------------------------

def load_presets(directory=PRESETS_DIR):
    presets = {}
    for preset_id in PRESET_ORDER:
        path = Path(directory) / f"{preset_id}.json"
        presets[preset_id] = json.loads(path.read_text(encoding="utf-8"))
    return presets


def matching_preset(config, presets):
    """Return the id of the preset whose values all match `config`, or None."""
    for preset_id, preset in presets.items():
        if all(config.get(group, {}).get(key) == value
               for group, values in preset["settings"].items()
               for key, value in values.items()):
            return preset_id
    return None


# ---- shadPS4 config files ------------------------------------------------------

def user_dir(exe):
    """Same rule as shadPS4's path_util.cpp: a `user` folder next to where it runs, else %APPDATA%."""
    portable = Path(exe).parent / "user"
    if portable.is_dir():
        return portable
    return Path(os.environ["APPDATA"]) / "shadPS4"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def merge_settings(config, settings):
    """Set only the given keys; shadPS4 keeps defaults for everything else."""
    for group, values in settings.items():
        config.setdefault(group, {}).update(values)
    return config


def apply_global_preset(udir, preset):
    path = Path(udir) / "config.json"
    config = merge_settings(read_json(path), preset["settings"])
    write_json(path, config)
    return config


def set_fullscreen(udir, fullscreen):
    path = Path(udir) / "config.json"
    config = merge_settings(read_json(path), {"GPU": {
        "full_screen": fullscreen,
        "full_screen_mode": "Fullscreen (Borderless)" if fullscreen else "Windowed",
    }})
    write_json(path, config)


def game_config_path(udir, serial):
    return Path(udir) / "custom_configs" / f"{serial}.json"


def set_game_preset(udir, serial, preset, presets):
    """Write a per-game override. preset=None removes the launcher's keys again."""
    path = game_config_path(udir, serial)
    config = read_json(path)
    if preset is None:
        for p in presets.values():
            for group, values in p["settings"].items():
                for key in values:
                    config.get(group, {}).pop(key, None)
        config = {group: values for group, values in config.items() if values}
        if config:
            write_json(path, config)
        elif path.exists():
            path.unlink()
        return config
    write_json(path, merge_settings(config, preset["settings"]))
    return config


def game_preset(udir, serial, presets):
    """'default' if the game has no override, a preset id, or 'custom'."""
    path = game_config_path(udir, serial)
    if not path.exists():
        return "default"
    config = read_json(path)
    if not any(config.get(group, {}).get(key) is not None
               for p in presets.values() for group, values in p["settings"].items() for key in values):
        return "default"
    return matching_preset(config, presets) or "custom"


def game_folders(udir):
    config = read_json(Path(udir) / "config.json")
    return [d["path"] for d in config.get("General", {}).get("install_dirs", []) if d.get("enabled", True)]


def add_game_folder(udir, folder):
    path = Path(udir) / "config.json"
    config = read_json(path)
    dirs = config.setdefault("General", {}).setdefault("install_dirs", [])
    if not any(Path(d["path"]) == Path(folder) for d in dirs):
        dirs.append({"path": str(folder), "enabled": True})
        write_json(path, config)


def remove_game_folder(udir, folder):
    path = Path(udir) / "config.json"
    config = read_json(path)
    general = config.setdefault("General", {})
    general["install_dirs"] = [d for d in general.get("install_dirs", []) if Path(d["path"]) != Path(folder)]
    write_json(path, config)


# ---- Games ---------------------------------------------------------------------

@dataclass
class Game:
    title: str
    serial: str
    version: str
    path: Path


def read_param_sfo(path):
    """Parse a PS4 PARAM.SFO (game metadata: title, title ID, version)."""
    data = Path(path).read_bytes()
    if data[:4] != b"\0PSF":
        raise ValueError(f"{path} is not a PARAM.SFO file")
    key_table, data_table, count = struct.unpack_from("<III", data, 8)
    values = {}
    for i in range(count):
        key_off, fmt, length, _max_len, data_off = struct.unpack_from("<HHIII", data, 20 + i * 16)
        key_start = key_table + key_off
        key = data[key_start:data.index(b"\0", key_start)].decode("ascii")
        raw = data[data_table + data_off:data_table + data_off + length]
        if fmt == 0x0404:  # 32-bit integer
            values[key] = struct.unpack("<I", raw[:4])[0]
        else:              # UTF-8 string
            values[key] = raw.rstrip(b"\0").decode("utf-8", "replace")
    return values


def find_games(folders, depth=2):
    """A game folder holds eboot.bin and sce_sys/param.sfo. Looks `depth` levels down."""
    games = []
    seen = set()

    def visit(folder, level):
        sfo = folder / "sce_sys" / "param.sfo"
        if sfo.is_file() and (folder / "eboot.bin").is_file():
            try:
                info = read_param_sfo(sfo)
            except (ValueError, struct.error, OSError):
                return
            key = folder.resolve()
            if key not in seen:
                seen.add(key)
                games.append(Game(
                    title=info.get("TITLE", folder.name),
                    serial=info.get("TITLE_ID", folder.name),
                    version=info.get("APP_VER", info.get("VERSION", "")),
                    path=folder,
                ))
            return
        if level < depth:
            try:
                children = sorted(p for p in folder.iterdir() if p.is_dir())
            except OSError:
                return
            for child in children:
                visit(child, level + 1)

    for folder in folders:
        if Path(folder).is_dir():
            visit(Path(folder), 0)
    return sorted(games, key=lambda g: g.title.lower())


# ---- Launching -----------------------------------------------------------------

def launch_command(exe, game_path=None, show_fps=False, big_picture=False):
    cmd = [str(exe)]
    if big_picture:
        cmd.append("-b")
    if game_path is not None:
        cmd += ["-g", str(game_path)]
    if show_fps:
        cmd.append("--show-fps")
    return cmd


def launch(exe, **kwargs):
    # Run from the exe's folder so shadPS4 resolves its user folder the same way user_dir() does
    return subprocess.Popen(launch_command(exe, **kwargs), cwd=Path(exe).parent)


# ---- Launcher's own settings ---------------------------------------------------

DEFAULT_LAUNCHER_SETTINGS = {"exe": str(DEFAULT_EXE), "fullscreen": False, "show_fps": True}


def load_launcher_settings():
    return {**DEFAULT_LAUNCHER_SETTINGS, **read_json(SETTINGS_FILE)}


def save_launcher_settings(settings):
    write_json(SETTINGS_FILE, settings)
