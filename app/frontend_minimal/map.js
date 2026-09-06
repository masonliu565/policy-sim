// Birds-eye continental USA with six clickable metro markers, and a camera
// that flies down into a local community view for the metro you pick.
//
// The country is drawn from the reference project's hand-drawn coastline: the
// 106 coastline points, Great Lakes, state grid and Alaska/Hawai'i insets were
// extracted from its source rather than retyped, and all six of our metros
// already existed in its coordinate space, so the pins sit where those cities
// actually are.
//
// The local view is a UNIT CHART, not a picture of a neighbourhood. One hundred
// house glyphs stand for one hundred children in that metro, split three ways
// by the metro's own child poverty rate: still in poverty after the policy,
// lifted out by it, and never in poverty. Every count comes from by_metro in
// the scenario file. Nothing here is invented, and the caption says so.

const PALETTE = {
  sky: "#a8cfc5", land: "#b6c994", coast: "#dbe2ba", lake: "#8dbfc0",
  panel: "#f9f9ef", ink: "#344c40", green: "#31553f", muted: "#7e8c73",
  clay: "#cf9b78", roof: "#8fae86", cream: "#e5dfc5",
};

// Reference-map coordinates. Not placed by eye.
const METRO_XY = {
  "San Francisco": [-975, 500], "Phoenix": [-620, 655], "Houston": [350, 710],
  "Atlanta": [640, 605], "Detroit": [530, 360], "New York": [915, 450],
};

const VIEW = { x0: -1165, x1: 1135, y0: 175, y1: 935 };
// The reference coastline is drawn about 3.5:1, far wider than the contiguous
// US actually is (~1.7:1); at full width it reads as a smear. Stretching y
// keeps every hand-drawn point where it sits relative to its neighbours.
const YS = 1.35;
const W = VIEW.x1 - VIEW.x0, H = (VIEW.y1 - VIEW.y0) * YS;
const CX = (VIEW.x0 + VIEW.x1) / 2, CY = (VIEW.y0 + VIEW.y1) / 2;
const LOCAL_ZOOM = 7.0;

let metros = [], selected = null, hover = null;
let dpr = 1, base = 1, cw = 0, ch = 0;
let cam = { x: CX, y: CY, z: 1 }, target = { x: CX, y: CY, z: 1 };
let raf = null;

const cv = document.getElementById("map");
const ctx = cv.getContext("2d");
const resetBtn = document.getElementById("reset");
const hint = document.getElementById("hint");

const lerp = (a, b, t) => a + (b - a) * t;
const seeded = s => { let t = s * 9301 + 49297; return () => (t = (t * 9301 + 49297) % 233280) / 233280; };
const tx = x => (x - cam.x) * base * cam.z + cw / 2;
const ty = y => (y - cam.y) * base * cam.z * YS + ch / 2;
const sc = () => base * cam.z;

function layout() {
  const wrap = document.getElementById("wrap");
  cw = wrap.clientWidth || 900;
  ch = Math.round(cw * H / W);
  dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(cw * dpr);
  cv.height = Math.round(ch * dpr);
  cv.style.height = ch + "px";
  base = cw / W;
}

function setTarget() {
  if (selected && METRO_XY[selected]) {
    const [mx, my] = METRO_XY[selected];
    target = { x: mx, y: my + 40, z: LOCAL_ZOOM };
  } else {
    target = { x: CX, y: CY, z: 1 };
  }
  if (!raf) raf = requestAnimationFrame(tick);
}

function tick() {
  const k = 0.16;
  cam.x = lerp(cam.x, target.x, k);
  cam.y = lerp(cam.y, target.y, k);
  cam.z = lerp(cam.z, target.z, k);
  const done = Math.abs(cam.z - target.z) < 0.004
    && Math.abs(cam.x - target.x) < 0.6 && Math.abs(cam.y - target.y) < 0.6;
  if (done) { cam = { ...target }; raf = null; } else { raf = requestAnimationFrame(tick); }
  draw();
}

function path(pts) {
  ctx.beginPath();
  pts.forEach(([x, y], i) => i ? ctx.lineTo(tx(x), ty(y)) : ctx.moveTo(tx(x), ty(y)));
  ctx.closePath();
}

// --------------------------------------------------------------------------
function draw() {
  layout();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = PALETTE.sky;
  ctx.fillRect(0, 0, cw, ch);

  // Cross-fade: the country hands over to the local view as the camera drops.
  const t = Math.max(0, Math.min(1, (cam.z - 2.2) / (LOCAL_ZOOM - 3.4)));
  const macro = 1 - t;

  if (macro > 0.01) {
    ctx.save();
    ctx.globalAlpha = macro;
    drawCountry();
    ctx.restore();
  }
  if (t > 0.01 && selected) {
    ctx.save();
    ctx.globalAlpha = t;
    drawLocal(metros.find(m => m.metro === selected));
    ctx.restore();
  }

  resetBtn.hidden = !selected;
  hint.textContent = selected
    ? "one house = one child in 100 · click Back to national to zoom out"
    : "click a metro to zoom into it";
  Streamlit.setFrameHeight(ch + 2);
}

function drawCountry() {
  const s = sc();
  path(GEO.coast);
  ctx.fillStyle = PALETTE.land; ctx.fill();
  ctx.strokeStyle = PALETTE.coast; ctx.lineWidth = Math.max(1.5, 7 * s); ctx.stroke();

  // The Great Lakes straddle the border: clipped to the land they vanish,
  // unclipped and unmoved they float clear of the coast.
  const LAKE_DY = 42;
  ctx.fillStyle = PALETTE.lake;
  for (const [x, y, rx, ry] of GEO.lakes) {
    ctx.beginPath();
    ctx.ellipse(tx(x), ty(y + LAKE_DY), rx * s, ry * s * YS, -0.18, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.save();
  path(GEO.coast); ctx.clip();
  ctx.strokeStyle = "rgba(83,121,82,.20)";
  ctx.lineWidth = Math.max(0.6, 2 * s);
  for (let x = -950; x < 980; x += 150) {
    ctx.beginPath(); ctx.moveTo(tx(x), ty(180)); ctx.lineTo(tx(x + 110), ty(800)); ctx.stroke();
  }
  for (let y = 290; y < 770; y += 105) {
    ctx.beginPath(); ctx.moveTo(tx(-1100), ty(y)); ctx.lineTo(tx(1050), ty(y - 16)); ctx.stroke();
  }
  ctx.restore();

  ctx.fillStyle = PALETTE.land; ctx.strokeStyle = PALETTE.coast;
  ctx.lineWidth = Math.max(1, 5 * s);
  path(GEO.alaska); ctx.fill(); ctx.stroke();
  for (const [x, y, r] of GEO.hawaii) {
    ctx.beginPath(); ctx.arc(tx(x), ty(y), r * s, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  ctx.font = `600 ${Math.max(6.5, 14 * s)}px "DM Sans","Segoe UI",sans-serif`;
  ctx.fillStyle = "#7d9781"; ctx.textAlign = "left";
  ctx.fillText("AK", tx(-1046), ty(905));
  ctx.fillText("HI", tx(-760), ty(905));

  for (const m of metros) drawMarker(m);
}

function drawMarker(m) {
  const xy = METRO_XY[m.metro]; if (!xy) return;
  const [mx, my] = xy, s = sc();
  const isSel = selected === m.metro, isHot = hover === m.metro;
  const rand = seeded(mx * 19 + my * 7);
  ctx.save();
  ctx.globalAlpha *= selected && !isSel ? 0.45 : 1;

  for (let i = 0; i < 8; i++) {
    const x = mx - 24 + (i % 4) * 12 + (rand() - 0.5) * 4;
    const y = my + 13 + Math.floor(i / 4) * 9;
    const w = 6 + rand() * 5, h = 15 + rand() * 32;
    ctx.fillStyle = i % 3 ? PALETTE.roof : PALETTE.cream;
    ctx.fillRect(tx(x), ty(y - h), w * s, h * s * YS);
  }

  const r = (isSel || isHot ? 7.5 : 5.5) * s;
  ctx.fillStyle = isSel ? PALETTE.green : "#3d6b50";
  ctx.beginPath(); ctx.arc(tx(mx), ty(my + 12), Math.max(3, r), 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f2f5e4";
  ctx.beginPath(); ctx.arc(tx(mx), ty(my + 12), Math.max(1.2, r * 0.4), 0, Math.PI * 2); ctx.fill();

  const fs = Math.max(8, 15 * s);
  ctx.font = `600 ${fs}px "DM Sans","Segoe UI",sans-serif`;
  ctx.textAlign = "center";
  const line = (isSel || isHot) && m.value ? `${m.metro}  ${m.value}` : m.metro;
  const wpx = ctx.measureText(line).width;
  const lx = tx(mx), ly = ty(my + 12) - Math.max(12, 20 * s);
  ctx.fillStyle = "rgba(249,249,239,.92)";
  ctx.beginPath(); ctx.roundRect(lx - wpx / 2 - 7, ly - fs, wpx + 14, fs + 9, 5); ctx.fill();
  ctx.fillStyle = isSel ? PALETTE.green : "#536c5a";
  ctx.fillText(line, lx, ly);
  ctx.restore();
}

// --------------------------------------------------------------------------
// Local view. A unit chart of 100 children, drawn as houses on a street grid.
//
// Laid out in SCREEN space, not world space. Drawn in world coordinates it
// scaled with the camera and a 10x10 grid overflowed the frame -- seven rows
// visible, title and legend off-screen. The camera still does the flying; the
// panel just fits whatever canvas it is given.
function drawLocal(m) {
  if (!m) return;
  const pad = Math.max(18, cw * 0.028);
  const base = Math.max(11, Math.min(cw, ch) * 0.024);   // type scale

  const titleY = pad + base * 1.5;
  const capY = ch - pad - base * 2.4;
  const legY = ch - pad - base * 0.5;
  const gridTop = titleY + base * 1.5;
  const gridBot = capY - base * 1.6;

  // 100 houses. Pick the row/column split that gives the biggest glyph in the
  // box we actually have: a 10x10 block leaves most of a wide canvas empty.
  let cols = 20, rows = 5, cell = 0;
  for (const c of [10, 20, 25]) {
    const r = 100 / c;
    const s2 = Math.min((cw - pad * 2) / c, (gridBot - gridTop) / r);
    if (s2 > cell) { cell = s2; cols = c; rows = r; }
  }
  const gw = cell * cols, gh = cell * rows;
  const gx = (cw - gw) / 2;
  const gy = gridTop + Math.max(0, (gridBot - gridTop - gh) / 2);

  // ground
  ctx.fillStyle = "#c8d6a6";
  ctx.beginPath();
  ctx.roundRect(gx - pad * 0.55, gy - pad * 0.4,
                gw + pad * 1.1, gh + pad * 0.8, 12);
  ctx.fill();

  const haveData = typeof m.base === "number" && typeof m.post === "number";
  const still = haveData ? Math.round(m.post * 100) : 0;
  const lifted = haveData ? Math.max(0, Math.round((m.base - m.post) * 100)) : 0;

  // streets between blocks
  ctx.strokeStyle = "rgba(120,145,110,.28)";
  ctx.lineWidth = Math.max(1, cell * 0.06);
  for (let r = 1; r < rows; r++) {
    const y = gy + r * cell;
    ctx.beginPath(); ctx.moveTo(gx, y); ctx.lineTo(gx + gw, y); ctx.stroke();
  }

  for (let i = 0; i < 100; i++) {
    const col = i % cols, row = Math.floor(i / cols);
    const cat = i < still ? "still" : (i < still + lifted ? "lifted" : "none");
    house(gx + col * cell, gy + row * cell, cell, cat);
  }

  const left = gx - pad * 0.55;
  ctx.textAlign = "left";
  ctx.font = `700 ${base * 1.7}px "DM Sans","Segoe UI",sans-serif`;
  ctx.fillStyle = PALETTE.green;
  ctx.fillText(m.metro, left, titleY);
  const titleW = ctx.measureText(m.metro).width;

  if (m.sample_n) {
    ctx.font = `500 ${base * 0.92}px "DM Sans","Segoe UI",sans-serif`;
    ctx.fillStyle = "#6b7d6a";
    ctx.fillText(`${m.sample_n.toLocaleString()} sampled households`,
                 left + titleW + base * 0.9, titleY);
  }

  ctx.font = `600 ${base}px "DM Sans","Segoe UI",sans-serif`;
  ctx.fillStyle = "#5d7060";
  ctx.fillText(haveData
      ? `100 children in this metro — ${still} still in poverty, ${lifted} lifted out by this policy`
      : "no metro child-poverty figure in this scenario", left, capY);

  const items = [["still", `still in poverty (${still})`],
                 ["lifted", `lifted out (${lifted})`],
                 ["none", `not in poverty (${100 - still - lifted})`]];
  ctx.font = `500 ${base * 0.9}px "DM Sans","Segoe UI",sans-serif`;
  let lx = left;
  for (const [cat, label] of items) {
    ctx.fillStyle = cat === "still" ? PALETTE.clay
      : cat === "lifted" ? PALETTE.green : PALETTE.cream;
    ctx.beginPath();
    ctx.roundRect(lx, legY - base * 0.72, base * 0.78, base * 0.78, 2);
    ctx.fill();
    ctx.fillStyle = "#5d7060";
    ctx.fillText(label, lx + base * 1.1, legY);
    lx += ctx.measureText(label).width + base * 2.4;
  }

  if (m.top && m.top.length && cw > 620) {
    ctx.textAlign = "right";
    ctx.font = `500 ${base * 0.9}px "DM Sans","Segoe UI",sans-serif`;
    ctx.fillStyle = "#6b7d6a";
    ctx.fillText("most affected: " + m.top
      .map(t => `${String(t.group).replace(/_/g, " ")} ${t.delta}`).join("   ·   "),
      cw - (gx - pad * 0.55), capY);
    ctx.textAlign = "left";
  }
}

function house(x, y, cell, cat) {
  const w = cell * 0.72, h = cell * 0.66;
  const px = x + (cell - w) / 2, py = y + (cell - h) / 2 + h * 0.18;
  ctx.fillStyle = cat === "still" ? PALETTE.clay
    : cat === "lifted" ? PALETTE.green : PALETTE.cream;
  ctx.beginPath();
  ctx.moveTo(px, py - h * 0.30);
  ctx.lineTo(px + w * 0.5, py - h * 0.72);
  ctx.lineTo(px + w, py - h * 0.30);
  ctx.lineTo(px + w, py + h * 0.28);
  ctx.lineTo(px, py + h * 0.28);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,.32)";
  ctx.fillRect(px + w * 0.38, py - h * 0.16, w * 0.24, h * 0.26);
}

// --------------------------------------------------------------------------
function hit(ev) {
  if (selected) return null;              // no re-picking while zoomed in
  const r = cv.getBoundingClientRect();
  const px = ev.clientX - r.left, py = ev.clientY - r.top;
  let best = null, bd = 1e9;
  for (const m of metros) {
    const xy = METRO_XY[m.metro]; if (!xy) continue;
    const d = Math.hypot(px - tx(xy[0]), py - ty(xy[1] + 12));
    if (d < bd) { bd = d; best = m.metro; }
  }
  return bd < Math.max(22, 34 * sc()) ? best : null;
}

cv.addEventListener("mousemove", e => {
  const h = hit(e);
  cv.classList.toggle("pick", !!h);
  if (h !== hover) { hover = h; if (!raf) draw(); }
});
cv.addEventListener("mouseleave", () => { if (hover) { hover = null; if (!raf) draw(); } });
cv.addEventListener("click", e => {
  const h = hit(e);
  if (!h) return;
  selected = h; hover = null;
  setTarget();
  Streamlit.setComponentValue(selected);
});
function goNational() {
  selected = null; hover = null; setTarget(); Streamlit.setComponentValue(null);
}
resetBtn.addEventListener("click", goNational);
window.addEventListener("keydown", e => { if (e.key === "Escape") goNational(); });
window.addEventListener("resize", () => draw());

const Streamlit = {
  setComponentValue(v) {
    window.parent.postMessage({ isStreamlitMessage: true,
      type: "streamlit:setComponentValue", value: v, dataType: "json" }, "*");
  },
  setFrameHeight(h) {
    window.parent.postMessage({ isStreamlitMessage: true,
      type: "streamlit:setFrameHeight", height: h }, "*");
  },
  ready() {
    window.parent.postMessage({ isStreamlitMessage: true,
      type: "streamlit:componentReady", apiVersion: 1 }, "*");
  },
};

window.addEventListener("message", ev => {
  if (!ev.data || ev.data.type !== "streamlit:render") return;
  const args = ev.data.args || {};
  metros = args.metros || [];
  if (args.selected !== undefined && args.selected !== selected) {
    selected = args.selected;
    cam = selected ? { ...cam } : cam;
    setTarget();
  }
  draw();
});

Streamlit.ready();
setTarget();
draw();
