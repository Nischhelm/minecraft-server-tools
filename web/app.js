const DATA_URL = "data/perf.json";
const LIVE_URL = "data/live.json";
const LIVE_POLL_MS = 15000;
const CHART_WIDTH = 600;
const CHART_HEIGHT = 140;
const PAD = { top: 8, right: 8, bottom: 26, left: 30 };

// label -> map-col element, filled in as dimension sections render, so
// fetchLive()'s periodic updates can place player dots without re-rendering
// the whole page.
const mapCols = {};

function tpsClass(tps) {
  if (tps == null) return "";
  if (tps >= 19) return "good";
  if (tps >= 15) return "warn";
  return "bad";
}

function fmt(value, digits = 1) {
  return value == null ? "–" : value.toFixed(digits);
}

// Relative to the chart's own oldest point (0 = start of the visible
// window), counting up left-to-right - matches how the line itself reads.
function formatElapsed(elapsedMs) {
  const totalMinutes = Math.round(elapsedMs / 60000);
  if (totalMinutes <= 0) return "0m";
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function buildScales(points, field, fixedMax) {
  const values = points.map((p) => p[field]).filter((v) => v != null);
  const min = 0;
  const max = fixedMax ?? Math.max(1, ...values);
  const times = points.map((p) => p.t);
  const tMin = Math.min(...times);
  const tMax = Math.max(...times);
  const tSpan = Math.max(1, tMax - tMin);
  return {
    min, max, tMin, tMax,
    x: (t) => PAD.left + ((t - tMin) / tSpan) * (CHART_WIDTH - PAD.left - PAD.right),
    y: (v) => CHART_HEIGHT - PAD.bottom - ((v - min) / (max - min)) * (CHART_HEIGHT - PAD.top - PAD.bottom),
  };
}

function lineChart(points, field, color, fixedMax) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`);

  const usable = points.filter((p) => p[field] != null);
  if (usable.length < 2) {
    const text = document.createElementNS(svg.namespaceURI, "text");
    text.setAttribute("x", CHART_WIDTH / 2);
    text.setAttribute("y", CHART_HEIGHT / 2);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("fill", "var(--text-dim)");
    text.setAttribute("font-size", "12");
    text.textContent = "no data yet";
    svg.appendChild(text);
    return svg;
  }

  const scale = buildScales(usable, field, fixedMax);

  // horizontal gridlines at 0/50/100%, with y-axis value labels
  for (const frac of [0, 0.5, 1]) {
    const y = scale.y(scale.min + frac * (scale.max - scale.min));
    const line = document.createElementNS(svg.namespaceURI, "line");
    line.setAttribute("x1", PAD.left);
    line.setAttribute("x2", CHART_WIDTH - PAD.right);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "var(--border)");
    line.setAttribute("stroke-width", "1");
    svg.appendChild(line);

    const label = document.createElementNS(svg.namespaceURI, "text");
    label.setAttribute("x", 2);
    label.setAttribute("y", y + 3);
    label.setAttribute("font-size", "9");
    label.setAttribute("fill", "var(--text-dim)");
    const axisValue = scale.min + frac * (scale.max - scale.min);
    // Below max=10, integer rounding collapses distinct ticks onto the same
    // label (e.g. a 0-1 mspt range rounding 0.5 and 1 both to "1") - show a
    // decimal in that range instead.
    label.textContent = scale.max < 10 ? axisValue.toFixed(1) : Math.round(axisValue);
    svg.appendChild(label);
  }

  // x-axis ticks: evenly spaced timestamps with a short vertical mark and label
  const tickFracs = [0, 1 / 3, 2 / 3, 1];
  const axisY = CHART_HEIGHT - PAD.bottom;
  tickFracs.forEach((frac, i) => {
    const t = scale.tMin + frac * (scale.tMax - scale.tMin);
    const x = scale.x(t);

    const tick = document.createElementNS(svg.namespaceURI, "line");
    tick.setAttribute("x1", x);
    tick.setAttribute("x2", x);
    tick.setAttribute("y1", axisY);
    tick.setAttribute("y2", axisY + 4);
    tick.setAttribute("stroke", "var(--text-dim)");
    tick.setAttribute("stroke-width", "1");
    svg.appendChild(tick);

    const label = document.createElementNS(svg.namespaceURI, "text");
    label.setAttribute("y", CHART_HEIGHT - 4);
    label.setAttribute("font-size", "9");
    label.setAttribute("fill", "var(--text-dim)");
    if (i === 0) {
      label.setAttribute("x", x);
      label.setAttribute("text-anchor", "start");
    } else if (i === tickFracs.length - 1) {
      label.setAttribute("x", x);
      label.setAttribute("text-anchor", "end");
    } else {
      label.setAttribute("x", x);
      label.setAttribute("text-anchor", "middle");
    }
    label.textContent = formatElapsed(t - scale.tMin);
    svg.appendChild(label);
  });

  const d = usable.map((p, i) => `${i === 0 ? "M" : "L"} ${scale.x(p.t).toFixed(1)} ${scale.y(p[field]).toFixed(1)}`).join(" ");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", color);
  path.setAttribute("stroke-width", "1.5");
  path.setAttribute("stroke-linejoin", "round");
  svg.appendChild(path);

  return svg;
}

function renderDimension(container, label, points, hasMap) {
  const section = document.createElement("section");
  section.className = "dim";

  const heading = document.createElement("h2");
  heading.textContent = label.replace(/_/g, " ");
  section.appendChild(heading);

  const body = document.createElement("div");
  body.className = hasMap ? "dim-body" : "dim-body no-map-layout";

  if (hasMap) {
    const mapCol = document.createElement("div");
    mapCol.className = "map-col";
    const img = document.createElement("img");
    img.src = `data/maps/${label}.png`;
    img.alt = `Region map: ${label}`;
    img.loading = "lazy";
    img.onerror = () => {
      mapCol.innerHTML = '<p class="no-map">No map generated yet.</p>';
    };
    mapCol.appendChild(img);
    body.appendChild(mapCol);
    mapCols[label] = mapCol;
  }

  const statsCol = document.createElement("div");
  statsCol.className = "stats-col";

  if (!points || points.length === 0) {
    statsCol.innerHTML = '<p class="empty">No performance data yet.</p>';
  } else {
    const latest = points[points.length - 1];
    const statsRow = document.createElement("div");
    statsRow.className = "stats-row";
    const stats = [
      ["TPS", fmt(latest.tps, 1), tpsClass(latest.tps)],
      ["MSPT", fmt(latest.mspt, 1), ""],
    ];
    if (latest.chunks != null) stats.push(["Chunks", fmt(latest.chunks, 0), ""]);
    if (latest.entities != null) stats.push(["Entities", fmt(latest.entities, 0), ""]);
    for (const [name, value, cls] of stats) {
      const stat = document.createElement("div");
      stat.className = "stat";
      stat.innerHTML = `<span class="label">${name}</span><span class="value ${cls}">${value}</span>`;
      statsRow.appendChild(stat);
    }
    statsCol.appendChild(statsRow);

    const tpsWrap = document.createElement("div");
    tpsWrap.className = "chart-wrap";
    tpsWrap.innerHTML = '<div class="chart-legend"><span class="tps">TPS (0-20)</span></div>';
    tpsWrap.appendChild(lineChart(points, "tps", "var(--accent-tps)", 20));
    statsCol.appendChild(tpsWrap);

    const msptWrap = document.createElement("div");
    msptWrap.className = "chart-wrap";
    msptWrap.innerHTML = '<div class="chart-legend"><span class="mspt">MSPT (ms)</span></div>';
    msptWrap.appendChild(lineChart(points, "mspt", "var(--accent-mspt)"));
    statsCol.appendChild(msptWrap);
  }

  body.appendChild(statsCol);
  section.appendChild(body);
  container.appendChild(section);
}

function renderLeaderboard(entries) {
  const el = document.getElementById("leaderboard");
  if (!entries || entries.length === 0) {
    el.innerHTML = "";
    return;
  }
  // Already sorted by last_login (most recent first) server-side.
  const rows = entries
    .map((e) => {
      const lastLogin = new Date(e.last_login).toLocaleString("en-US");
      return `<li><span class="player">${e.player}</span><span class="hours">${e.hours}h</span><span class="last-login">last seen ${lastLogin}</span></li>`;
    })
    .join("");
  el.innerHTML = `<h2>Playtime</h2><ol class="leaderboard-list">${rows}</ol>`;
}

// Distinct, stable-per-session colors - assigned by alphabetical rank among
// currently-online players, so the same player keeps the same color across
// every dimension's map and doesn't reshuffle as long as the online set
// doesn't change. Doubles as this palette's cap on guaranteed-unique colors;
// beyond that it repeats (cosmetic collision only).
const PLAYER_COLORS = ["#ff6b6b", "#4dabf7", "#69db7c", "#ffd43b", "#da77f2", "#ff922b", "#38d9a9", "#f783ac"];

function assignPlayerColors(players) {
  const sorted = [...players].map((p) => p.name).sort();
  const colors = new Map();
  sorted.forEach((name, i) => colors.set(name, PLAYER_COLORS[i % PLAYER_COLORS.length]));
  return colors;
}

function renderLiveStatus(live, colors) {
  const el = document.getElementById("live-status");
  if (!live) {
    el.textContent = "Live status unavailable.";
    el.className = "live-status unknown";
    return;
  }
  if (!live.awake) {
    // updated_at only moves while asleep when the awake state itself last
    // flipped (write_live_status() isn't called for player events while
    // there's nobody online to log in/out) - so it doubles as "asleep since".
    const asleepFor = live.updated_at ? formatElapsed(Math.max(0, Date.now() - new Date(live.updated_at).getTime())) : null;
    const suffix = asleepFor ? ` (${asleepFor})` : "";
    el.innerHTML = `<span class="dot asleep"></span> Server is asleep${suffix} - join to wake it up.`;
    el.className = "live-status asleep";
    return;
  }
  if (live.players.length === 0) {
    el.innerHTML = '<span class="dot awake"></span> Server is online - nobody online';
  } else {
    const chips = live.players
      .map((p) => `<span class="player-chip"><span class="swatch" style="background:${colors.get(p.name)}"></span>${p.name}</span>`)
      .join("");
    el.innerHTML = `<span class="dot awake"></span> Server is online - ${chips}`;
  }
  el.className = "live-status awake";
}

function updatePlayerDots(live, colors) {
  for (const mapCol of Object.values(mapCols)) {
    mapCol.querySelectorAll(".player-dot").forEach((el) => el.remove());
  }
  if (!live || !live.awake) return;

  for (const player of live.players) {
    if (player.dim == null || player.x_pct == null || player.y_pct == null) continue;
    const mapCol = mapCols[player.dim];
    if (!mapCol) continue;

    const dot = document.createElement("div");
    dot.className = "player-dot";
    dot.style.left = `${player.x_pct}%`;
    dot.style.top = `${player.y_pct}%`;
    dot.style.background = colors.get(player.name);
    dot.title = player.name;
    mapCol.appendChild(dot);
  }
}

async function fetchLive() {
  try {
    const res = await fetch(LIVE_URL, { cache: "no-store" });
    const live = await res.json();
    const colors = assignPlayerColors(live.players || []);
    renderLiveStatus(live, colors);
    updatePlayerDots(live, colors);
  } catch (err) {
    renderLiveStatus(null);
  }
}

async function main() {
  const container = document.getElementById("dimensions");
  let data;
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    document.getElementById("generated-at").textContent = "Could not load data.";
    return;
  }

  document.getElementById("generated-at").textContent = `Charts/maps last updated: ${new Date(data.generated_at).toLocaleString("en-US")}`;

  renderLeaderboard(data.leaderboard);

  renderDimension(container, "overall", data.perf.overall, false);
  for (const label of data.dimensions) {
    renderDimension(container, label, data.perf[label], true);
  }

  fetchLive();
  setInterval(fetchLive, LIVE_POLL_MS);
}

main();
