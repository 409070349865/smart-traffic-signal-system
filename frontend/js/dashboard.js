/**
 * SignalSync Dashboard — Complete 8-Step Intelligent Traffic Control System
 * Real-time WebSocket clients (Traffic State & OpenCV Video Stream),
 * 2D 4-Way Junction Canvas, Multi-Lane Vision Perspective Feeds,
 * Emergency Preemption, Yellow-Box Gridlock Guard, and Telemetry.
 */

'use strict';

/* ─────────────────────── CONFIG & GLOBALS ─────────────────────── */
const WS_TRAFFIC_URL = `ws://${location.host}/ws/traffic`;
const WS_VIDEO_URL   = `ws://${location.host}/ws/video`;
const API_BASE       = `${location.protocol}//${location.host}/api`;

const DIRS = ['NORTH', 'SOUTH', 'EAST', 'WEST'];
const DIR_COLORS = {
  NORTH: '#00e676',
  SOUTH: '#7c4dff',
  EAST:  '#00e5ff',
  WEST:  '#ff7b00'
};

let wsTraffic = null;
let wsVideo   = null;
let isPaused  = false;
let visionTab = 'rgb'; // 'rgb' | 'gray' | 'mog2' | 'grid'
let lastTrafficState = null;
let lastVideoFrame   = null;
let webcamStream     = null;

// Real-time chart data history
let perfChartHistory = {
  adaptive: [186, 185, 184, 186, 187, 185, 184, 186, 186, 185, 186],
  fixed:    [191, 192, 190, 191, 193, 192, 191, 191, 192, 190, 191]
};

// Simulation vehicles for Junction Canvas
let junctionVehicles = [];
let junctionPedestrians = [];
let gridlockCar = null;

/* ─────────────────────── WEBSOCKET: TRAFFIC STATE ─────────────────────── */
function connectTrafficWS() {
  wsTraffic = new WebSocket(WS_TRAFFIC_URL);

  wsTraffic.onopen = () => {
    updateConnPill(true);
  };

  wsTraffic.onmessage = (evt) => {
    if (isPaused) return;
    try {
      const data = JSON.parse(evt.data);
      if (data.type === 'state') {
        lastTrafficState = data;
        renderTrafficState(data);
      }
    } catch (e) {
      console.error('Error parsing traffic WS payload:', e);
    }
  };

  wsTraffic.onclose = () => {
    updateConnPill(false);
    setTimeout(connectTrafficWS, 2000);
  };

  wsTraffic.onerror = () => {
    wsTraffic.close();
  };
}

function updateConnPill(connected) {
  const pill = document.getElementById('connPill');
  const dot  = pill.querySelector('.dot');
  const lbl  = document.getElementById('connLabel');
  if (connected) {
    dot.className = 'dot pulsing';
    lbl.textContent = 'LIVE 16 FPS';
    pill.style.color = 'var(--green)';
    pill.style.borderColor = 'rgba(0,230,118,0.3)';
    pill.style.background = 'var(--green-bg)';
  } else {
    dot.className = 'dot';
    lbl.textContent = 'CONNECTING…';
    pill.style.color = 'var(--yellow)';
    pill.style.borderColor = 'rgba(255,234,0,0.3)';
    pill.style.background = 'var(--yellow-bg)';
  }
}

/* ─────────────────────── WEBSOCKET: OPENCV VIDEO STREAM ─────────────────────── */
function connectVideoWS() {
  wsVideo = new WebSocket(WS_VIDEO_URL);

  wsVideo.onmessage = (evt) => {
    if (isPaused) return;
    try {
      const data = JSON.parse(evt.data);
      if (data.type === 'video_frame') {
        lastVideoFrame = data;
        renderVideoFrame(data);
      }
    } catch (e) {
      console.error('Error parsing video WS payload:', e);
    }
  };

  wsVideo.onclose = () => {
    setTimeout(connectVideoWS, 2500);
  };
}

function renderVideoFrame(data) {
  const dock = document.getElementById('videoMonitorDock');
  if (dock.style.display === 'none') {
    dock.style.display = 'block';
  }

  // Draw base64 JPEG to video canvas
  const canvas = document.getElementById('videoCanvas');
  const ctx = canvas.getContext('2d');
  const img = new Image();
  img.onload = () => {
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  };
  img.src = `data:image/jpeg;base64,${data.jpeg_b64}`;

  // Update HUD
  const fpsEl = document.getElementById('dockFps');
  if (fpsEl && data.fps) fpsEl.textContent = `${data.fps.toFixed(1)} FPS`;

  const totalEl = document.getElementById('vHudTotal');
  if (totalEl) totalEl.textContent = data.vehicle_count || 0;

  if (data.quadrant_counts) {
    el('vHudN').textContent = data.quadrant_counts.NORTH || 0;
    el('vHudS').textContent = data.quadrant_counts.SOUTH || 0;
    el('vHudE').textContent = data.quadrant_counts.EAST || 0;
    el('vHudW').textContent = data.quadrant_counts.WEST || 0;
  }
}

/* ─────────────────────── TRAFFIC STATE RENDERER ─────────────────────── */
function renderTrafficState(s) {
  const activeDir = s.active_direction;
  const phase = s.current_phase;
  const remaining = Math.ceil(s.phase_remaining);

  // 1. Active Phase Indicator Bar
  const phaseNameEl = el('activeLaneName');
  if (phaseNameEl) {
    phaseNameEl.textContent = `LANE ${laneNumber(activeDir)}: ${activeDir} (${phase})`;
    phaseNameEl.style.color = phase === 'GREEN' ? 'var(--green)' : phase === 'YELLOW' ? 'var(--yellow)' : 'var(--red)';
  }

  const cdValEl = el('phaseCountdownVal');
  if (cdValEl) {
    cdValEl.innerHTML = `${remaining}<span class="cd-unit">s</span>`;
  }

  // LED lights on active phase bar
  const leds = document.getElementById('phaseLeds');
  if (leds) {
    leds.querySelectorAll('.pled').forEach(p => p.classList.remove('active'));
    if (phase === 'GREEN') leds.querySelector('.pled.green')?.classList.add('active');
    else if (phase === 'YELLOW') leds.querySelector('.pled.yellow')?.classList.add('active');
    else leds.querySelector('.pled.red')?.classList.add('active');
  }

  // 2. Next Priority Banner
  if (s.next_priority) {
    el('nextPriorityVal').textContent = s.next_priority;
  }

  // 3. Canvas Badges (Yellow-box, Pedestrians)
  const ybBadge = el('yellowBoxBadge');
  if (ybBadge) {
    if (s.yellow_box_blocked || s.gridlock_alert) {
      ybBadge.textContent = 'Yellow-Box: GRIDLOCK ALERT';
      ybBadge.classList.add('alert');
    } else {
      ybBadge.textContent = 'Yellow-Box: CLEAR';
      ybBadge.classList.remove('alert');
    }
  }

  const curbBadge = el('curbPedsBadge');
  if (curbBadge) {
    curbBadge.textContent = `Curb Pedestrians: ${s.curb_pedestrians || 0}`;
  }
  const pedCountBtn = el('pedDemandCount');
  if (pedCountBtn) {
    pedCountBtn.textContent = s.curb_pedestrians || 0;
  }

  // 4. Emergency Ambulance Preemption Status
  const ambBadge = el('ambStatusBadge');
  if (ambBadge) {
    if (s.ambulance_active) {
      ambBadge.textContent = `PREEMPTING (${s.ambulance_direction} ${Math.ceil(s.ambulance_countdown)}s)`;
      ambBadge.style.color = '#fff';
      ambBadge.style.background = 'var(--red)';
    } else {
      ambBadge.textContent = 'STANDBY';
      ambBadge.style.color = 'var(--red)';
      ambBadge.style.background = 'rgba(255,23,68,0.2)';
    }
  }

  // Highlight active emergency button
  DIRS.forEach(d => {
    const btn = el(`ambBtn${d.charAt(0)}`);
    if (btn) {
      btn.classList.toggle('active', s.ambulance_active && s.ambulance_direction === d);
    }
  });

  // 5. Per-Approach Cards & Cameras
  DIRS.forEach(d => {
    const lane = s.lanes[d];
    if (!lane) return;

    const isActive = (d === activeDir);
    const lPhase = isActive ? phase : 'RED';

    // Camera header badge
    const sigBadge = el(`camSig-${d}`);
    if (sigBadge) {
      sigBadge.textContent = isActive ? `${phase} (${remaining}s)` : 'RED (WAIT)';
      sigBadge.className = `cam-sig-badge ${lPhase.toLowerCase()}`;
    }

    // Camera stats footer
    el(`camVeh-${d}`).textContent = `${lane.vehicle_count} (${lane.pcu.toFixed(1)} PCU)`;
    el(`camDen-${d}`).textContent = `${Math.round(lane.density)}%`;
    el(`camTg-${d}`).textContent  = `${Math.round(lane.t_green)}s`;

    // Right Column PCU Card
    const pcuCard = el(`pcuCard-${d}`);
    if (pcuCard) {
      pcuCard.classList.toggle('active-corridor', isActive && phase === 'GREEN');
      pcuCard.classList.toggle('starvation-boost', lane.is_starved);
    }

    el(`pcuTg-${d}`).textContent = `T_grn: ${Math.round(lane.t_green)}s`;
    el(`pcuDetail-${d}`).textContent = `B:${lane.bikes} C:${lane.cars} H:${lane.buses} ~ ${lane.pcu.toFixed(1)} PCU`;
    el(`pcuWait-${d}`).textContent = `RedWait: ${Math.round(lane.red_wait)}s`;
    el(`pcuDenVal-${d}`).textContent = `${Math.round(lane.density)}%`;
    el(`pcuFill-${d}`).style.width = `${Math.min(100, Math.max(5, lane.density))}%`;
    el(`pcuScore-${d}`).textContent = lane.priority_score.toFixed(1);

    const starveTag = el(`tagStarve-${d}`);
    if (starveTag) {
      starveTag.style.display = lane.is_starved ? 'inline-block' : 'none';
    }
  });

  // 6. Multi-junction Green Wave Corridor
  if (s.corridor_eta_s !== undefined) {
    el('gwPcu').textContent = `${s.corridor_platoon_pcu} PCU`;
    el('gwEta').textContent = `${Math.ceil(s.corridor_eta_s)}s`;
  }

  // 7. Feature F: Eco Telemetry
  if (s.fuel_saved_ml !== undefined) {
    el('ecoFuelSaved').textContent = `${Math.round(s.fuel_saved_ml)} mL`;
    el('ecoFuelLiters').textContent = `${(s.fuel_saved_ml / 1000).toFixed(3)} L`;
    el('ecoCo2Avoided').textContent = `${Math.round(s.co2_avoided_g)} g`;
    el('ecoCo2Kg').textContent = `${(s.co2_avoided_g / 1000).toFixed(3)} kg`;
    el('ecoValueSaved').textContent = `₹${s.value_saved_inr.toFixed(2)}`;
  }

  // 8. Step 8: Performance Evaluation
  if (s.adaptive_avg_wait !== undefined) {
    el('perfAdaptWait').textContent = `${s.adaptive_avg_wait}s`;
    el('perfFixedWait').textContent = `${s.fixed_avg_wait}s`;
    el('perfQueueLoad').textContent = `${s.active_queue_load} veh`;
    el('perfFixedQueue').textContent = `${s.fixed_queue_load} veh`;

    const diffPct = Math.round(((s.adaptive_avg_wait - s.fixed_avg_wait) / s.fixed_avg_wait) * 100);
    el('perfDiffPill').textContent = `${diffPct}% Wait`;

    // Append to chart history
    perfChartHistory.adaptive.push(s.adaptive_avg_wait);
    perfChartHistory.fixed.push(s.fixed_avg_wait);
    if (perfChartHistory.adaptive.length > 25) {
      perfChartHistory.adaptive.shift();
      perfChartHistory.fixed.shift();
    }
  }

  // 9. Incident Log
  if (s.incidents) {
    renderIncidents(s.incidents);
  }

  // 10. Decision Module Log
  if (s.decision_logs) {
    renderDecisionLogs(s.decision_logs);
  }
  if (s.total_vehicles_processed !== undefined) {
    el('clearedVehCount').textContent = `${s.total_vehicles_processed} veh`;
  }
}

function laneNumber(dir) {
  switch (dir) {
    case 'NORTH': return '1';
    case 'SOUTH': return '2';
    case 'EAST':  return '3';
    case 'WEST':  return '4';
    default: return '1';
  }
}

/* ─────────────────────── INCIDENTS & DECISION LOGS ─────────────────────── */
function renderIncidents(incidents) {
  const container = document.getElementById('incidentList');
  if (!container) return;

  container.innerHTML = incidents.slice(0, 4).map(inc => {
    const isStalled = inc.incident_type.includes('STALLED');
    const typeClass = isStalled ? 'stalled' : 'red_violation';
    const icon = isStalled ? '⚠️' : '🚨';
    return `
      <div class="incident-card">
        <div class="inc-thumb">${icon}</div>
        <div class="inc-info">
          <div class="inc-title ${typeClass}">${inc.incident_type} • ${inc.direction}</div>
          <div class="inc-desc">${inc.description}</div>
        </div>
        <div class="inc-time">${inc.timestamp}</div>
      </div>
    `;
  }).join('');
}

function renderDecisionLogs(logs) {
  const container = document.getElementById('logTerminal');
  if (!container) return;

  container.innerHTML = logs.map(line => {
    let colored = line;
    if (line.includes('STARVATION BOOST')) {
      colored = colored.replace(/\[STARVATION BOOST\]/g, '<span class="starve">[STARVATION BOOST]</span>');
    }
    if (line.includes('Feature E Crosswalk')) {
      colored = `<span class="ped">${colored}</span>`;
    }
    if (line.includes('Feature B AMBULANCE')) {
      colored = `<span class="amb">${colored}</span>`;
    }
    return `<div class="log-line">${colored}</div>`;
  }).join('');
}

/* ─────────────────────── 4-WAY JUNCTION CANVAS SIMULATION ─────────────────────── */
function initJunctionSimulation() {
  const canvas = document.getElementById('junctionCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // Spawn initial vehicles
  spawnInitialJunctionVehicles();

  function animate() {
    drawJunction(ctx, canvas.width, canvas.height);
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);
}

function spawnInitialJunctionVehicles() {
  junctionVehicles = [];
  DIRS.forEach(dir => {
    for (let i = 0; i < 4; i++) {
      junctionVehicles.push(createVehicle(dir, i));
    }
  });
}

function createVehicle(dir, queueIndex = 0, type = null) {
  if (!type) {
    const types = ['bike', 'car', 'bus'];
    const weights = [0.35, 0.5, 0.15];
    const r = Math.random();
    type = r < weights[0] ? 'bike' : r < (weights[0] + weights[1]) ? 'car' : 'bus';
  }

  let length = 18;
  let width = 10;
  let color = '#00e676';
  let pcu = 1.0;

  if (type === 'bike') {
    length = 10; width = 5; color = '#00e5ff'; pcu = 0.5;
  } else if (type === 'bus') {
    length = 32; width = 13; color = '#ff7b00'; pcu = 3.0;
  } else if (type === 'amb') {
    length = 24; width = 12; color = '#ff1744'; pcu = 4.0;
  }

  return {
    dir,
    type,
    queueIndex,
    progress: Math.max(0, 140 - queueIndex * (length + 8)), // distance from stopline
    speed: 1.2,
    passedStopLine: false,
    length,
    width,
    color,
    pcu
  };
}

function drawJunction(ctx, W, H) {
  ctx.clearRect(0, 0, W, H);
  const cx = W / 2;
  const cy = H / 2;
  const roadW = 100;
  const halfRoad = roadW / 2;

  // 1. Asphalt Background
  ctx.fillStyle = '#06080e';
  ctx.fillRect(0, 0, W, H);

  // 2. Road arms (cross intersection)
  ctx.fillStyle = '#111522';
  ctx.fillRect(cx - halfRoad, 0, roadW, H); // N-S Road
  ctx.fillRect(0, cy - halfRoad, W, roadW); // E-W Road

  // 3. Road Markings & Zebra Crossings
  ctx.strokeStyle = '#222b40';
  ctx.lineWidth = 1;

  // Outer curbs
  ctx.strokeRect(cx - halfRoad, 0, roadW, H);
  ctx.strokeRect(0, cy - halfRoad, W, roadW);

  // Dashed white lane dividers
  ctx.setLineDash([8, 8]);
  ctx.strokeStyle = 'rgba(255,255,255,0.2)';
  ctx.lineWidth = 1.5;

  // North lane divider
  ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, cy - halfRoad - 20); ctx.stroke();
  // South lane divider
  ctx.beginPath(); ctx.moveTo(cx, cy + halfRoad + 20); ctx.lineTo(cx, H); ctx.stroke();
  // West lane divider
  ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(cx - halfRoad - 20, cy); ctx.stroke();
  // East lane divider
  ctx.beginPath(); ctx.moveTo(cx + halfRoad + 20, cy); ctx.lineTo(W, cy); ctx.stroke();
  ctx.setLineDash([]);

  // Solid White Stop Lines
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 3;
  // North Stop line
  ctx.beginPath(); ctx.moveTo(cx, cy - halfRoad); ctx.lineTo(cx + halfRoad, cy - halfRoad); ctx.stroke();
  // South Stop line
  ctx.beginPath(); ctx.moveTo(cx - halfRoad, cy + halfRoad); ctx.lineTo(cx, cy + halfRoad); ctx.stroke();
  // East Stop line
  ctx.beginPath(); ctx.moveTo(cx + halfRoad, cy); ctx.lineTo(cx + halfRoad, cy + halfRoad); ctx.stroke();
  // West Stop line
  ctx.beginPath(); ctx.moveTo(cx - halfRoad, cy - halfRoad); ctx.lineTo(cx - halfRoad, cy); ctx.stroke();

  // Zebra Crosswalks
  drawZebra(ctx, cx - halfRoad, cy - halfRoad - 14, roadW, 10, true);  // North
  drawZebra(ctx, cx - halfRoad, cy + halfRoad + 4, roadW, 10, true);   // South
  drawZebra(ctx, cx - halfRoad - 14, cy - halfRoad, 10, roadW, false); // West
  drawZebra(ctx, cx + halfRoad + 4, cy - halfRoad, 10, roadW, false);  // East

  // 4. Central Yellow-Box Gridlock Guard (hatched square)
  drawYellowBox(ctx, cx - halfRoad + 6, cy - halfRoad + 6, roadW - 12, roadW - 12);

  // 5. Traffic Signal Lights at 4 Approaches
  const activeDir = lastTrafficState ? lastTrafficState.active_direction : 'SOUTH';
  const curPhase  = lastTrafficState ? lastTrafficState.current_phase : 'GREEN';

  drawSignalLight(ctx, cx + halfRoad + 12, cy - halfRoad - 6, activeDir === 'NORTH' ? curPhase : 'RED');
  drawSignalLight(ctx, cx - halfRoad - 18, cy + halfRoad + 6, activeDir === 'SOUTH' ? curPhase : 'RED');
  drawSignalLight(ctx, cx + halfRoad + 6, cy + halfRoad + 12, activeDir === 'EAST' ? curPhase : 'RED');
  drawSignalLight(ctx, cx - halfRoad - 6, cy - halfRoad - 18, activeDir === 'WEST' ? curPhase : 'RED');

  // 6. Update & Draw Moving Vehicles
  updateAndDrawVehicles(ctx, cx, cy, halfRoad, activeDir, curPhase);

  // 7. Pedestrians crossing during Pedestrian Phase
  if (lastTrafficState && lastTrafficState.ped_phase_active) {
    drawAnimatedPedestrians(ctx, cx, cy, halfRoad);
  }
}

function drawZebra(ctx, x, y, w, h, horizontal) {
  ctx.fillStyle = 'rgba(255, 255, 255, 0.35)';
  const stripe = 5;
  const gap = 5;
  if (horizontal) {
    for (let sx = x + 2; sx < x + w - 2; sx += stripe + gap) {
      ctx.fillRect(sx, y, stripe, h);
    }
  } else {
    for (let sy = y + 2; sy < y + h - 2; sy += stripe + gap) {
      ctx.fillRect(x, sy, w, stripe);
    }
  }
}

function drawYellowBox(ctx, x, y, w, h) {
  const isAlert = lastTrafficState && (lastTrafficState.yellow_box_blocked || lastTrafficState.gridlock_alert);
  ctx.strokeStyle = isAlert ? 'rgba(255, 23, 68, 0.85)' : 'rgba(255, 234, 0, 0.7)';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(x, y, w, h);

  // Diagonal crosshatch lines
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  for (let offset = -w; offset < w * 2; offset += 16) {
    ctx.moveTo(x + offset, y);
    ctx.lineTo(x + offset + h, y + h);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  // If gridlock car is simulated, draw stationary car in center box
  if (isAlert) {
    ctx.fillStyle = '#ff1744';
    ctx.fillRect(x + w / 2 - 12, y + h / 2 - 6, 24, 12);
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    ctx.strokeRect(x + w / 2 - 12, y + h / 2 - 6, 24, 12);
    ctx.fillStyle = '#fff';
    ctx.font = '8px JetBrains Mono';
    ctx.fillText('STALL', x + w / 2 - 11, y + h / 2 + 3);
  }
}

function drawSignalLight(ctx, x, y, phase) {
  // Housing
  ctx.fillStyle = '#080b12';
  ctx.strokeStyle = '#1b2336';
  ctx.lineWidth = 1;
  ctx.fillRect(x - 4, y - 4, 16, 28);
  ctx.strokeRect(x - 4, y - 4, 16, 28);

  // Red
  ctx.fillStyle = phase === 'RED' ? '#ff1744' : '#33080e';
  ctx.beginPath(); ctx.arc(x + 4, y + 2, 3, 0, Math.PI * 2); ctx.fill();
  if (phase === 'RED') {
    ctx.shadowColor = '#ff1744'; ctx.shadowBlur = 6; ctx.fill(); ctx.shadowBlur = 0;
  }

  // Yellow
  ctx.fillStyle = phase === 'YELLOW' ? '#ffea00' : '#332f08';
  ctx.beginPath(); ctx.arc(x + 4, y + 10, 3, 0, Math.PI * 2); ctx.fill();
  if (phase === 'YELLOW') {
    ctx.shadowColor = '#ffea00'; ctx.shadowBlur = 6; ctx.fill(); ctx.shadowBlur = 0;
  }

  // Green
  ctx.fillStyle = phase === 'GREEN' ? '#00e676' : '#083318';
  ctx.beginPath(); ctx.arc(x + 4, y + 18, 3, 0, Math.PI * 2); ctx.fill();
  if (phase === 'GREEN') {
    ctx.shadowColor = '#00e676'; ctx.shadowBlur = 6; ctx.fill(); ctx.shadowBlur = 0;
  }
}

function updateAndDrawVehicles(ctx, cx, cy, halfRoad, activeDir, phase) {
  const laneCenterOffset = halfRoad / 2;

  junctionVehicles.forEach((v, idx) => {
    const isGreen = (v.dir === activeDir && phase === 'GREEN');
    const isAmbulance = (v.type === 'amb' || (lastTrafficState && lastTrafficState.ambulance_active && v.dir === lastTrafficState.ambulance_direction));

    // Vehicle movement physics
    if (isGreen || v.passedStopLine || isAmbulance) {
      v.progress += v.speed * (isAmbulance ? 2.5 : 1.5);
      if (v.progress > 140) v.passedStopLine = true;
    } else {
      // Move up to the stop line or vehicle in front
      const targetStop = 140 - v.queueIndex * (v.length + 8);
      if (v.progress < targetStop) {
        v.progress += v.speed * 0.8;
      }
    }

    // Wrap around once vehicles clear the junction
    if (v.progress > 380) {
      v.progress = 0;
      v.passedStopLine = false;
    }

    // Calculate (x, y, angle) based on direction
    let vx = 0, vy = 0, angle = 0;
    if (v.dir === 'NORTH') {
      // Approaches from top, driving south
      vx = cx + laneCenterOffset;
      vy = v.progress;
      angle = Math.PI / 2;
    } else if (v.dir === 'SOUTH') {
      // Approaches from bottom, driving north
      vx = cx - laneCenterOffset;
      vy = (cy * 2) - v.progress;
      angle = -Math.PI / 2;
    } else if (v.dir === 'EAST') {
      // Approaches from right, driving west
      vx = (cx * 2) - v.progress;
      vy = cy + laneCenterOffset;
      angle = Math.PI;
    } else if (v.dir === 'WEST') {
      // Approaches from left, driving east
      vx = v.progress;
      vy = cy - laneCenterOffset;
      angle = 0;
    }

    // Render vehicle body
    ctx.save();
    ctx.translate(vx, vy);
    ctx.rotate(angle);

    // Body
    ctx.fillStyle = v.color;
    ctx.fillRect(-v.length / 2, -v.width / 2, v.length, v.width);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1;
    ctx.strokeRect(-v.length / 2, -v.width / 2, v.length, v.width);

    // Windshield
    ctx.fillStyle = '#060a12';
    ctx.fillRect(v.length * 0.1, -v.width * 0.35, v.length * 0.25, v.width * 0.7);

    // Flashing emergency siren for ambulance
    if (v.type === 'amb' || isAmbulance) {
      ctx.fillStyle = (Date.now() % 400 < 200) ? '#ff1744' : '#00e5ff';
      ctx.beginPath();
      ctx.arc(0, 0, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.restore();
  });
}

function drawAnimatedPedestrians(ctx, cx, cy, halfRoad) {
  const t = (Date.now() / 200) % 20;
  ctx.fillStyle = '#00e5ff';

  // Pedestrians on zebra crossings
  for (let i = 0; i < 4; i++) {
    const px = cx - halfRoad + 15 + i * 20;
    const py = cy - halfRoad - 10 + Math.sin(t + i) * 2;
    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill();
  }
}

/* ─────────────────────── APPROACH CAMERA FEEDS CANVASES ─────────────────────── */
function initCameraFeeds() {
  DIRS.forEach(d => {
    const canvas = document.getElementById(`camCanvas-${d}`);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    function animateCam() {
      drawApproachPerspective(ctx, canvas.width, canvas.height, d);
      requestAnimationFrame(animateCam);
    }
    requestAnimationFrame(animateCam);
  });
}

function drawApproachPerspective(ctx, W, H, dir) {
  ctx.clearRect(0, 0, W, H);

  // Background style based on selected tab view
  if (visionTab === 'gray') {
    ctx.fillStyle = '#1c1c1c';
  } else if (visionTab === 'mog2') {
    ctx.fillStyle = '#040404';
  } else {
    ctx.fillStyle = '#090d16';
  }
  ctx.fillRect(0, 0, W, H);

  // Perspective trapezoid lane ROI
  const pTopLeft  = { x: W * 0.32, y: H * 0.15 };
  const pTopRight = { x: W * 0.68, y: H * 0.15 };
  const pBotRight = { x: W * 0.88, y: H * 0.95 };
  const pBotLeft  = { x: W * 0.12, y: H * 0.95 };

  // Road surface
  ctx.fillStyle = visionTab === 'mog2' ? '#000000' : '#121724';
  ctx.beginPath();
  ctx.moveTo(pTopLeft.x, pTopLeft.y);
  ctx.lineTo(pTopRight.x, pTopRight.y);
  ctx.lineTo(pBotRight.x, pBotRight.y);
  ctx.lineTo(pBotLeft.x, pBotLeft.y);
  ctx.closePath();
  ctx.fill();

  // ROI polygon boundary (Cyan perspective trapezoid)
  ctx.strokeStyle = visionTab === 'mog2' ? '#ffffff' : '#00e5ff';
  ctx.lineWidth = 1.2;
  ctx.stroke();

  // Dashed lane divider in perspective
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = 'rgba(0, 229, 255, 0.4)';
  ctx.beginPath();
  ctx.moveTo((pTopLeft.x + pTopRight.x) / 2, pTopLeft.y);
  ctx.lineTo((pBotLeft.x + pBotRight.x) / 2, pBotLeft.y);
  ctx.stroke();
  ctx.setLineDash([]);

  // Vehicle contours / bounding boxes inside approach lane
  const laneState = lastTrafficState ? lastTrafficState.lanes[dir] : null;
  const count = laneState ? laneState.vehicle_count : 4;

  const positions = [
    { yFrac: 0.78, lane: -1, type: 'bus',  label: 'BUS 3PCU',   w: 38, h: 22, color: '#ff7b00' },
    { yFrac: 0.62, lane:  1, type: 'car',  label: 'CAR 1PCU',   w: 28, h: 18, color: '#00e5ff' },
    { yFrac: 0.45, lane: -1, type: 'bike', label: 'BIKE 0.5PCU',w: 18, h: 12, color: '#00e676' },
    { yFrac: 0.30, lane:  1, type: 'car',  label: 'CAR 1PCU',   w: 22, h: 14, color: '#00e5ff' },
    { yFrac: 0.22, lane: -1, type: 'bike', label: 'BIKE 0.5PCU',w: 14, h: 10, color: '#00e676' },
  ];

  const toDraw = Math.min(count, positions.length);

  for (let i = 0; i < toDraw; i++) {
    const p = positions[i];
    const y = H * p.yFrac;
    const laneSpan = (pBotRight.x - pBotLeft.x) * (p.yFrac);
    const midX = W / 2 + (p.lane * laneSpan * 0.2);

    if (visionTab === 'mog2') {
      // White silhouette mask
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(midX - p.w / 2, y - p.h / 2, p.w, p.h);
    } else {
      // Bounding box with PCU Tag
      ctx.strokeStyle = p.color;
      ctx.lineWidth = 1.2;
      ctx.strokeRect(midX - p.w / 2, y - p.h / 2, p.w, p.h);

      // Corner markers
      drawCornerAccents(ctx, midX - p.w / 2, y - p.h / 2, p.w, p.h, p.color);

      // Label
      ctx.fillStyle = 'rgba(10, 14, 22, 0.85)';
      ctx.fillRect(midX - p.w / 2, y - p.h / 2 - 11, p.w + 6, 10);
      ctx.fillStyle = p.color;
      ctx.font = '7.5px JetBrains Mono';
      ctx.fillText(p.label, midX - p.w / 2 + 2, y - p.h / 2 - 3);
    }
  }

  // Camera identifier watermark
  ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
  ctx.font = '8px JetBrains Mono';
  ctx.fillText(`CAM_${dir}`, 6, 12);
}

function drawCornerAccents(ctx, x, y, w, h, color) {
  const cLen = 4;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  // Top-left
  ctx.beginPath(); ctx.moveTo(x, y + cLen); ctx.lineTo(x, y); ctx.lineTo(x + cLen, y); ctx.stroke();
  // Bottom-right
  ctx.beginPath(); ctx.moveTo(x + w, y + h - cLen); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - cLen, y + h); ctx.stroke();
}

/* ─────────────────────── PERFORMANCE EVALUATION CHART ─────────────────────── */
function initPerformanceChart() {
  const canvas = document.getElementById('perfChartCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  function renderChart() {
    drawPerfChart(ctx, canvas.width, canvas.height);
    requestAnimationFrame(renderChart);
  }
  requestAnimationFrame(renderChart);
}

function drawPerfChart(ctx, W, H) {
  ctx.clearRect(0, 0, W, H);
  const dataA = perfChartHistory.adaptive;
  const dataF = perfChartHistory.fixed;
  if (!dataA.length) return;

  const minVal = 170;
  const maxVal = 210;

  function toY(val) {
    return H - ((val - minVal) / (maxVal - minVal)) * (H - 14) - 6;
  }

  // Grid lines
  ctx.strokeStyle = '#141c2c';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2);
  ctx.stroke();

  // Draw Fixed curve (Red/Orange)
  ctx.strokeStyle = '#ff5252';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  dataF.forEach((val, idx) => {
    const x = (idx / (dataF.length - 1)) * W;
    const y = toY(val);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Draw Adaptive curve (SignalSync Green with glow)
  ctx.strokeStyle = '#00e676';
  ctx.lineWidth = 2;
  ctx.beginPath();
  dataA.forEach((val, idx) => {
    const x = (idx / (dataA.length - 1)) * W;
    const y = toY(val);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

/* ─────────────────────── CONTROL ACTIONS & REST APIS ─────────────────────── */

// 1. Vision Tab Switcher
function setVisionTab(tab) {
  visionTab = tab;
  document.querySelectorAll('#feedTabs .tab-btn').forEach(btn => btn.classList.remove('active'));
  const activeBtn = Array.from(document.querySelectorAll('#feedTabs .tab-btn')).find(b => {
    if (tab === 'rgb') return b.textContent.includes('RGB');
    if (tab === 'gray') return b.textContent.includes('Gray');
    if (tab === 'mog2') return b.textContent.includes('MOG2');
    if (tab === 'grid') return b.textContent.includes('Split');
    return false;
  });
  if (activeBtn) activeBtn.classList.add('active');
}

// 2. Emergency Preemption (Feature B)
async function triggerAmbulance(direction) {
  try {
    const res = await fetch(`${API_BASE}/traffic/ambulance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction })
    });
    // Add ambulance vehicle to the junction animation
    junctionVehicles.unshift(createVehicle(direction, 0, 'amb'));
  } catch (e) {
    console.error('Failed to trigger ambulance preemption:', e);
  }
}

// 3. Yellow-Box Gridlock Test
async function testYellowBoxGridlock() {
  try {
    const res = await fetch(`${API_BASE}/traffic/gridlock`, { method: 'POST' });
    const data = await res.json();
    const btn = el('btnTestGridlock');
    if (data.yellow_box_blocked) {
      btn.style.borderColor = 'var(--red)';
      btn.style.color = 'var(--red)';
      btn.textContent = '⚠️ Clear Gridlock';
    } else {
      btn.style.borderColor = 'var(--border-accent)';
      btn.style.color = 'var(--text-main)';
      btn.textContent = '⚠️ Test Yellow-Box Gridlock';
    }
  } catch (e) {
    console.error('Failed to toggle gridlock:', e);
  }
}

// 4. Pedestrian Demand & Walk Phase
async function addPedestrianDemand() {
  try {
    await fetch(`${API_BASE}/traffic/pedestrian/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: 4 })
    });
  } catch (e) {
    console.error('Failed adding ped demand:', e);
  }
}

async function triggerPedWalkPhase() {
  try {
    await fetch(`${API_BASE}/traffic/pedestrian/trigger`, { method: 'POST' });
  } catch (e) {
    console.error('Failed triggering ped phase:', e);
  }
}

// 5. Rush Presets
async function setPreset(name) {
  try {
    await fetch(`${API_BASE}/traffic/scenario`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
    if (name === 'Normal Flow') el('scenNormal')?.classList.add('active');
    else if (name === 'Peak Rush') el('scenPeak')?.classList.add('active');
    else if (name === 'Arterial Surge') el('scenSurge')?.classList.add('active');
    else if (name === 'Night Low-Flow') el('scenNight')?.classList.add('active');
  } catch (e) {
    console.error('Failed setting preset:', e);
  }
}

// 6. Next Phase & Reset
async function forceNextPhase() {
  try {
    await fetch(`${API_BASE}/traffic/next_phase`, { method: 'POST' });
  } catch (e) {
    console.error('Failed forcing next phase:', e);
  }
}

async function resetQueues() {
  try {
    await fetch(`${API_BASE}/traffic/reset`, { method: 'POST' });
    spawnInitialJunctionVehicles();
  } catch (e) {
    console.error('Failed resetting queues:', e);
  }
}

// 7. Sim Incidents (Feature G)
async function simRedRunner() {
  try {
    await fetch(`${API_BASE}/traffic/sim_incident`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'red_runner' })
    });
  } catch (e) {
    console.error('Failed simulating red runner:', e);
  }
}

async function simStalledVeh() {
  try {
    await fetch(`${API_BASE}/traffic/sim_incident`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'stalled' })
    });
  } catch (e) {
    console.error('Failed simulating stalled vehicle:', e);
  }
}

// 8. Sliders / Timing Thresholds
async function onThresholdChange() {
  const minG   = parseFloat(el('slMinGreen').value);
  const maxG   = parseFloat(el('slMaxGreen').value);
  const yel    = parseFloat(el('slYellow').value);
  const allRed = parseFloat(el('slAllRed').value);
  const starve = parseFloat(el('slMaxRedWait').value);
  const spawn  = parseFloat(el('slSpawnRate').value);

  el('valMinGreen').textContent  = `${minG}s`;
  el('valMaxGreen').textContent  = `${maxG}s`;
  el('valYellow').textContent    = `${yel}s`;
  el('valAllRed').textContent    = `${allRed}s`;
  el('valMaxRedWait').textContent= `${starve}s`;
  el('valSpawnRate').textContent = `${spawn.toFixed(1)}x`;

  try {
    await fetch(`${API_BASE}/traffic/thresholds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        t_min: minG,
        t_max: maxG,
        yellow_s: yel,
        all_red_s: allRed,
        max_red_wait: starve,
        spawn_rate: spawn
      })
    });
  } catch (e) {
    console.error('Failed updating thresholds:', e);
  }
}

// 9. Pause/Resume
function togglePause() {
  isPaused = !isPaused;
  const pBtn = el('btnPause');
  const pIco = el('pauseIcon');
  const pTxt = el('pauseText');
  if (isPaused) {
    pIco.textContent = '▶';
    pTxt.textContent = 'RESUME';
    pBtn.style.color = 'var(--green)';
    pBtn.style.borderColor = 'var(--green)';
  } else {
    pIco.textContent = '⏸';
    pTxt.textContent = 'PAUSE';
    pBtn.style.color = 'var(--yellow)';
    pBtn.style.borderColor = 'rgba(255,234,0,0.4)';
  }
}

// 10. Live Camera Initialization
async function initLiveCamera() {
  const dock = el('videoMonitorDock');
  dock.style.display = 'block';
  el('dockTitleText').textContent = 'LIVE WEBCAM STREAM & OPENCV TRACKING';

  // Request browser webcam
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    try {
      webcamStream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 360 }
      });
      const videoEl = el('webcamElement');
      videoEl.srcObject = webcamStream;
      videoEl.play();

      // Draw local webcam frames to the canvas with tracking simulation overlay
      const canvas = el('videoCanvas');
      const ctx = canvas.getContext('2d');
      function drawWebcam() {
        if (!webcamStream) return;
        ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
        
        // Draw overlay HUD chips
        ctx.fillStyle = 'rgba(12, 16, 24, 0.7)';
        ctx.fillRect(8, 8, 260, 24);
        ctx.strokeStyle = '#00e5ff';
        ctx.strokeRect(8, 8, 260, 24);
        ctx.fillStyle = '#00e5ff';
        ctx.font = '10px JetBrains Mono';
        ctx.fillText('LIVE WEBCAM OPENCV TRACKING ACTIVE', 14, 24);

        requestAnimationFrame(drawWebcam);
      }
      requestAnimationFrame(drawWebcam);
    } catch (err) {
      console.warn('Browser webcam not accessible, falling back to server camera API:', err);
    }
  }

  // Also notify server backend
  try {
    await fetch(`${API_BASE}/video/camera`, { method: 'POST' });
  } catch (e) {
    console.error('Server camera call failed:', e);
  }
}

// 11. Video File Upload
async function handleVideoUpload(input) {
  const file = input.files[0];
  if (!file) return;

  const dock = el('videoMonitorDock');
  dock.style.display = 'block';
  el('dockTitleText').textContent = `OPENCV STREAM: ${file.name}`;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`${API_BASE}/video/upload`, {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    console.log('Video uploaded successfully:', data);
  } catch (e) {
    console.error('Upload failed:', e);
  }
}

function closeVideoMonitor() {
  el('videoMonitorDock').style.display = 'none';
  if (webcamStream) {
    webcamStream.getTracks().forEach(t => t.stop());
    webcamStream = null;
  }
}

// 12. Download CSV Report
function downloadCSVReport() {
  window.location.href = `${API_BASE}/traffic/report_csv`;
}

// 13. Abstract & Python Code Modal
function openAbstractModal() {
  el('abstractModal').style.display = 'flex';
}

function closeAbstractModal() {
  el('abstractModal').style.display = 'none';
}

function handleModalBackdropClick(e) {
  if (e.target.id === 'abstractModal') {
    closeAbstractModal();
  }
}

function copyPythonCode() {
  const code = el('pythonSourceCode').innerText;
  navigator.clipboard.writeText(code).then(() => {
    alert('SignalSync Python CV source code copied to clipboard!');
  });
}

// Helper
function el(id) {
  return document.getElementById(id);
}

/* ─────────────────────── INITIALIZATION ─────────────────────── */
window.addEventListener('DOMContentLoaded', () => {
  connectTrafficWS();
  connectVideoWS();
  initJunctionSimulation();
  initCameraFeeds();
  initPerformanceChart();
});
