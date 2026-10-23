// Mochi UI. Talks to mochi.py over a small local JSON API.

const token = new URLSearchParams(location.search).get('t') || sessionStorage.getItem('mochi-token') || '';
sessionStorage.setItem('mochi-token', token);

const $ = id => document.getElementById(id);
let state = null;
let hardware = null;
let selectedId = null;

async function api(path, body) {
  let res;
  try {
    res = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'X-Mochi-Token': token, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('Mochi has stopped running. Close this window and open Mochi again.');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function act(path, body = {}, success) {
  try {
    state = await api(path, body);
    render();
    if (success) toast(success);
    return true;
  } catch (err) {
    toast(err.message, true);
    return false;
  }
}

function graphicsChoices() {
  const choices = {};
  for (const section of state.graphics) {
    for (const setting of section.settings) choices[setting.id] = setting;
  }
  return choices;
}

// Presets rewrite some Graphics & Display settings; say which ones and highlight them
async function applyPreset(preset) {
  const before = graphicsChoices();
  if (!await act('/api/preset', { preset: preset.id })) return;
  const changed = Object.values(graphicsChoices()).filter(s => before[s.id]?.current !== s.current);
  toast(changed.length
    ? `${preset.name} is now the default. Changed: ${changed.map(s => `${s.name} → ${s.options[s.current]?.label ?? 'custom'}`).join(', ')}`
    : `${preset.name} is now the default. Your settings already matched it.`);
  for (const s of changed) {
    const row = document.querySelector(`.gfx-row[data-setting="${s.id}"]`);
    row?.classList.add('changed');
    setTimeout(() => row?.classList.remove('changed'), 3000);
  }
}

// ---- Mascot -----------------------------------------------------------------
// One soft mochi whose face follows the chosen preset.

function mascotSvg() {
  const ink = 'var(--ink-line, #2E2A4F)';
  return `
<svg viewBox="0 0 120 100" xmlns="http://www.w3.org/2000/svg">
  <ellipse cx="60" cy="92" rx="38" ry="5" fill="#2E2A4F" opacity=".12"/>
  <g data-mood="performance" class="zoom" stroke="var(--ink-soft)" stroke-width="3" stroke-linecap="round">
    <path d="M2 52h9M0 64h12M4 76h8"/>
  </g>
  <g class="body-group">
    <path d="M60 19c3-8 10-12 18-11-2 8-9 12-18 11z" fill="#8CC489" stroke="${ink}" stroke-width="3" stroke-linejoin="round"/>
    <path d="M14 78C14 40 34 19 60 19s46 21 46 59c0 10-8 12-46 12S14 88 14 78z" fill="var(--body)" stroke="${ink}" stroke-width="3.5"/>
    <ellipse cx="40" cy="36" rx="9" ry="4.5" fill="#fff" transform="rotate(-25 40 36)"/>
    <ellipse cx="36" cy="67" rx="7.5" ry="4.5" fill="#F7A9BF"/>
    <ellipse cx="84" cy="67" rx="7.5" ry="4.5" fill="#F7A9BF"/>

    <g data-mood="balanced" fill="${ink}">
      <circle cx="46" cy="57" r="4.4"/><circle cx="74" cy="57" r="4.4"/>
      <path d="M55 66q5 5 10 0" fill="none" stroke="${ink}" stroke-width="3" stroke-linecap="round"/>
    </g>
    <g data-mood="performance" fill="none" stroke="${ink}" stroke-width="3.2" stroke-linecap="round">
      <path d="M41 58q5-7 10 0M69 58q5-7 10 0"/>
      <path d="M54 65q6 8 12 0z" fill="${ink}"/>
    </g>
    <g data-mood="quality" fill="${ink}">
      <path d="M46 50l1.6 5.4L53 57l-5.4 1.6L46 64l-1.6-5.4L39 57l5.4-1.6z"/>
      <path d="M74 50l1.6 5.4L81 57l-5.4 1.6L74 64l-1.6-5.4L67 57l5.4-1.6z"/>
      <path d="M56 66q4 4 8 0" fill="none" stroke="${ink}" stroke-width="3" stroke-linecap="round"/>
    </g>
    <g data-mood="sleepy" fill="none" stroke="${ink}" stroke-width="3" stroke-linecap="round">
      <path d="M41 58q5 3 10 0M69 58q5 3 10 0"/>
      <path d="M57 68q3 2 6 0"/>
    </g>
  </g>
  <path data-mood="quality" class="twinkle" d="M104 12l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" fill="#F2C46B"/>
  <text data-mood="sleepy" class="zzz" x="98" y="30" font-family="M PLUS Rounded 1c, sans-serif" font-weight="800" font-size="14" fill="var(--ink-soft)">z z</text>
</svg>`;
}

$('mascot').innerHTML = mascotSvg();
$('empty-mascot').innerHTML = mascotSvg();
$('empty-mascot').dataset.mood = 'sleepy';

// ---- Rendering --------------------------------------------------------------

function render() {
  const s = state;
  $('exe-banner').hidden = s.exeFound;
  $('mascot').dataset.mood = s.preset || 'balanced';

  // Default preset cards
  const recommended = hardware?.recommended;
  $('presets').innerHTML = '';
  for (const p of s.presets) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'preset';
    card.setAttribute('role', 'radio');
    card.setAttribute('aria-checked', String(p.id === s.preset));
    card.innerHTML = `<span class="preset-name">${esc(p.name)}${p.id === recommended ? '<span class="badge">Best for this PC</span>' : ''}</span>
      <span class="preset-summary">${esc(p.summary)}</span>`;
    card.addEventListener('click', () => applyPreset(p));
    $('presets').append(card);
  }
  const current = s.presets.find(p => p.id === s.preset);
  $('preset-details').innerHTML = current
    ? current.details.map(d => `<li>${esc(d)}</li>`).join('')
    : '<li>Your current settings don\'t match a preset. Pick one to apply it.</li>';

  $('show-fps').checked = s.showFps;
  renderGraphics();

  // Folders
  $('folders').innerHTML = '';
  for (const folder of s.folders) {
    const chip = document.createElement('span');
    chip.className = 'folder-chip';
    chip.innerHTML = `<span title="${esc(folder)}">${esc(folder)}</span><button type="button" aria-label="Remove ${esc(folder)}">×</button>`;
    chip.querySelector('button').addEventListener('click', () =>
      act('/api/folders/remove', { path: folder }, 'Folder removed'));
    $('folders').append(chip);
  }

  // Games
  $('grid').innerHTML = '';
  $('empty').hidden = s.games.length > 0;
  for (const g of s.games) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'game';
    card.setAttribute('aria-pressed', String(g.id === selectedId));
    card.innerHTML = `
      <div class="cover">${g.hasIcon ? `<img src="${iconUrl(g)}" alt="" loading="lazy">` : esc(initials(g.title))}</div>
      <div class="game-title">${esc(g.title)}</div>
      <div class="game-meta"><span>${esc(g.serial)}</span>${presetChip(g.preset)}</div>`;
    card.addEventListener('click', () => select(g.id));
    card.addEventListener('dblclick', () => play(g));
    $('grid').append(card);
  }

  if (selectedId !== null && !s.games.some(g => g.id === selectedId)) selectedId = null;
  renderDock();
}

function renderDock() {
  const game = state.games.find(g => g.id === selectedId);
  $('dock').classList.toggle('open', !!game);
  $('dock').setAttribute('aria-hidden', String(!game));
  if (!game) return;

  $('dock-cover').src = game.hasIcon ? iconUrl(game) : 'mochi.svg';
  $('dock-title').textContent = game.title;
  $('dock-meta').textContent = [game.serial, game.version && `v${game.version}`].filter(Boolean).join(' · ');
  $('play').disabled = !state.exeFound;

  const options = [{ id: null, name: 'Default' }, ...state.presets];
  $('game-preset').innerHTML = '';
  for (const opt of options) {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'radio');
    const active = opt.id === null ? game.preset === 'default' : game.preset === opt.id;
    b.setAttribute('aria-checked', String(active));
    b.textContent = opt.name;
    b.addEventListener('click', () => act('/api/game-preset', { serial: game.serial, preset: opt.id },
      opt.id ? `${game.title} will use ${opt.name}` : `${game.title} follows the default`));
    $('game-preset').append(b);
  }
}

function renderGraphics() {
  $('graphics').innerHTML = '';
  for (const section of state.graphics) {
    const panel = document.createElement('section');
    panel.className = 'panel gfx-section';
    panel.innerHTML = `<h3>${esc(section.name)}</h3>`;
    for (const setting of section.settings) {
      const row = document.createElement('div');
      row.className = 'gfx-row';
      row.dataset.setting = setting.id;
      const presetTag = setting.byPreset
        ? ' <span class="chip" title="Picking a preset under “How games run” changes this setting">Preset</span>'
        : '';
      row.innerHTML = `<div><div class="gfx-name">${esc(setting.name)}${presetTag}</div><div class="gfx-help">${esc(setting.help)}</div></div>`;

      const group = document.createElement('div');
      group.className = 'segmented';
      group.setAttribute('role', 'radiogroup');
      group.setAttribute('aria-label', setting.name);
      setting.options.forEach((option, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-checked', String(i === setting.current));
        b.textContent = option.label;
        b.addEventListener('click', () => act('/api/graphics', { setting: setting.id, option: i },
          `${setting.name}: ${option.label}`));
        group.append(b);
      });
      row.append(group);

      const choice = document.createElement('div');
      const current = setting.options[setting.current];
      choice.className = current ? 'gfx-choice' : 'gfx-choice custom';
      choice.textContent = current ? current.help : 'config.json has a value Mochi doesn\'t list. Pick an option to replace it.';
      row.append(choice);
      panel.append(row);
    }
    $('graphics').append(panel);
  }
}

function showTab(name) {
  const graphics = name === 'graphics';
  $('tab-games').setAttribute('aria-selected', String(!graphics));
  $('tab-graphics').setAttribute('aria-selected', String(graphics));
  $('games-view').hidden = graphics;
  $('graphics-view').hidden = !graphics;
  $('add-folder').hidden = graphics;
  if (graphics && selectedId !== null) select(selectedId);
}

function renderHardware() {
  const h = hardware;
  const display = h.display.width ? `${h.display.width}×${h.display.height} at ${h.display.hz} Hz` : 'Unknown';
  $('pc-list').innerHTML = `
    <dt>CPU</dt><dd>${esc(h.cpu)} <span class="muted">(${h.cores} cores, ${h.threads} threads)</span></dd>
    <dt>Graphics</dt><dd>${esc(h.gpu)} <span class="muted">(${h.integrated ? 'integrated' : 'dedicated'})</span></dd>
    <dt>Memory</dt><dd>${h.ramGb} GB</dd>
    <dt>Display</dt><dd>${display}</dd>`;
  $('pc-notes').innerHTML = h.notes.map(n => `<li class="good">${esc(n)}</li>`).join('')
    + h.warnings.map(w => `<li>${esc(w)}</li>`).join('');
}

function presetChip(preset) {
  if (preset === 'default') return '<span class="chip default">Default</span>';
  if (preset === 'custom') return '<span class="chip">Custom</span>';
  return `<span class="chip">${esc(preset[0].toUpperCase() + preset.slice(1))}</span>`;
}

const iconUrl = g => `/api/icon/${g.id}?t=${encodeURIComponent(token)}`;
const initials = title => title.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---- Actions ----------------------------------------------------------------

function select(id) {
  selectedId = selectedId === id ? null : id;
  render();
}

async function play(game) {
  try {
    await api('/api/play', { id: game.id });
    toast(`Starting ${game.title}…`);
  } catch (err) {
    toast(err.message, true);
  }
}

let toastTimer;
function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), Math.max(isError ? 5000 : 2600, message.length * 50));
}

$('tab-games').addEventListener('click', () => showTab('games'));
$('tab-graphics').addEventListener('click', () => showTab('graphics'));
$('show-fps').addEventListener('change', e => act('/api/options', { showFps: e.target.checked }));
$('add-folder').addEventListener('click', () => act('/api/folders/add', {}, 'Game folders updated'));
$('empty-add').addEventListener('click', () => act('/api/folders/add', {}, 'Game folders updated'));
$('choose-exe').addEventListener('click', () => act('/api/exe/choose'));
$('open-config').addEventListener('click', () => act('/api/open-config', {}, 'Opened the shadPS4 settings folder in File Explorer'));
$('big-picture').addEventListener('click', () => act('/api/big-picture', {}, 'Opening Big Picture…'));
$('play').addEventListener('click', () => {
  const game = state.games.find(g => g.id === selectedId);
  if (game) play(game);
});
$('dock-close').addEventListener('click', () => select(selectedId));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && selectedId !== null) select(selectedId);
});

// ---- Start ------------------------------------------------------------------

(async () => {
  try {
    state = await api('/api/state');
    render();
  } catch (err) {
    toast(`Mochi couldn't load: ${err.message}`, true);
    return;
  }
  try {
    hardware = await api('/api/hardware');
    renderHardware();
    // First run: nothing applied yet, so start with what suits this PC
    if (!state.preset) await act('/api/preset', { preset: hardware.recommended }, `Set up for this PC: ${hardware.recommended}`);
    else render();
  } catch (err) {
    $('pc-list').innerHTML = `<div class="loading">Couldn't read hardware details (${esc(err.message)}).</div>`;
  }
})();

// Tell mochi.py the window is still open; it quits shortly after this connection closes
new EventSource(`/api/alive?t=${encodeURIComponent(token)}`);
