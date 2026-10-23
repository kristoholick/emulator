"""Mochi's logic: hardware detection, presets, shadPS4 config files and game
discovery. No UI code here, so it can be tested on its own."""

import json
import os
import re
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

# The PC Mochi's presets are tuned on: Ryzen 5 5600G with its integrated Radeon Vega 7
REFERENCE_CPU = "5600G"
REFERENCE_PC = "Ryzen 5 5600G with Radeon Vega 7 graphics"


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

    @property
    def is_reference_pc(self):
        return REFERENCE_CPU.lower() in self.cpu.lower() and self.integrated


def is_dedicated(gpu_name):
    name = gpu_name.lower()
    return any(hint in name for hint in DEDICATED_GPU_HINTS)


def is_basic_intel_gpu(gpu_name):
    """Intel HD/UHD Graphics: entry-level integrated GPUs, far slower than Iris Xe, Arc or a Vega 7."""
    name = gpu_name.lower()
    return "intel" in name and ("uhd graphics" in name or "hd graphics" in name)


def is_missing_gpu_driver(gpu_name):
    name = gpu_name.lower()
    return "basic display" in name or "basic render" in name


def is_low_power_cpu(cpu_name):
    """Laptop CPUs ending in U or Y (e.g. i5-8265U, Ryzen 5 5500U) run at 15 W or less."""
    return re.search(r"\d{4,5}[UY]\b", cpu_name) is not None


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
    """The GPU is the bottleneck for PS4 emulation, so integrated graphics get Performance."""
    return "performance" if hw.integrated else "balanced"


def hardware_notes(hw):
    """Good news about this PC, shown above the warnings."""
    if hw.is_reference_pc:
        return [f"This is the PC Mochi is tuned on ({REFERENCE_PC}). Performance is set up for it."]
    return []


def hardware_warnings(hw):
    warnings = []
    gpu = hw.main_gpu["name"]
    if is_missing_gpu_driver(gpu):
        warnings.append(f"Windows is using “{gpu}”, so no graphics driver is installed. "
                        "Install your GPU's driver; shadPS4 needs Vulkan to run.")
    elif is_basic_intel_gpu(gpu):
        warnings.append(f"{gpu} is much slower than the Vega 7 Mochi is tuned on, and far below a PS4. "
                        "Expect most 3D games to run very slowly; 2D and lighter games have the best chance.")
    elif hw.integrated and not hw.is_reference_pc:
        warnings.append(f"{gpu} is integrated graphics: expect low frame rates in demanding 3D games.")
    elif hw.integrated:
        warnings.append("The Vega 7 is the bottleneck: expect 20–30 fps in heavy 3D games.")
    if is_low_power_cpu(hw.cpu):
        warnings.append(f"{hw.cpu} is a low-power laptop CPU. Plug in the charger and set Windows' "
                        "power mode to Best performance while playing.")
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


# ---- Graphics & display settings -----------------------------------------------
# Only settings the shadPS4 engine really applies. Each option lists the exact
# config.json values it writes.

def _option(label, help_text, **groups):
    return {"label": label, "help": help_text, "values": groups}


def _toggle(group, key, off_help, on_help):
    return [_option("Off", off_help, **{group: {key: False}}), _option("On", on_help, **{group: {key: True}})]


GRAPHICS_SECTIONS = [
    {"id": "display", "name": "Display", "settings": [
        {"id": "display_mode", "name": "Display mode",
         "help": "How the game window opens.",
         "options": [
             _option("Windowed", "A normal window you can move and resize.",
                     GPU={"full_screen": False, "full_screen_mode": "Windowed"}),
             _option("Borderless fullscreen", "Fills the screen at your desktop resolution. Switching apps is instant.",
                     GPU={"full_screen": True, "full_screen_mode": "Fullscreen (Borderless)"}),
             _option("Exclusive fullscreen", "Takes over the display in its current mode. Switching apps is slower.",
                     GPU={"full_screen": True, "full_screen_mode": "Fullscreen"}),
         ]},
        {"id": "window_size", "name": "Window size",
         "help": "Size of the game window in Windowed mode. Fullscreen always fills the screen. "
                 "Games still render at their own resolution.",
         "options": [_option(f"{w} × {h}", "", GPU={"window_width": w, "window_height": h})
                     for w, h in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160))]},
        {"id": "vsync", "name": "VSync",
         "help": "How finished frames are shown on your screen.",
         "options": [
             _option("On (Mailbox)", "No tearing, with low input delay.", GPU={"present_mode": "Mailbox"}),
             _option("On (FIFO)", "Classic VSync: no tearing and steady pacing, slightly more input delay. "
                                  "Try it if you see stutter.", GPU={"present_mode": "Fifo"}),
             _option("Off (Immediate)", "Lowest input delay. The picture can tear.",
                     GPU={"present_mode": "Immediate"}),
         ]},
        {"id": "hdr", "name": "HDR",
         "help": "HDR output for games that support it, on an HDR screen with HDR turned on in Windows.",
         "options": _toggle("GPU", "hdr_allowed", "Standard (SDR) output.", "HDR output when the game supports it.")},
    ]},
    {"id": "graphics", "name": "Graphics", "settings": [
        {"id": "console_mode", "name": "Console mode",
         "help": "Which PS4 model the game sees. Only games with PS4 Pro support change.",
         "options": [
             _option("PS4", "The standard PS4 graphics.", General={"neo_mode": False}),
             _option("PS4 Pro", "Games with PS4 Pro support use their Pro graphics, usually a higher "
                                "resolution or frame rate. Much heavier on the GPU.", General={"neo_mode": True}),
         ]},
        {"id": "upscaling", "name": "Upscaling (FSR 1)",
         "help": "AMD FSR 1 upscales the game's picture to your window size. It only runs when the game "
                 "renders below the window size.",
         "options": _toggle("GPU", "fsr_enabled", "Simple scaling, the cheapest.",
                            "Sharper upscaling, costs a little GPU time.")},
        {"id": "sharpening", "name": "Sharpening (RCAS)",
         "help": "Sharpens the picture after FSR upscaling. It only works while upscaling is on.",
         "options": [
             _option("Off", "No sharpening.", GPU={"rcas_enabled": False}),
             _option("Light", "Gentle sharpening.", GPU={"rcas_enabled": True, "rcas_attenuation": 1000}),
             _option("Normal", "shadPS4's default strength.", GPU={"rcas_enabled": True, "rcas_attenuation": 250}),
             _option("Strong", "Maximum sharpening. Can look grainy.",
                     GPU={"rcas_enabled": True, "rcas_attenuation": 0}),
         ]},
        {"id": "anisotropic_filtering", "name": "Anisotropic filtering",
         "help": "Keeps textures sharp on surfaces seen at an angle, such as roads and floors. "
                 "Needs the Mochi engine built from this version.",
         "options": [
             _option("Game's choice", "Uses whatever the game asks for.", GPU={"anisotropic_filtering": 0}),
             _option("Off", "Turns it off, even where the game uses it. Blurriest, slightly faster.",
                     GPU={"anisotropic_filtering": 1}),
             *[_option(f"{n}x", "Higher is sharper in the distance and costs a little more GPU time.",
                       GPU={"anisotropic_filtering": n}) for n in (2, 4, 8, 16)],
         ]},
        {"id": "shader_precision", "name": "Shader precision",
         "help": "How exactly pixel and compute shaders do their math.",
         "options": [
             _option("Exact", "Matches the PS4 most closely.", GPU={"fast_shader_math": False}),
             _option("Fast", "Lets the GPU fuse and simplify math. Faster, with a small risk of visual glitches.",
                     GPU={"fast_shader_math": True}),
         ]},
        {"id": "shader_cache", "name": "Shader cache",
         "help": "Saves compiled shaders to disk, so stutter mostly goes away after you've seen something once.",
         "options": [
             _option("Off", "Shaders compile again every time you play.",
                     Vulkan={"pipeline_cache_enabled": False, "pipeline_cache_archived": False}),
             _option("On", "Stored compressed in your shadPS4 folder.",
                     Vulkan={"pipeline_cache_enabled": True, "pipeline_cache_archived": True}),
         ]},
    ]},
    {"id": "advanced", "name": "Advanced", "settings": [
        {"id": "readbacks", "name": "GPU readbacks",
         "help": "Lets the game read back what the GPU drew. Fixes some missing or broken effects.",
         "options": [
             _option("Off", "Fastest.", GPU={"readbacks_mode": 0}),
             _option("Relaxed", "Fixes most readback effects, at a speed cost.", GPU={"readbacks_mode": 1}),
             _option("Precise", "Most accurate and slowest.", GPU={"readbacks_mode": 2}),
         ]},
        {"id": "linear_readbacks", "name": "Linear image readbacks",
         "help": "Also reads back linear (untiled) images the GPU wrote. A few games need it.",
         "options": _toggle("GPU", "readback_linear_images_enabled", "Faster.", "More accurate, slower.")},
        {"id": "copy_gpu_buffers", "name": "Copy GPU command buffers",
         "help": "Copies each command buffer before the GPU runs it. Fixes games that change a buffer "
                 "while it's still in use.",
         "options": _toggle("GPU", "copy_gpu_buffers", "Faster.", "Safer for some games, slower.")},
        {"id": "vblank", "name": "Vblank rate",
         "help": "How often the emulated PS4 signals a new screen refresh. Many games tie their speed to "
                 "it, so anything other than 60 Hz can make a game run too fast.",
         "options": [_option(f"{hz} Hz", "The PS4's real rate." if hz == 60 else
                             "Raises the frame-rate cap in some games, and speeds others up.",
                             GPU={"vblank_frequency": hz})
                     for hz in (60, 90, 120, 144)]},
    ]},
]

# shadPS4's own defaults (src/core/emulator_settings.h), for keys missing from config.json
SHADPS4_DEFAULTS = {
    "General": {"neo_mode": False},
    "GPU": {"full_screen": False, "full_screen_mode": "Windowed", "window_width": 1280, "window_height": 720,
            "present_mode": "Mailbox", "hdr_allowed": False, "fsr_enabled": False, "rcas_enabled": True,
            "rcas_attenuation": 250, "anisotropic_filtering": 0, "fast_shader_math": False,
            "readbacks_mode": 0, "readback_linear_images_enabled": False, "copy_gpu_buffers": False,
            "vblank_frequency": 60},
    "Vulkan": {"pipeline_cache_enabled": False, "pipeline_cache_archived": False},
}


def graphics_setting(setting_id):
    for section in GRAPHICS_SECTIONS:
        for setting in section["settings"]:
            if setting["id"] == setting_id:
                return setting
    raise ValueError(f"Unknown graphics setting: {setting_id}")


def current_option(config, setting):
    """Index of the option that matches `config`, or None when no option does."""
    def value(group, key):
        return config.get(group, {}).get(key, SHADPS4_DEFAULTS[group][key])

    for i, option in enumerate(setting["options"]):
        if all(value(group, key) == v for group, values in option["values"].items() for key, v in values.items()):
            return i
    return None


def graphics_state(config, presets=None):
    preset_keys = {(group, key) for p in (presets or {}).values()
                   for group, values in p["settings"].items() for key in values}
    return [{
        "id": section["id"],
        "name": section["name"],
        "settings": [{
            "id": s["id"],
            "name": s["name"],
            "help": s["help"],
            "options": [{"label": o["label"], "help": o["help"]} for o in s["options"]],
            "current": current_option(config, s),
            "byPreset": any((group, key) in preset_keys
                            for o in s["options"] for group, values in o["values"].items() for key in values),
        } for s in section["settings"]],
    } for section in GRAPHICS_SECTIONS]


def apply_graphics_option(udir, setting_id, option_index):
    options = graphics_setting(setting_id)["options"]
    if not isinstance(option_index, int) or not 0 <= option_index < len(options):
        raise ValueError("That option doesn't exist.")
    path = Path(udir) / "config.json"
    config = merge_settings(read_json(path), options[option_index]["values"])
    write_json(path, config)
    return config


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

DEFAULT_LAUNCHER_SETTINGS = {"exe": str(DEFAULT_EXE), "show_fps": True}


def load_launcher_settings():
    return {**DEFAULT_LAUNCHER_SETTINGS, **read_json(SETTINGS_FILE)}


def save_launcher_settings(settings):
    write_json(SETTINGS_FILE, settings)
