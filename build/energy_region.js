/* HydroTwin Energy — region page renderer (merged twin).
   All numbers come from data.js (window.HTE_DATA, the energy project's own
   shipped snapshot) and energy_extra.js (window.HTE_EXTRA, walk-forward
   backtest + diagnostics computed by the energy pipeline's own code from its
   cached raw data). Nothing is hardcoded here. */
(function () {
  "use strict";
  const D = window.HTE_DATA, X = window.HTE_EXTRA;
  const rid = window.REGION_ID;
  const R = D.regions.find(r => r.id === rid);
  const E = X[rid];
  const $ = id => document.getElementById(id);
  const fmt = (n, d) => (n === null || n === undefined || isNaN(n)) ? "—" : Number(n).toFixed(d === undefined ? 2 : d);
  const money = n => "$" + fmt(n);
  const BLUE = "#8FC1F2", GREEN = "#43D17C", GOLD = "#FFD24D", RED = "#FF7A6B", GREY = "#9FB3C8";

  /* ---------- header + cards ---------- */
  document.title = R.name + " · HydroTwin Energy (merged twin)";
  $("rg-name").textContent = R.name;
  $("rg-hub").textContent = R.hub;
  $("rg-span").textContent = R.price_rows + " priced days · " + R.price_range[0] + " → " + R.price_range[1] +
    " · holdout from " + R.holdout_start + " · snapshot " + D.meta.snapshot_date;
  const S = E.stats, L = R.latest;
  const cards = [
    ["Latest price", money(L.last_price), L.last_price_date],
    ["Mean price", money(S.mean), S.first_date + " → " + S.last_date],
    ["Median price", money(S.median), S.n + " priced days"],
    ["Maximum price", money(S.max), "full record"],
    ["Minimum price", money(S.min), "full record"],
    ["River flow (region gauges)", fmt(L.flow_cfs, 0) + " cfs", "anomaly " + fmt(L.flow_anom_pct, 1) + "%"],
    ["Henry Hub gas", "$" + fmt(L.gas_usd, 3), "per MMBtu"],
    ["Soil moisture", fmt(L.soil_pct, 1) + "%", "SCAN, 8 in depth"],
  ];
  if (L.swe_in !== undefined) cards.push(["Snow water equivalent", fmt(L.swe_in, 2) + " in", "SNOTEL mean"]);
  cards.push(["7 day precip", fmt(L.precip7_mm, 1) + " mm", "Open-Meteo, 7d sum"]);
  cards.push(["7 day mean temp", fmt(L.temp7_c, 1) + " °C", "Open-Meteo"]);
  $("rg-cards").innerHTML = cards.map(c =>
    `<div class="hts-card"><div class="hts-cl">${c[0]}</div><div class="hts-cv">${c[1]}</div><div class="hts-cd">${c[2]}</div></div>`).join("");

  /* ---------- canvas helpers ---------- */
  function setup(cv, hCss) {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(300, r.width) * dpr; cv.height = hCss * dpr;
    cv.style.height = hCss + "px";
    const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    return [ctx, Math.max(300, r.width), hCss];
  }
  function axes(ctx, W, H, pad, ymin, ymax, ylab) {
    ctx.strokeStyle = "#22344C"; ctx.fillStyle = GREY; ctx.font = "11px Arial"; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (H - pad.t - pad.b) * i / 4, v = ymax - (ymax - ymin) * i / 4;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillText(fmt(v, Math.abs(ymax - ymin) < 3 ? 2 : 0), 4, y + 3);
    }
    if (ylab) { ctx.save(); ctx.rotate(-Math.PI / 2); ctx.fillText(ylab, -H / 2 - 20, 12); ctx.restore(); }
  }
  function line(ctx, pts, x0, x1, y0, y1, color, width, dash) {
    ctx.strokeStyle = color; ctx.lineWidth = width || 1.6; ctx.setLineDash(dash || []);
    ctx.beginPath();
    pts.forEach((p, i) => { const x = x0 + (x1 - x0) * p[0], y = y1 - (y1 - y0) * p[1]; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke(); ctx.setLineDash([]);
  }

  /* ---------- price history ---------- */
  let histDays = 0; // 0 = all
  function drawHistory() {
    const cv = $("cv-history"); const [ctx, W, H] = setup(cv, 250);
    const pad = { l: 52, r: 10, t: 10, b: 22 };
    let ser = E.series; if (histDays) ser = ser.slice(-histDays);
    const vs = ser.map(p => p.v), ymin = Math.min(...vs), ymax = Math.max(...vs);
    axes(ctx, W, H, pad, ymin, ymax, "$/MWh");
    const X0 = pad.l, X1 = W - pad.r, Y0 = pad.t, Y1 = H - pad.b;
    const pts = ser.map((p, i) => [i / (ser.length - 1), (p.v - ymin) / (ymax - ymin || 1)]);
    line(ctx, pts, X0, X1, Y0, Y1, BLUE, 1.3);
    ctx.fillStyle = GREY; ctx.font = "11px Arial";
    for (let i = 0; i < 4; i++) { const k = Math.floor((ser.length - 1) * i / 3); ctx.fillText(ser[k].d, X0 + (X1 - X0) * i / 3 - 26, H - 6); }
    // holdout shading
    const hi = ser.findIndex(p => p.d >= R.holdout_start);
    if (hi > 0) {
      const x = X0 + (X1 - X0) * hi / (ser.length - 1);
      ctx.fillStyle = "rgba(255,210,77,.07)"; ctx.fillRect(x, Y0, X1 - x, Y1 - Y0);
      ctx.fillStyle = GOLD; ctx.fillText("holdout →", x + 4, Y0 + 12);
    }
  }
  $("btn-hist-1y").onclick = () => { histDays = 365; drawHistory(); };
  $("btn-hist-all").onclick = () => { histDays = 0; drawHistory(); };

  /* ---------- moving forecast (walk-forward holdout) ---------- */
  const byOrigin = {};
  E.backtest.forEach(r => {
    if (!byOrigin[r.origin]) byOrigin[r.origin] = { origin: r.origin, p_last: r.p_last, preds: {}, sigma: {} };
    byOrigin[r.origin].preds[r.h] = r.pred; byOrigin[r.origin].sigma[r.h] = r.sigma;
  });
  const origins = Object.values(byOrigin).sort((a, b) => a.origin < b.origin ? -1 : 1);
  const priceOn = {}; E.series.forEach(p => priceOn[p.d] = p.v);
  const datePlus = (d, n) => { const t = new Date(d + "T00:00:00Z"); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
  let mi = 0, playing = false, timer = null;
  const selH = () => parseInt($("mv-h").value, 10);
  function drawMoving() {
    const cv = $("cv-moving"); const [ctx, W, H] = setup(cv, 300);
    const pad = { l: 52, r: 10, t: 12, b: 22 };
    const o = origins[mi], h = selH();
    // window: 45 days before origin .. origin+h
    const days = [];
    for (let k = -45; k <= h; k++) days.push(datePlus(o.origin, k));
    const vals = days.map(d => priceOn[d]);
    const fcDates = []; for (let k = 1; k <= h; k++) fcDates.push(datePlus(o.origin, k));
    const fcVals = fcDates.map((d, i) => o.preds[i + 1]);
    const all = vals.filter(v => v !== undefined).concat(fcVals, [o.p_last + 1.2816 * o.sigma[h], o.p_last - 1.2816 * o.sigma[h]]);
    let ymin = Math.min(...all), ymax = Math.max(...all); const mg = (ymax - ymin) * 0.08 || 1; ymin -= mg; ymax += mg;
    axes(ctx, W, H, pad, ymin, ymax, "$/MWh");
    const X0 = pad.l, X1 = W - pad.r, Y0 = pad.t, Y1 = H - pad.b;
    const fx = k => (k + 45) / (45 + h), fy = v => (v - ymin) / (ymax - ymin);
    // band for selected horizon (fan from origin using per-horizon sigma)
    ctx.fillStyle = "rgba(67,209,124,.13)"; ctx.beginPath();
    const top = [], bot = [];
    for (let k = 1; k <= h; k++) { top.push([fx(k), fy(o.preds[k] + 1.2816 * o.sigma[k])]); bot.push([fx(k), fy(o.preds[k] - 1.2816 * o.sigma[k])]); }
    top.forEach((p, i) => { const x = X0 + (X1 - X0) * p[0], y = Y1 - (Y1 - Y0) * p[1]; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    bot.reverse().forEach(p => { const x = X0 + (X1 - X0) * p[0], y = Y1 - (Y1 - Y0) * p[1]; ctx.lineTo(x, y); });
    ctx.closePath(); ctx.fill();
    // actuals up to origin
    const actPts = [], futPts = [];
    days.forEach((d, i) => { const k = i - 45; if (priceOn[d] === undefined) return; (k <= 0 ? actPts : futPts).push([fx(k), fy(priceOn[d])]); });
    if (actPts.length) line(ctx, actPts, X0, X1, Y0, Y1, BLUE, 1.6);
    if (futPts.length) { futPts.unshift([fx(0), fy(o.p_last)]); line(ctx, futPts, X0, X1, Y0, Y1, GOLD, 1.8); }
    // forecast line
    const fcPts = [[fx(0), fy(o.p_last)]].concat(fcVals.map((v, i) => [fx(i + 1), fy(v)]));
    line(ctx, fcPts, X0, X1, Y0, Y1, GREEN, 1.8);
    // persistence dashed
    line(ctx, [[fx(0), fy(o.p_last)], [fx(h), fy(o.p_last)]], X0, X1, Y0, Y1, RED, 1.2, [5, 4]);
    // origin marker
    ctx.fillStyle = "#F5F6F2"; ctx.beginPath(); ctx.arc(X0 + (X1 - X0) * fx(0), Y1 - (Y1 - Y0) * fy(o.p_last), 3.2, 0, 7); ctx.fill();
    ctx.fillStyle = GREY; ctx.fillText(o.origin, X0 + (X1 - X0) * fx(0) - 24, H - 6);
    ctx.fillText(datePlus(o.origin, h), X1 - 62, H - 6);
    // readout
    let se = 0, n = 0;
    for (let i = 0; i <= mi; i++) { const oo = origins[i], a = priceOn[datePlus(oo.origin, h)]; if (a !== undefined && oo.preds[h] !== undefined) { se += Math.abs(a - oo.preds[h]); n++; } }
    const actH = priceOn[datePlus(o.origin, h)];
    $("mv-read").innerHTML =
      `Forecast origin <b>${o.origin}</b> · last observed price <b>${money(o.p_last)}</b> · ` +
      `+${h}d forecast <b>${money(o.preds[h])}</b> · actual <b>${actH === undefined ? "—" : money(actH)}</b> · ` +
      `running holdout MAE (+${h}d, ${n} origins) <b>${n ? money(se / n) : "—"}</b> · ` +
      `origin ${mi + 1} of ${origins.length}`;
  }
  function step(d) { mi = Math.min(origins.length - 1, Math.max(0, mi + d)); drawMoving(); }
  $("mv-play").onclick = () => {
    playing = !playing; $("mv-play").textContent = playing ? "❚❚ Pause" : "▶ Play";
    if (playing) { timer = setInterval(() => { if (mi >= origins.length - 1) { $("mv-play").click(); return; } step(1); }, 350); }
    else clearInterval(timer);
  };
  $("mv-prev").onclick = () => step(-1);
  $("mv-next").onclick = () => step(1);
  $("mv-h").onchange = drawMoving;

  /* ---------- 7-day outlook ---------- */
  function drawOutlook() {
    const cv = $("cv-outlook"); const [ctx, W, H] = setup(cv, 240);
    const pad = { l: 52, r: 10, t: 10, b: 22 };
    const tail = E.series.slice(-30), fc = R.forecast;
    const all = tail.map(p => p.v).concat(fc.map(f => f.hi), fc.map(f => f.lo));
    let ymin = Math.min(...all), ymax = Math.max(...all); const mg = (ymax - ymin) * .08 || 1; ymin -= mg; ymax += mg;
    axes(ctx, W, H, pad, ymin, ymax, "$/MWh");
    const X0 = pad.l, X1 = W - pad.r, Y0 = pad.t, Y1 = H - pad.b, N = tail.length + fc.length;
    const fx = i => i / (N - 1), fy = v => (v - ymin) / (ymax - ymin);
    ctx.fillStyle = "rgba(67,209,124,.13)"; ctx.beginPath();
    fc.forEach((f, i) => { const x = X0 + (X1 - X0) * fx(tail.length - 1 + i + 1), y = Y1 - (Y1 - Y0) * fy(f.hi); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    [...fc].reverse().forEach((f, i) => { const x = X0 + (X1 - X0) * fx(tail.length - 1 + (fc.length - i)), y = Y1 - (Y1 - Y0) * fy(f.lo); ctx.lineTo(x, y); });
    ctx.closePath(); ctx.fill();
    line(ctx, tail.map((p, i) => [fx(i), fy(p.v)]), X0, X1, Y0, Y1, BLUE, 1.5);
    line(ctx, [[fx(tail.length - 1), fy(tail[tail.length - 1].v)]].concat(fc.map((f, i) => [fx(tail.length + i), fy(f.pred)])), X0, X1, Y0, Y1, GREEN, 1.8);
    ctx.fillStyle = GREY; ctx.fillText("issued " + R.forecast_origin, X0 + 2, H - 6); ctx.fillText(fc[fc.length - 1].date, X1 - 62, H - 6);
    $("fc-tbl").innerHTML = "<tr><th>Date</th><th>Forecast $/MWh</th><th>Band low</th><th>Band high</th></tr>" +
      fc.map(f => `<tr><td>${f.date}</td><td>${fmt(f.pred)}</td><td>${fmt(f.lo)}</td><td>${fmt(f.hi)}</td></tr>`).join("");
  }

  /* ---------- model statistics ---------- */
  const MH = [1, 2, 3, 4, 5, 6, 7];
  $("ms-tbl").innerHTML =
    "<tr><th>Horizon</th><th>Model</th><th>MAE $</th><th>RMSE $</th><th>R²</th><th>Persistence MAE $</th><th>Skill vs persistence</th><th>n</th></tr>" +
    MH.map(h => { const m = R.metrics[h]; return `<tr><td>+${h}d</td><td>${m.chosen}</td><td>${fmt(m.mae)}</td><td>${fmt(m.rmse)}</td><td>${fmt(m.r2, 3)}</td><td>${fmt(m.pers_mae)}</td><td>${fmt(m.skill_pct, 1)}%</td><td>${m.n}</td></tr>`; }).join("");
  $("cmp-tbl").innerHTML =
    "<tr><th>Horizon</th><th>Validation MAE ridge</th><th>Validation MAE MLP</th><th>Selected</th><th>Holdout MAE selected</th><th>Holdout MAE ridge</th><th>Holdout MAE persistence</th></tr>" +
    MH.map(h => { const m = R.metrics[h]; return `<tr><td>+${h}d</td><td>${fmt(m.val_mae.ridge)}</td><td>${fmt(m.val_mae.mlp)}</td><td>${m.chosen}</td><td>${fmt(m.mae)}</td><td>${fmt(m.ridge_mae)}</td><td>${fmt(m.pers_mae)}</td></tr>`; }).join("");
  $("pool-tbl").innerHTML =
    "<tr><th>Region</th><th>+1d MAE $</th><th>+1d skill</th><th>+7d MAE $</th><th>+7d skill</th><th>Mean skill +1..+7d</th></tr>" +
    D.regions.map(r2 => {
      const mean = MH.reduce((a, h) => a + r2.metrics[h].skill_pct, 0) / 7;
      return `<tr class="${r2.id === rid ? "hts-cur" : ""}"><td>${r2.name}</td><td>${fmt(r2.metrics[1].mae)}</td><td>${fmt(r2.metrics[1].skill_pct, 1)}%</td><td>${fmt(r2.metrics[7].mae)}</td><td>${fmt(r2.metrics[7].skill_pct, 1)}%</td><td>${fmt(mean, 1)}%</td></tr>`;
    }).join("");
  function drawLead() {
    const cv = $("cv-lead"); const [ctx, W, H] = setup(cv, 220);
    const pad = { l: 46, r: 10, t: 10, b: 24 };
    const ymax = Math.max(...MH.map(h => R.metrics[h].pers_mae)) * 1.15;
    axes(ctx, W, H, pad, 0, ymax, "MAE $");
    const bw = (W - pad.l - pad.r) / 7;
    MH.forEach((h, i) => {
      const x = pad.l + bw * i + bw * 0.18, w2 = bw * 0.28, Y1 = H - pad.b, Y0 = pad.t;
      ctx.fillStyle = GREEN; const v1 = R.metrics[h].mae; ctx.fillRect(x, Y1 - (Y1 - Y0) * v1 / ymax, w2, (Y1 - Y0) * v1 / ymax);
      ctx.fillStyle = RED; const v2 = R.metrics[h].pers_mae; ctx.fillRect(x + w2 + 3, Y1 - (Y1 - Y0) * v2 / ymax, w2, (Y1 - Y0) * v2 / ymax);
      ctx.fillStyle = GREY; ctx.fillText("+" + h + "d", x + 2, H - 8);
    });
    ctx.fillStyle = GREEN; ctx.fillText("■ model", pad.l + 4, pad.t + 4);
    ctx.fillStyle = RED; ctx.fillText("■ persistence", pad.l + 70, pad.t + 4);
  }

  /* ---------- feature importance ---------- */
  function drawFeatimp() {
    const h = $("fi-h").value, coefs = E.featimp_ridge[h], feats = E.features;
    const rows = feats.map((f, i) => [f, coefs[i]]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    const cv = $("cv-featimp"); const [ctx, W, H] = setup(cv, 300);
    const mx = Math.max(...rows.map(r => Math.abs(r[1]))) || 1, mid = W / 2;
    ctx.font = "11.5px Arial";
    rows.forEach((r, i) => {
      const y = 14 + i * 15, w = (W / 2 - 90) * Math.abs(r[1]) / mx;
      ctx.fillStyle = r[1] >= 0 ? GREEN : RED;
      if (r[1] >= 0) ctx.fillRect(mid, y - 8, w, 10); else ctx.fillRect(mid - w, y - 8, w, 10);
      ctx.fillStyle = "#E6EDF5"; ctx.textAlign = "right"; ctx.fillText(r[0], mid - (r[1] < 0 ? w + 6 : 6) * 1 - (r[1] < 0 ? 0 : 0), y);
      ctx.textAlign = "left";
      if (r[1] < 0) { ctx.fillStyle = "#E6EDF5"; ctx.fillText(r[0], mid + 6, y); }
      ctx.fillStyle = GREY; ctx.fillText(fmt(r[1], 3), r[1] >= 0 ? mid + w + 5 : mid - w - 34, y);
    });
    ctx.strokeStyle = "#3A4A5E"; ctx.beginPath(); ctx.moveTo(mid, 2); ctx.lineTo(mid, H - 2); ctx.stroke();
    ctx.textAlign = "left";
  }
  $("fi-h").onchange = drawFeatimp;

  /* ---------- ablation ---------- */
  $("ab-tbl").innerHTML =
    "<tr><th>Feature group dropped</th><th>+1d MAE $</th><th>Δ vs full</th><th>+7d MAE $</th><th>Δ vs full</th></tr>" +
    Object.keys(E.ablation).map(g => { const a = E.ablation[g]; return `<tr><td>${g}</td><td>${fmt(a["1"].mae)}</td><td>${fmt(a["1"].delta_mae)}</td><td>${fmt(a["7"].mae)}</td><td>${fmt(a["7"].delta_mae)}</td></tr>`; }).join("");

  /* ---------- correlation ---------- */
  function drawCorr() {
    const rows = Object.entries(E.corr_next_day).filter(r => r[1] !== null).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    const cv = $("cv-corr"); const [ctx, W, H] = setup(cv, 300);
    const mx = Math.max(...rows.map(r => Math.abs(r[1]))) || 1, mid = W / 2;
    ctx.font = "11.5px Arial";
    rows.forEach((r, i) => {
      const y = 14 + i * 15, w = (W / 2 - 90) * Math.abs(r[1]) / mx;
      ctx.fillStyle = r[1] >= 0 ? BLUE : GOLD;
      if (r[1] >= 0) ctx.fillRect(mid, y - 8, w, 10); else ctx.fillRect(mid - w, y - 8, w, 10);
      ctx.fillStyle = "#E6EDF5";
      if (r[1] >= 0) { ctx.textAlign = "right"; ctx.fillText(r[0], mid - 6, y); ctx.textAlign = "left"; }
      else ctx.fillText(r[0], mid + 6, y);
      ctx.fillStyle = GREY; ctx.fillText(fmt(r[1], 3), r[1] >= 0 ? mid + w + 5 : mid - w - 34, y);
    });
    ctx.strokeStyle = "#3A4A5E"; ctx.beginPath(); ctx.moveTo(mid, 2); ctx.lineTo(mid, H - 2); ctx.stroke();
  }

  /* ---------- drift ---------- */
  const drows = Object.entries(E.drift).sort((a, b) => Math.abs(b[1].shift_std) - Math.abs(a[1].shift_std));
  $("dr-tbl").innerHTML =
    "<tr><th>Feature</th><th>Train mean</th><th>Holdout mean</th><th>Shift (train std)</th></tr>" +
    drows.map(r => `<tr><td>${r[0]}</td><td>${fmt(r[1].train_mean, 3)}</td><td>${fmt(r[1].holdout_mean, 3)}</td><td>${fmt(r[1].shift_std, 2)}</td></tr>`).join("");

  /* ---------- confidence band table ---------- */
  $("band-tbl").innerHTML =
    "<tr><th>Horizon</th><th>Residual σ $</th><th>Band half width (±1.2816σ) $</th></tr>" +
    MH.map(h => { const m = R.metrics[h]; return `<tr><td>+${h}d</td><td>${fmt(m.resid_sigma)}</td><td>${fmt(1.2816 * m.resid_sigma)}</td></tr>`; }).join("");

  /* ---------- live conditions sparks ---------- */
  function spark(cvId, pts, color, unit) {
    const cv = $(cvId); if (!cv || !pts || !pts.length) return;
    const [ctx, W, H] = setup(cv, 64);
    const vs = pts.map(p => p.v), ymin = Math.min(...vs), ymax = Math.max(...vs);
    line(ctx, pts.map((p, i) => [i / (pts.length - 1), (p.v - ymin) / (ymax - ymin || 1)]), 2, W - 2, 4, H - 14, color, 1.3);
    ctx.fillStyle = GREY; ctx.font = "10.5px Arial";
    ctx.fillText(pts[0].d, 2, H - 2); ctx.fillText(pts[pts.length - 1].d, W - 62, H - 2);
    ctx.fillStyle = "#E6EDF5"; ctx.fillText(fmt(pts[pts.length - 1].v, 1) + " " + unit, 2, 10);
  }
  spark("sp-flow", R.sparks.flow, BLUE, "cfs");
  spark("sp-swe", R.sparks.swe, "#BFE3FF", "in");
  spark("sp-soil", R.sparks.soil, GOLD, "%");
  spark("sp-gas", R.sparks.gas, GREEN, "$");
  const FL = R.features_last;
  $("fl-tbl").innerHTML = "<tr><th>Feature</th><th>Value at forecast origin " + R.forecast_origin + "</th></tr>" +
    R.features.map(f => `<tr><td>${f}</td><td>${fmt(FL[f], 3)}</td></tr>`).join("");

  /* ---------- notebook ---------- */
  const ST = R.stations;
  $("nb-stations").innerHTML =
    "<li>USGS gauges: " + ST.usgs.join("; ") + "</li>" +
    "<li>SNOTEL (snow): " + (ST.snotel.join(", ") || "none") + "</li>" +
    "<li>SCAN (soil): " + ST.scan.join(", ") + "</li>" +
    "<li>Weather points (Open-Meteo): " + ST.weather.join(", ") + "</li>" +
    "<li>Gas: EIA Henry Hub daily spot (all regions)</li>";
  $("nb-limits").innerHTML = R.limitations.map(t => "<li>" + t + "</li>").join("");

  function drawAll() { drawHistory(); drawMoving(); drawOutlook(); drawLead(); drawFeatimp(); drawCorr(); }
  window.addEventListener("resize", drawAll);
  drawAll();
})();
