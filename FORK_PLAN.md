# Fork plan: Mochi, built on shadPS4 and tuned for this PC

Upstream: https://github.com/shadps4-emu/shadPS4 (GPL-2.0-or-later)
Local checkout: `C:\Claude code work\emulator\shadps4`

## Build status (2026-10-23)

- Upstream commit `8a48cd0` ("kernel: Implement posix_flock and sceKernelFlock", 2026-10-22)
- Toolchain: Visual Studio Build Tools 2026 (MSVC 14.51) plus its Clang (clang-cl), CMake, Ninja and Windows SDK 10.0.26100
- Build: `fork\build.cmd` → `Build\x64-Clang-Release\shadps4.exe` (56.6 MB). All 2,394 build steps succeeded with no errors
- Checked: `shadps4.exe --help` runs, and `shadps4.exe -b` (Big Picture) opens and stays up. The AMD Vulkan driver (31.0.21925.1001, May 2026) is registered
- Not checked: running an actual game (none available yet)
- First run created `%APPDATA%\shadPS4\` with a default `config.json`. That's the folder to edit, since there's no portable `user\` folder next to the exe

To rebuild after pulling changes, run `fork\build.cmd`. Only changed files recompile.

## Target hardware

| Part | Value | What it means for PS4 emulation |
|---|---|---|
| CPU | Ryzen 5 5600G, 6 cores / 12 threads, Zen 3 | Fine. PS4 code runs natively on x86-64, and 6 Zen 3 cores beat the PS4's 8 Jaguar cores |
| GPU | Radeon Vega 7 (integrated, shares system RAM) | **The bottleneck.** Roughly 1/3–1/2 of a PS4 GPU. Expect 720p or lower internal resolution, and 20–30 fps in heavy 3D games |
| RAM | ~12 GB usable (iGPU reserves the rest) | Tight. The PS4 has 8 GB, and the emulator plus the iGPU's share both come out of this. Dual-channel 16–32 GB helps the iGPU directly |
| Display | 1920×1080 @ 180 Hz | Use FSR to upscale 720p → 1080p; games stay at their own 30/60 fps |
| Disk | NVMe SSD | Good for shader cache and fast loading |

## How settings work today (no code changes needed)

- Global settings live in `config.json` inside the user folder. That's a `user\` folder next to where you run `shadps4.exe` if one exists; otherwise `%APPDATA%\shadPS4\` (the one in use on this PC).
- Per-game overrides live in `custom_configs\<GAME_SERIAL>.json` in the same user folder (for example `CUSA03173.json`). Only fields listed in `GetOverrideableFields()` can be overridden per game.
- All settings are declared in `src/core/emulator_settings.h` as `Setting<T>` members of `GeneralSettings`, `GPUSettings`, `VulkanSettings` and so on. Each `Setting` has a default, a global value and an optional game-specific value.
- Launch: `shadps4.exe -g <game folder or serial>`, or `shadps4.exe -b` for the built-in Big Picture launcher. Useful flags: `--show-fps`, `-f true` (fullscreen), `--config-clean` (ignore your settings and use defaults).

## Settings that matter for a Vega 7

| Setting (group → key) | Default | Suggested here | Why |
|---|---|---|---|
| GPU → `fast_shader_math` | false | true (Performance, Balanced) | Allows fused multiply-add and drops NaN/Inf preservation in pixel and compute shaders. Faster, small risk of visual glitches |
| GPU → `fsr_enabled` | false | off (Performance), on (Balanced, Quality) | FSR 1 upscales the game's output to the window size. It costs a pass per frame |
| GPU → `rcas_enabled` / `rcas_attenuation` | true / 250 | true / 250–400 | Sharpening after FSR; a higher value means less sharpening |
| GPU → `present_mode` | Mailbox | Mailbox | No tearing and low latency on a 180 Hz screen. `Fifo` if you see stutter |
| GPU → `vblank_frequency` | 60 | 60 | Leave it. Many games tie game speed to vblank |
| GPU → `readbacks_mode` | Disabled | Disabled | Readbacks are accurate but slow |
| GPU → `copy_gpu_buffers` | false | false | Accuracy option with a performance cost |
| Vulkan → `pipeline_cache_enabled` | false | **true** | Saves compiled shaders so the second play-through stutters far less. The biggest single win on a slow GPU |
| Vulkan → `pipeline_cache_archived` | false | true | Stores the cache compressed |
| Vulkan → `gpu_id` | -1 | -1 | Only one GPU, so auto is right |
| General → `show_fps_counter` | false | true while tuning | See what each change does |

**Correction (found while building Mochi):** `internal_screen_width/height` is *not* a render-resolution setting. It's the display size shadPS4 reports to the game as the PS4 video-out port (`src/core/libraries/videoout/video_out.cpp`). Games still render at their own native resolution. shadPS4 currently has no resolution-scaling option, so the presets leave it alone.

## Fork roadmap

Keep the fork's own changes small and separate from upstream code, so pulling new shadPS4 versions stays easy (upstream asks contributors to rebase rather than merge).

### Phase 1 — Mochi, the launcher (done, no C++ changes)
Our own launcher with its own name and look, in `fork/mochi/`. shadPS4 is credited as the engine.

- **Start it:** double-click `fork\mochi\Mochi.cmd` (or `python fork\mochi\mochi.py`). It opens in its own Edge app window and quits about 15 seconds after the window closes.
- **What it does:**
  - reads the hardware and recommends a preset (integrated GPU → Performance)
  - applies Performance / Balanced / Quality to `config.json`, changing only the preset's keys
  - per game: Default or any preset, written to `custom_configs\<TITLE_ID>.json`
  - finds games in shadPS4's own game folders (`General.install_dirs`): any folder with `eboot.bin` and `sce_sys\param.sfo`, two levels deep, with cover art from `sce_sys\icon0.png`
  - fullscreen and FPS-counter toggles, Big Picture, and opening the settings folder
- **Files:**
  - `core.py`: all logic (PARAM.SFO parser, presets, config merging, game scan, launch). Tests: `python -m unittest test_core.py`
  - `mochi.py`: a local server on 127.0.0.1 with a random per-run token, plus native file pickers
  - `web/`: the UI (HTML/CSS/JS, light and dark themes, a mascot whose face follows the preset)
  - `presets/*.json`: the three presets, which are easy to edit
- **Checked:** the real `shadps4.exe` loads a Mochi-written config, keeps every preset value and the game folders, and fills its own defaults.
- **Not checked:** launching a real game (none available yet).

### Phase 2 — small C++ additions
- **Engine rebrand (done):**
  - mochi icon: `src/resources/mochi.png` for the window, `src/dist/mochi.ico` for the exe
  - window title "Mochi | <game> | engine: shadPS4 v…" (`src/emulator.cpp`)
  - Big Picture title and exit prompt (`src/imgui/big_picture/big_picture.cpp`)
  - SDL app name (`src/sdl_window.cpp`)
  - no-arguments message (`src/main.cpp`)
  - exe file details, keeping the shadPS4 Team copyright (`src/dist/shadps4.rc`)
  - The exe is still named `shadps4.exe`, and the original shadPS4 icon files are kept, which keeps upstream rebases simple. In-game, the window still shows the game's own icon when it has one.
- Real resolution scaling: render targets scaled in the texture cache (`src/video_core/texture_cache/`). This is a big job and the only way to make heavy games lighter on a Vega 7.
- Show the active Mochi preset in the FPS overlay (`src/imgui/`).

### Phase 3 — focus on one game (the bbport route)
Pick one game you own. Make it run well by fixing whatever emulation bugs it hits in `src/core/libraries/` (system libraries) or `src/video_core/` (graphics). This is how the Bloodborne ports grew out of shadPS4. Upstream accepts only real emulation fixes, not game-specific hacks (see `AGENTS.md`), so game-specific tweaks stay in the fork.

## Git setup for the fork
1. Fork shadps4-emu/shadPS4 on GitHub under your account.
2. In this checkout: `git remote rename origin upstream`, then add your fork as `origin`.
3. Work on a branch (for example `pc-tuning`), and update with `git fetch upstream` + `git rebase upstream/main`.
4. GPL-2.0 means anything you distribute must stay GPL with source available. Credit shadPS4, as bbport does.

## What you must supply yourself
- **Games:** decrypted dumps from a PS4 you own (usually requires a jailbroken console).
- **System modules / fonts:** some games need firmware modules dumped from your own PS4 into `user\sys_modules`.
These can't be downloaded legally, and this project won't help obtain them.
