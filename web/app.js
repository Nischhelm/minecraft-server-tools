const DATA_URL = "data/perf.json";
const CHART_WIDTH = 600;
const CHART_HEIGHT = 130;
const PAD = { top: 8, right: 8, bottom: 18, left: 30 };

function tpsClass(tps) {
  if (tps == null) return "";
  if (tps >= 19) return "good";
  if (tps >= 15) return "warn";
  return "bad";
}

function fmt(value, digits = 1) {
  return value == null ? "–" : value.toFixed(digits);
}

function buildScales(points, field, fixedMax) {
  const values = points.map((p) => p[field]).filter((v) => v != null);
  const min = 0;
  const max = fixedMax ?? Math.max(1, ...values);
  const times = points.map((p) => p.t);
  return {
    min, max,
    tMin: Math.min(...times), tMax: Math.max(...times),
    x: (t) => PAD.left + ((t - Math.min(...times)) / Math.max(1, Math.max(...times) - Math.min(...times))) * (CHART_WIDTH - PAD.left - PAD.right),
    y: (v) => CHART_HEIGHT - PAD.bottom - ((v - min) / (max - min)) * (CHART_HEIGHT - PAD.top - PAD.bottom),
  };
}

function lineChart(points, field, color, fixedMax) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`);
  svg.setAttribute("preserveAspectRatio", "none");

  const usable = points.filter((p) => p[field] != null);
  if (usable.length < 2) {
    const text = document.createElementNS(svg.namespaceURI, "text");
    text.setAttribute("x", CHART_WIDTH / 2);
    text.setAttribute("y", CHART_HEIGHT / 2);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("fill", "var(--text-dim)");
    text.setAttribute("font-size", "12");
    text.textContent = "noch keine Daten";
    svg.appendChild(text);
    return svg;
  }

  const scale = buildScales(usable, field, fixedMax);

  // horizontal gridlines at 0/50/100%
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
    label.textContent = Math.round(scale.min + frac * (scale.max - scale.min));
    svg.appendChild(label);
  }

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
  body.className = "dim-body";

  if (hasMap) {
    const mapCol = document.createElement("div");
    mapCol.className = "map-col";
    const img = document.createElement("img");
    img.src = `data/maps/${label}.png`;
    img.alt = `Region-Karte: ${label}`;
    img.loading = "lazy";
    img.onerror = () => {
      mapCol.innerHTML = '<p class="no-map">Noch keine Karte generiert.</p>';
    };
    mapCol.appendChild(img);
    body.appendChild(mapCol);
  }

  const statsCol = document.createElement("div");
  statsCol.className = "stats-col";

  if (!points || points.length === 0) {
    statsCol.innerHTML = '<p class="empty">Noch keine Performance-Daten.</p>';
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

async function main() {
  const container = document.getElementById("dimensions");
  let data;
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    document.getElementById("generated-at").textContent = "Daten konnten nicht geladen werden.";
    return;
  }

  document.getElementById("generated-at").textContent = `Stand: ${new Date(data.generated_at).toLocaleString("de-DE")}`;

  renderDimension(container, "overall", data.perf.overall, false);
  for (const label of data.dimensions) {
    renderDimension(container, label, data.perf[label], true);
  }
}

main();
