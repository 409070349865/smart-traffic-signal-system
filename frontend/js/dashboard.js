/**
 * SignalSync Dashboard — Real-time WebSocket client
 * Handles: state rendering, canvas animation, controls, performance chart
 */

'use strict';

/* ─────────────────────── CONFIG ─────────────────────── */
const WS_URL   = `ws://${location.host}/ws/traffic`;
const API_BASE = `${location.protocol}//${location.host}/api`;
const DIRS     = ['NORTH', 'SOUTH', 'EAST', 'WEST'];
const DIR_COLORS = { NORTH: '#00e676', SOUTH: '#7c4dff', EAST: '#00e5ff', WEST: '#ff6d00' };
const VEHICLE_ICONS = ['🚗', '🚕', '🚙', '🚌', '🏍️', '🚐'];

/* ─────────────────────── STATE ──────────────────────── */
let ws        = null;
let lastState = null;
let paused    = false;
let perfHistory = { adaptive: Array(20).fill(30), fixed: Array(20).fill(30) };
let logEntries = [];
let animFrames = {};  // canvas animation frame ids per direction

/* ─────────────────────── WEBSOCKET ──────────────────── */
function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setConnected(true);
    addLog('WebSocket connected to SignalSync server.', 'highlight');
  };

  ws.onmessage = (evt) => {
    if (paused) return;
    try {
      const data = JSON.parse(evt.data);
      if (data.type === 'state') {
        lastState = data;
        renderState(data);
      }
    } catch (e) {
      console.warn('WS parse error:', e);
    }
  };

  ws.onclose = () => {
    setConnected(false);
    addLog('Connection lost. Reconnecting in 2s…', 'warn');
    setTimeout(connect, 2000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

function setConnected(ok) {
  const badge = document.getElementById('connBadge');
  const dot   = badge.querySelector('.dot');
  const lbl   = document.getElementById('connLabel');
  dot.classList.toggle('pulsing',       ok);
  dot.classList.toggle('disconnected', !ok);
  lbl.textContent  = ok ? 'LIVE' : 'DISCONNECTED';
  badge.style.color       = ok ? 'var(--green)' : 'var(--red)';
  badge.style.borderColor = ok ? 'rgba(0,230,118,.25)' : 'rgba(255,23,68,.25)';
  badge.style.background  = ok ? 'rgba(0,230,118,.08)' : 'rgba(255,23,68,.08)';
}

/* ─────────────────────── STATE RENDER ───────────────── */
function renderState(s) {
  const dir    = s.active_direction;
  const phase  = s.current_phase;
  const lanes  = s.lanes;

  // ── Header chips
  el('hdrPhase').textContent  = phase;
  el('hdrPhase').className    = `chip-val ${phaseColor(phase)}`;
  el('hdrCycle').textContent  = `#${s.cycle_number}`;
  el('hdrClients').textContent = '1';

  // ── Active approach
  const phaseRemaining = Math.ceil(s.phase_remaining);
  el('phaseTimer').textContent = phaseRemaining;
  el('phaseTimer').style.color = phaseHex(phase);
  el('approachDir').textContent = `LANE ${laneNum(dir)}: ${dir} (${phase} PHASE)`;
  el('approachDir').style.color = phaseHex(phase);

  // ── Per-lane rendering
  DIRS.forEach(d => {
    const lane = lanes[d];
    if (!lane) return;
    const isActive = d === dir;
    const lPhase   = isActive ? phase : 'RED';

    // Signal badge
    const sigEl = el(`sig-${d}`);
    sigEl.textContent = isActive
      ? `${phase} (${phaseRemaining}s)`
      : 'RED (WAIT)';
    sigEl.className = `signal-light ${lPhase.toLowerCase()}`;

    // Cam card highlight
    const camCard = el(`cam-${d}`);
    camCard.className = 'cam-card ' + (isActive
      ? (phase === 'GREEN' ? 'active-green' : phase === 'YELLOW' ? 'active-yellow' : 'active-red')
      : 'active-red');

    // Stats
    el(`cnt-${d}`).textContent  = lane.vehicle_count;
    el(`den-${d}`).textContent  = `${lane.density}%`;
    el(`tg-${d}`).textContent   = `${lane.t_green}s`;

    // Decision module (right column)
    el(`lv-${d}`).textContent   = lane.vehicle_count;
    el(`ld-${d}`).textContent   = `${lane.density}%`;
    el(`lw-${d}`).textContent   = `${lane.wait_time}s`;
    el(`ltg-${d}`).textContent  = `${lane.t_green}s`;

    const laneRow = el(`lr-${d}`);
    laneRow.classList.toggle('is-active', isActive && phase === 'GREEN');

    // Canvas animation
    drawCamCanvas(d, lane, isActive, phase);
  });

  // ── Junction diagram
  drawJunction(s);

  // ── Performance
  const adaptWait = s.adaptive_avg_wait;
  const fixedWait = s.fixed_avg_wait;
  el('adaptWait').textContent = `${adaptWait}s`;
  el('totalVeh').textContent  = s.total_vehicles_processed;

  const diff = fixedWait > 0
    ? Math.round((fixedWait - adaptWait) / fixedWait * 100)
    : 0;
  const perfBadge = el('perfBadge');
  perfBadge.textContent = `${diff >= 0 ? '+' : ''}${diff}% DIFF`;
  perfBadge.style.color = diff >= 0 ? 'var(--green)' : 'var(--red)';

  // ── Performance chart
  if (s.adaptive_throughput?.length) {
    perfHistory.adaptive = s.adaptive_throughput;
    perfHistory.fixed    = s.fixed_throughput;
  }
  drawPerfChart();

  // ── Timing sliders (sync from server once)
  if (s.timing) syncSliders(s.timing);
}

/* ─────────────────────── CAM CANVAS DRAW ────────────── */
function drawCamCanvas(dir, lane, isActive, phase) {
  const canvas = document.getElementById(`canvas-${dir}`);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;

  ctx.clearRect(0, 0, W, H);

  // Background
  ctx.fillStyle = '#070a10';
  ctx.fillRect(0, 0, W, H);

  // Road surface
  ctx.fillStyle = '#0e1520';
  ctx.fillRect(20, 20, W - 40, H - 40);

  // Road lines (dashed center)
  ctx.setLineDash([8, 6]);
  ctx.strokeStyle = '#1c2a3a';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(W / 2, 20); ctx.lineTo(W / 2, H - 20);
  ctx.stroke();
  ctx.moveTo(20, H / 2); ctx.lineTo(W - 20, H / 2);
  ctx.stroke();
  ctx.setLineDash([]);

  // ROI polygon overlay
  ctx.strokeStyle = isActive && phase === 'GREEN'
    ? 'rgba(0,230,118,.5)'
    : 'rgba(0,229,255,.2)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.rect(25, 25, W - 50, H - 50);
  ctx.stroke();

  // Vehicles
  const count = Math.min(lane.vehicle_count, 16);
  const cols = 4, rows = 4;
  const vw = (W - 50) / cols, vh = (H - 50) / rows;

  for (let i = 0; i < count; i++) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    const x = 28 + col * vw + vw * 0.2;
    const y = 28 + row * vh + vh * 0.15;

    // Vehicle color dot
    const vc = DIR_COLORS[dir];
    ctx.fillStyle = isActive ? vc : 'rgba(100,120,160,.6)';
    ctx.beginPath();
    ctx.roundRect(x, y, vw * 0.6, vh * 0.65, 2);
    ctx.fill();

    // Glow effect for active
    if (isActive && phase === 'GREEN') {
      ctx.shadowColor = vc;
      ctx.shadowBlur = 6;
      ctx.fillStyle = vc;
      ctx.beginPath();
      ctx.roundRect(x, y, vw * 0.6, vh * 0.65, 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  // Phase indicator strip
  const phColor = phase === 'GREEN' ? '#00e676'
                : phase === 'YELLOW' ? '#ffea00'
                : '#ff1744';
  ctx.fillStyle = isActive ? phColor : 'rgba(255,23,68,.4)';
  ctx.fillRect(0, H - 5, W, 5);

  // Density bar
  const barW = (lane.density / 100) * (W - 40);
  ctx.fillStyle = 'rgba(0,229,255,.1)';
  ctx.fillRect(20, 5, W - 40, 5);
  ctx.fillStyle = densityColor(lane.density);
  ctx.fillRect(20, 5, barW, 5);

  // Direction label
  ctx.fillStyle = isActive ? DIR_COLORS[dir] : 'rgba(100,120,160,.8)';
  ctx.font = 'bold 9px JetBrains Mono, monospace';
  ctx.fillText(dir.charAt(0), W - 16, H - 12);
}

function densityColor(d) {
  if (d > 70) return '#ff1744';
  if (d > 40) return '#ffea00';
  return '#00e676';
}

/* ─────────────────────── JUNCTION CANVAS ────────────── */
function drawJunction(s) {
  const canvas = document.getElementById('junctionCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const CX = W / 2, CY = H / 2;
  const ROAD_W = 80;

  ctx.clearRect(0, 0, W, H);

  // Background
  ctx.fillStyle = '#07090f';
  ctx.fillRect(0, 0, W, H);

  // Roads
  const drawRoad = (x1, y1, x2, y2) => {
    ctx.fillStyle = '#111827';
    if (x1 === x2) {
      ctx.fillRect(x1 - ROAD_W / 2, Math.min(y1, y2), ROAD_W, Math.abs(y2 - y1));
    } else {
      ctx.fillRect(Math.min(x1, x2), y1 - ROAD_W / 2, Math.abs(x2 - x1), ROAD_W);
    }
  };
  drawRoad(CX, 0, CX, H);
  drawRoad(0, CY, W, CY);

  // Intersection box
  ctx.fillStyle = '#16202e';
  ctx.fillRect(CX - ROAD_W / 2, CY - ROAD_W / 2, ROAD_W, ROAD_W);

  // Road markings
  ctx.setLineDash([10, 8]);
  ctx.strokeStyle = '#1e2f40';
  ctx.lineWidth = 1.5;
  // North-South center
  ctx.beginPath();
  ctx.moveTo(CX, 0); ctx.lineTo(CX, CY - ROAD_W / 2);
  ctx.moveTo(CX, CY + ROAD_W / 2); ctx.lineTo(CX, H);
  ctx.stroke();
  // East-West center
  ctx.beginPath();
  ctx.moveTo(0, CY); ctx.lineTo(CX - ROAD_W / 2, CY);
  ctx.moveTo(CX + ROAD_W / 2, CY); ctx.lineTo(W, CY);
  ctx.stroke();
  ctx.setLineDash([]);

  const active = s.active_direction;
  const phase  = s.current_phase;

  // Draw each lane's traffic flow and signal
  const laneConfigs = {
    NORTH: { ix: CX, iy: CY - ROAD_W / 2, lx: CX, ly: 30,       angle: Math.PI * 1.5, label: 'N' },
    SOUTH: { ix: CX, iy: CY + ROAD_W / 2, lx: CX, ly: H - 30,   angle: Math.PI * 0.5, label: 'S' },
    EAST:  { ix: CX + ROAD_W / 2, iy: CY, lx: W - 30, ly: CY,   angle: 0,             label: 'E' },
    WEST:  { ix: CX - ROAD_W / 2, iy: CY, lx: 30, ly: CY,       angle: Math.PI,       label: 'W' },
  };

  DIRS.forEach(dir => {
    const lc   = laneConfigs[dir];
    const lane = s.lanes[dir];
    if (!lane) return;
    const isAct = dir === active;
    const lPhase = isAct ? phase : 'RED';
    const color = isAct
      ? (phase === 'GREEN' ? '#00e676' : phase === 'YELLOW' ? '#ffea00' : '#ff1744')
      : 'rgba(255,23,68,.35)';

    // Flow indicator (line from intersection edge to lane)
    if (isAct && phase === 'GREEN') {
      ctx.strokeStyle = 'rgba(0,230,118,.25)';
      ctx.lineWidth = ROAD_W * 0.6;
      ctx.lineCap = 'butt';
      ctx.beginPath();
      ctx.moveTo(lc.ix, lc.iy);
      ctx.lineTo(lc.lx, lc.ly);
      ctx.stroke();
      ctx.lineCap = 'round';
    }

    // Vehicles along the road
    const count = Math.min(lane.vehicle_count, 8);
    for (let i = 0; i < count; i++) {
      const t = (i + 1) / 10;
      const vx = lc.ix + (lc.lx - lc.ix) * t;
      const vy = lc.iy + (lc.ly - lc.iy) * t;
      ctx.fillStyle = isAct ? DIR_COLORS[dir] : 'rgba(80,100,130,.7)';
      ctx.beginPath();
      ctx.arc(vx, vy, 5, 0, Math.PI * 2);
      ctx.fill();
    }

    // Signal head box
    const sx = lc.lx + (dir === 'EAST' ? -18 : dir === 'WEST' ? 18 : 0);
    const sy = lc.ly + (dir === 'NORTH' ? 18 : dir === 'SOUTH' ? -18 : 0);
    drawSignalHead(ctx, sx, sy, lPhase);

    // Direction label
    ctx.fillStyle = isAct ? color : 'rgba(100,120,150,.7)';
    ctx.font = `bold 11px Inter, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(dir, lc.lx, lc.ly + (dir === 'NORTH' ? 30 : dir === 'SOUTH' ? -30 : 0) + (dir === 'EAST' ? 0 : dir === 'WEST' ? 0 : 0));
    // Vehicle count
    ctx.font = `10px JetBrains Mono, monospace`;
    ctx.fillStyle = 'rgba(130,150,180,.9)';
    const lblOffset = 44;
    if (dir === 'NORTH') ctx.fillText(`${lane.vehicle_count}v`, lc.lx, lc.ly + lblOffset);
    if (dir === 'SOUTH') ctx.fillText(`${lane.vehicle_count}v`, lc.lx, lc.ly - lblOffset);
    if (dir === 'EAST')  ctx.fillText(`${lane.vehicle_count}v`, lc.lx - lblOffset, lc.ly);
    if (dir === 'WEST')  ctx.fillText(`${lane.vehicle_count}v`, lc.lx + lblOffset, lc.ly);
  });

  // Center intersection label
  ctx.fillStyle = 'rgba(0,229,255,.5)';
  ctx.font = 'bold 10px JetBrains Mono, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('⊕', CX, CY);

  // Phase timer overlay
  ctx.font = 'bold 13px JetBrains Mono, monospace';
  ctx.fillStyle = 'rgba(0,229,255,.7)';
  ctx.fillText(`${Math.ceil(s.phase_remaining)}s`, CX, CY + 14);

  ctx.textAlign = 'left';
}

function drawSignalHead(ctx, x, y, phase) {
  const r = 7;
  // Housing
  ctx.fillStyle = '#0a0d14';
  ctx.strokeStyle = '#1e2535';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(x - r - 2, y - r - 2, (r + 2) * 2, (r + 2) * 2, 3);
  ctx.fill();
  ctx.stroke();

  // Light
  const color = phase === 'GREEN' ? '#00e676'
              : phase === 'YELLOW' ? '#ffea00'
              : '#ff1744';
  ctx.fillStyle = color;
  ctx.shadowColor = color;
  ctx.shadowBlur = 10;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
}

/* ─────────────────────── PERFORMANCE CHART ──────────── */
function drawPerfChart() {
  const canvas = document.getElementById('perfChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const PAD = { top: 8, right: 8, bottom: 16, left: 32 };
  const cW = W - PAD.left - PAD.right;
  const cH = H - PAD.top - PAD.bottom;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#07090f';
  ctx.fillRect(0, 0, W, H);

  const adaptive = perfHistory.adaptive;
  const fixed    = perfHistory.fixed;
  const allVals  = [...adaptive, ...fixed].filter(v => v != null);
  const maxVal   = Math.max(...allVals, 40);
  const minVal   = Math.min(...allVals, 0);
  const range    = maxVal - minVal || 1;

  const xScale = (i) => PAD.left + (i / (adaptive.length - 1)) * cW;
  const yScale = (v) => PAD.top + cH - ((v - minVal) / range) * cH;

  // Grid lines
  ctx.strokeStyle = 'rgba(30,37,53,.8)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = PAD.top + (i / 4) * cH;
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
    ctx.fillStyle = 'rgba(100,120,150,.6)';
    ctx.font = '8px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(maxVal - (i / 4) * range), PAD.left - 3, y + 3);
  }

  // Fixed line
  ctx.strokeStyle = 'rgba(80,100,130,.6)';
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  fixed.forEach((v, i) => {
    if (v == null) return;
    const x = xScale(i), y = yScale(v);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  // Adaptive line with gradient fill
  const grad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + cH);
  grad.addColorStop(0, 'rgba(0,229,255,.25)');
  grad.addColorStop(1, 'rgba(0,229,255,0)');

  ctx.beginPath();
  adaptive.forEach((v, i) => {
    if (v == null) return;
    const x = xScale(i), y = yScale(v);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  // Close fill
  const lastX = xScale(adaptive.length - 1);
  ctx.lineTo(lastX, PAD.top + cH);
  ctx.lineTo(PAD.left, PAD.top + cH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Adaptive stroke
  ctx.strokeStyle = '#00e5ff';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#00e5ff';
  ctx.shadowBlur = 4;
  ctx.beginPath();
  adaptive.forEach((v, i) => {
    if (v == null) return;
    const x = xScale(i), y = yScale(v);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.shadowBlur = 0;

  ctx.textAlign = 'left';
}

/* ─────────────────────── CONTROLS ───────────────────── */
function togglePause() {
  paused = !paused;
  const btn = document.getElementById('btnPause');
  btn.textContent = paused ? '▶ RESUME' : '⏸ PAUSE';
  btn.classList.toggle('paused', paused);
  addLog(paused ? 'Dashboard PAUSED.' : 'Dashboard RESUMED.', paused ? 'warn' : 'highlight');
}

function inject(dir) {
  fetch(`${API_BASE}/traffic/inject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction: dir, count: 6 }),
  }).then(() => {
    addLog(`Injected +6 vehicles → ${dir}`, 'highlight');
    pulseButton(`inj${dir.charAt(0)}`);
  });
}

function setScenario(name) {
  fetch(`${API_BASE}/traffic/scenario`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }).then(() => {
    addLog(`Scenario: ${name}`, 'highlight');
    document.querySelectorAll('.scen-btn').forEach(b => {
      b.classList.toggle('active', b.textContent.trim() === name);
    });
  });
}

let timingSyncTimer = null;
function updateTiming() {
  const tmin    = parseFloat(document.getElementById('slTmin').value);
  const tmax    = parseFloat(document.getElementById('slTmax').value);
  const yellow  = parseFloat(document.getElementById('slYellow').value);
  const allRed  = parseFloat(document.getElementById('slAllRed').value);
  el('valTmin').textContent   = `${tmin}s`;
  el('valTmax').textContent   = `${tmax}s`;
  el('valYellow').textContent = `${yellow}s`;
  el('valAllRed').textContent = `${allRed}s`;

  clearTimeout(timingSyncTimer);
  timingSyncTimer = setTimeout(() => {
    fetch(`${API_BASE}/traffic/timing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ t_min: tmin, t_max: tmax, yellow_s: yellow, all_red_s: allRed }),
    }).then(() => addLog(`Timing updated: T_min=${tmin}s T_max=${tmax}s`, 'highlight'));
  }, 300);
}

let syncOnce = false;
function syncSliders(timing) {
  if (syncOnce) return;  // only sync on first message
  syncOnce = true;
  document.getElementById('slTmin').value    = timing.t_min;
  document.getElementById('slTmax').value    = timing.t_max;
  document.getElementById('slYellow').value  = timing.yellow_s;
  document.getElementById('slAllRed').value  = timing.all_red_s;
  el('valTmin').textContent   = `${timing.t_min}s`;
  el('valTmax').textContent   = `${timing.t_max}s`;
  el('valYellow').textContent = `${timing.yellow_s}s`;
  el('valAllRed').textContent = `${timing.all_red_s}s`;
}

function forceNext() {
  addLog('Manual phase advance requested.', 'warn');
  // We just inject a large burst into all lanes to force re-allocation; 
  // or send a custom signal (future: /api/traffic/force-next endpoint)
  inject('NORTH');
}

function testAdaptive() {
  setScenario('N-S Highway Rush');
  setTimeout(() => setScenario('Balanced Flow'), 6000);
  addLog('Testing adaptive response: N-S Rush → Balanced (6s)', 'highlight');
}

/* ─────────────────────── LOG ─────────────────────────── */
function addLog(msg, type = '') {
  const now = new Date();
  const ts  = `[${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}]`;
  logEntries.unshift({ ts, msg, type });
  if (logEntries.length > 20) logEntries.pop();
  renderLog();
}

function renderLog() {
  const container = document.getElementById('logEntries');
  if (!container) return;
  container.innerHTML = logEntries.slice(0, 8).map(e => `
    <div class="log-entry">
      <span class="log-ts">${e.ts}</span>
      <span class="${e.type === 'highlight' ? 'log-highlight' : e.type === 'warn' ? 'log-warn' : ''}">${e.msg}</span>
    </div>
  `).join('');
}

/* ─────────────────────── HELPERS ─────────────────────── */
function el(id) { return document.getElementById(id); }

function phaseColor(phase) {
  return phase === 'GREEN' ? 'green' : phase === 'YELLOW' ? 'yellow' : 'red';
}
function phaseHex(phase) {
  return phase === 'GREEN' ? 'var(--green)' : phase === 'YELLOW' ? 'var(--yellow)' : 'var(--red)';
}

function laneNum(dir) {
  return { NORTH: 1, SOUTH: 2, EAST: 3, WEST: 4 }[dir] || 1;
}

function pulseButton(id) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.style.transform = 'scale(1.15)';
  btn.style.boxShadow = '0 0 12px rgba(0,229,255,.6)';
  setTimeout(() => {
    btn.style.transform = '';
    btn.style.boxShadow = '';
  }, 300);
}

/* ─────────────────────── INIT ────────────────────────── */
window.addEventListener('DOMContentLoaded', () => {
  addLog('SignalSync dashboard loaded.', 'highlight');
  addLog('Connecting to WebSocket server…');
  connect();

  // Start drawing canvases with placeholder data immediately
  DIRS.forEach(d => {
    const canvas = document.getElementById(`canvas-${d}`);
    if (canvas) {
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#070a10';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = 'rgba(0,229,255,.3)';
      ctx.font = '11px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.fillText('AWAITING FEED…', canvas.width / 2, canvas.height / 2);
    }
  });
});
