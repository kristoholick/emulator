"""Mochi: a cozy launcher for the shadPS4 emulator core.

    python fork\\mochi\\mochi.py      (or double-click Mochi.cmd)

Runs a tiny web server on 127.0.0.1 only, opens the UI in its own app window,
and quits a few seconds after that window is closed.
"""

import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core

WEB_DIR = core.LAUNCHER_DIR / "web"
WINDOW_PROFILE = core.LAUNCHER_DIR / ".window-profile"
TOKEN = secrets.token_urlsafe(18)  # stops other web pages from driving the local API
IDLE_EXIT_SECONDS = 15
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


class State:
    def __init__(self):
        self.presets = core.load_presets()
        self.settings = core.load_launcher_settings()
        self.games = []
        self.hardware = None
        self.last_ping = time.monotonic()
        self.dialog_lock = threading.Lock()

    @property
    def exe(self):
        return Path(self.settings["exe"])

    @property
    def udir(self):
        return core.user_dir(self.exe)


state = State()


def snapshot():
    udir = state.udir
    config = core.read_json(udir / "config.json")
    folders = core.game_folders(udir)
    state.games = core.find_games(folders)
    return {
        "exe": str(state.exe),
        "exeFound": state.exe.is_file(),
        "configDir": str(udir),
        "presets": [{"id": pid, "name": p["name"], "summary": p["summary"], "details": p["details"]}
                    for pid, p in state.presets.items()],
        "preset": core.matching_preset(config, state.presets),
        "fullscreen": state.settings["fullscreen"],
        "showFps": state.settings["show_fps"],
        "folders": folders,
        "games": [{
            "id": i,
            "title": g.title,
            "serial": g.serial,
            "version": g.version,
            "preset": core.game_preset(udir, g.serial, state.presets),
            "hasIcon": (g.path / "sce_sys" / "icon0.png").is_file(),
        } for i, g in enumerate(state.games)],
    }


def hardware_info():
    if state.hardware is None:
        state.hardware = core.detect_hardware()
    hw = state.hardware
    gpu = hw.main_gpu
    return {
        "cpu": hw.cpu,
        "cores": hw.cores,
        "threads": hw.threads,
        "ramGb": hw.ram_gb,
        "gpu": gpu["name"],
        "integrated": hw.integrated,
        "display": {"width": gpu["width"], "height": gpu["height"], "hz": gpu["hz"]},
        "recommended": core.recommend_preset(hw),
        "warnings": core.hardware_warnings(hw),
    }


def pick_path(kind):
    """Native Windows file/folder picker (a browser page can't see real paths)."""
    import tkinter as tk
    from tkinter import filedialog

    with state.dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if kind == "folder":
                return filedialog.askdirectory(parent=root, title="Choose the folder with your PS4 game dumps")
            return filedialog.askopenfilename(parent=root, title="Find shadps4.exe",
                                              filetypes=[("shadps4.exe", "shadps4.exe")])
        finally:
            root.destroy()


def save_settings():
    core.save_launcher_settings(state.settings)


def game_by_id(game_id):
    if not isinstance(game_id, int) or not 0 <= game_id < len(state.games):
        raise ValueError("That game is no longer in the list. Refresh and try again.")
    return state.games[game_id]


# ---- POST actions: each returns the fresh state -------------------------------

def act_preset(body):
    core.apply_global_preset(state.udir, state.presets[body["preset"]])


def act_options(body):
    if "fullscreen" in body:
        state.settings["fullscreen"] = bool(body["fullscreen"])
        core.set_fullscreen(state.udir, state.settings["fullscreen"])
    if "showFps" in body:
        state.settings["show_fps"] = bool(body["showFps"])
    save_settings()


def act_game_preset(body):
    preset = body.get("preset")
    core.set_game_preset(state.udir, body["serial"], state.presets[preset] if preset else None, state.presets)


def act_add_folder(_body):
    folder = pick_path("folder")
    if folder:
        core.add_game_folder(state.udir, Path(folder))


def act_remove_folder(body):
    core.remove_game_folder(state.udir, body["path"])


def start_engine(**kwargs):
    if not state.exe.is_file():
        raise ValueError("shadps4.exe wasn't found. Build it with fork\\build.cmd or choose its location.")
    try:
        core.launch(state.exe, show_fps=state.settings["show_fps"], **kwargs)
    except OSError as err:
        raise ValueError(f"shadps4.exe couldn't start ({err.strerror}). Rebuild it with fork\\build.cmd.") from err


def act_play(body):
    start_engine(game_path=game_by_id(body.get("id")).path)


def act_big_picture(_body):
    start_engine(big_picture=True)


def act_choose_exe(_body):
    path = pick_path("exe")
    if path:
        state.settings["exe"] = path
        save_settings()


def act_open_config(_body):
    state.udir.mkdir(parents=True, exist_ok=True)
    os.startfile(state.udir)


ACTIONS = {
    "/api/preset": act_preset,
    "/api/options": act_options,
    "/api/game-preset": act_game_preset,
    "/api/folders/add": act_add_folder,
    "/api/folders/remove": act_remove_folder,
    "/api/play": act_play,
    "/api/big-picture": act_big_picture,
    "/api/exe/choose": act_choose_exe,
    "/api/open-config": act_open_config,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_body(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_body(json.dumps(data).encode(), "application/json", status)

    def authorized(self, url):
        given = self.headers.get("X-Mochi-Token") or parse_qs(url.query).get("t", [""])[0]
        if secrets.compare_digest(given, TOKEN):
            return True
        self.send_json({"error": "Not allowed"}, 403)
        return False

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            if not self.authorized(url):
                return
            state.last_ping = time.monotonic()
            try:
                if url.path == "/api/state":
                    return self.send_json(snapshot())
                if url.path == "/api/hardware":
                    return self.send_json(hardware_info())
                if url.path == "/api/ping":
                    return self.send_json({"ok": True})
                if url.path.startswith("/api/icon/"):
                    game = game_by_id(int(url.path.rsplit("/", 1)[1]))
                    icon = game.path / "sce_sys" / "icon0.png"
                    return self.send_body(icon.read_bytes(), "image/png")
            except Exception as err:
                return self.send_json({"error": str(err)}, 500)
            return self.send_json({"error": "Unknown request"}, 404)

        file = (WEB_DIR / (url.path.lstrip("/") or "index.html")).resolve()
        if WEB_DIR.resolve() not in file.parents or not file.is_file():
            return self.send_body(b"Not found", "text/plain", 404)
        self.send_body(file.read_bytes(), CONTENT_TYPES.get(file.suffix, "application/octet-stream"))

    def do_POST(self):
        url = urlparse(self.path)
        if not self.authorized(url):
            return
        action = ACTIONS.get(url.path)
        if action is None:
            return self.send_json({"error": "Unknown request"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            action(body)
            self.send_json(snapshot())
        except Exception as err:
            self.send_json({"error": str(err)}, 400)


def find_edge():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return exe
    return None


def open_window(url):
    """Edge's --app mode gives a clean window with no tabs or address bar."""
    import subprocess

    edge = find_edge()
    if edge is None:
        webbrowser.open(url)
        return
    subprocess.Popen([
        str(edge), f"--app={url}", f"--user-data-dir={WINDOW_PROFILE}",
        "--window-size=1240,800", "--no-first-run", "--no-default-browser-check",
    ])


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?t={TOKEN}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if os.environ.get("MOCHI_NO_WINDOW"):
        print(url, flush=True)
    else:
        open_window(url)
    state.last_ping = time.monotonic()
    while time.monotonic() - state.last_ping < IDLE_EXIT_SECONDS:
        time.sleep(1)
    server.shutdown()


if __name__ == "__main__":
    main()
