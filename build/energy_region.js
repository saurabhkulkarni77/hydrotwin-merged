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

  /* ---------- weekly / monthly outlooks ---------- */
  function outlookBlock(O, cvId, tblId, fcId, unit) {
    const cv = $(cvId); const [ctx, W, H] = setup(cv, 250);
    const pad = { l: 52, r: 10, t: 10, b: 22 };
    const ser = O.series, fc = O.forecast;
    const all = ser.map(p => p.v).concat(fc.map(f => f.hi), fc.map(f => f.lo));
    let ymin = Math.min(...all), ymax = Math.max(...all);
    const mg = (ymax - ymin) * 0.08 || 1; ymin -= mg; ymax += mg;
    axes(ctx, W, H, pad, ymin, ymax, "$/MWh");
    const X0 = pad.l, X1 = W - pad.r, Y0 = pad.t, Y1 = H - pad.b, N = ser.length + fc.length;
    const fx = i => i / (N - 1), fy = v => (v - ymin) / (ymax - ymin);
    const hi = ser.findIndex(p => p.d >= O.holdout_start);
    if (hi > 0) {
      const x = X0 + (X1 - X0) * fx(hi);
      ctx.fillStyle = "rgba(255,210,77,.07)"; ctx.fillRect(x, Y0, X1 - x, Y1 - Y0);
      ctx.fillStyle = GOLD; ctx.font = "11px Arial"; ctx.fillText("holdout →", x + 4, Y0 + 12);
    }
    ctx.fillStyle = "rgba(67,209,124,.13)"; ctx.beginPath();
    fc.forEach((f, i) => { const x = X0 + (X1 - X0) * fx(ser.length + i), y = Y1 - (Y1 - Y0) * fy(f.hi); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    [...fc].reverse().forEach((f, i) => { const x = X0 + (X1 - X0) * fx(ser.length + fc.length - 1 - i), y = Y1 - (Y1 - Y0) * fy(f.lo); ctx.lineTo(x, y); });
    ctx.closePath(); ctx.fill();
    line(ctx, ser.map((p, i) => [fx(i), fy(p.v)]), X0, X1, Y0, Y1, BLUE, 1.4);
    line(ctx, [[fx(ser.length - 1), fy(ser[ser.length - 1].v)]].concat(fc.map((f, i) => [fx(ser.length + i), fy(f.pred)])), X0, X1, Y0, Y1, GREEN, 1.8);
    ctx.fillStyle = GREY; ctx.font = "11px Arial";
    ctx.fillText(ser[0].d, X0, H - 6); ctx.fillText(fc[fc.length - 1].date, X1 - 62, H - 6);
    $(tblId).innerHTML =
      "<tr><th>Horizon</th><th>Model</th><th>MAE $</th><th>RMSE $</th><th>R²</th><th>Persistence MAE $</th><th>Skill vs persistence</th><th>n</th></tr>" +
      Object.keys(O.metrics).sort((a, b) => a - b).map(h => {
        const m = O.metrics[h];
        return m ? `<tr><td>+${h}${unit}</td><td>${m.chosen}</td><td>${fmt(m.mae)}</td><td>${fmt(m.rmse)}</td><td>${fmt(m.r2, 3)}</td><td>${fmt(m.pers_mae)}</td><td>${fmt(m.skill_pct, 1)}%</td><td>${m.n}</td></tr>`
                 : `<tr><td>+${h}${unit}</td><td colspan="7">not enough origins to score honestly</td></tr>`;
      }).join("");
    $(fcId).innerHTML = "<tr><th>Period starting</th><th>Forecast $/MWh</th><th>Band low</th><th>Band high</th></tr>" +
      fc.map(f => `<tr><td>${f.date}</td><td>${fmt(f.pred)}</td><td>${fmt(f.lo)}</td><td>${fmt(f.hi)}</td></tr>`).join("");
  }
  function drawOutlooks() {
    outlookBlock(E.outlooks.weekly, "cv-weekly", "wk-tbl", "wk-fc", " wk");
    outlookBlock(E.outlooks.monthly, "cv-monthly", "mo-tbl", "mo-fc", " mo");
    $("wk-ho").textContent = "holdout from " + E.outlooks.weekly.holdout_start +
      ", " + E.outlooks.weekly.n_periods + " weeks in the record";
    $("mo-ho").textContent = "holdout from " + E.outlooks.monthly.holdout_start +
      ", " + E.outlooks.monthly.n_periods + " months in the record";
  }

  /* ---------- yearly climatology outlook ---------- */
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function drawYearly() {
    const Y = E.outlooks.yearly, O = Y.outlook;
    const cv = $("cv-yearly"); const [ctx, W, H] = setup(cv, 250);
    const pad = { l: 52, r: 10, t: 10, b: 22 };
    const all = O.flatMap(o => [o.p75, o.p25]).filter(v => v !== null);
    let ymin = Math.min(...all), ymax = Math.max(...all);
    const mg = (ymax - ymin) * 0.1 || 1; ymin -= mg; ymax += mg;
    axes(ctx, W, H, pad, ymin, ymax, "$/MWh");
    const X0 = pad.l, X1 = W - pad.r, Y0 = pad.t, Y1 = H - pad.b;
    const fx = i => i / (O.length - 1), fy = v => (v - ymin) / (ymax - ymin);
    ctx.fillStyle = "rgba(255,210,77,.15)"; ctx.beginPath();
    O.forEach((o, i) => { const x = X0 + (X1 - X0) * fx(i), y = Y1 - (Y1 - Y0) * fy(o.p75); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    [...O].reverse().forEach((o, i) => { const x = X0 + (X1 - X0) * fx(O.length - 1 - i), y = Y1 - (Y1 - Y0) * fy(o.p25); ctx.lineTo(x, y); });
    ctx.closePath(); ctx.fill();
    line(ctx, O.map((o, i) => [fx(i), fy(o.mean)]), X0, X1, Y0, Y1, GOLD, 1.9);
    ctx.fillStyle = GREY; ctx.font = "11px Arial";
    O.forEach((o, i) => { if (i % 2 === 0) ctx.fillText(MON[o.month - 1] + " " + o.date.slice(0, 4), X0 + (X1 - X0) * fx(i) - 18, H - 6); });
    $("yr-tbl").innerHTML =
      "<tr><th>Calendar month</th><th>Mean $/MWh</th><th>Median</th><th>P25</th><th>P75</th><th>Year to year std</th><th>Years of data</th></tr>" +
      MON.map((mn, i) => { const c = Y.clim[String(i + 1)];
        return c ? `<tr><td>${mn}</td><td>${fmt(c.mean)}</td><td>${fmt(c.median)}</td><td>${fmt(c.p25)}</td><td>${fmt(c.p75)}</td><td>${fmt(c.std)}</td><td>${c.n_years} (${c.years.join(", ")})</td></tr>`
                 : `<tr><td>${mn}</td><td colspan="6">no complete month in the record</td></tr>`; }).join("");
    $("yr-note").textContent =
      "This yearly outlook is a climatology, not a fitted model. Each future month is priced at the average of that " +
      "calendar month across the " + Y.years.length + " calendar years in this record (" + Y.first + " to " + Y.last + " — a span of about " +
      (Math.round((new Date(Y.last) - new Date(Y.first)) / 864e5 / 365.25 * 10) / 10) + " years, against the river twin's 25). " +
      "The band is the middle half of those years (25th to 75th percentile): with so few years it is the spread of what " +
      "actually happened, not a confidence interval. Leave-one-year-out check of this climatology — predicting each year " +
      "from only the years before it: " + Y.folds.map(f => f.year + " MAE $" + fmt(f.mae) + " over " + f.n_months + " months").join("; ") +
      "; overall MAE $" + fmt(Y.loyo_mae) + "/MWh. Treat the outlook as a seasonal guide, not a forecast with daily model accuracy.";
  }

  /* ---------- tiny orbit 3D engine (canvas 2D, painter's algorithm) ------ */
  function orbit3d(cv, tip, draw, opts) {
    const st = Object.assign({ az: -0.65, el: 0.55, zoom: 1.0 }, opts || {});
    let hover = [];
    function render() {
      const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      const W = Math.max(300, r.width), H = 440;
      cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + "px";
      const ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, W, H);
      const caz = Math.cos(st.az), saz = Math.sin(st.az), cel = Math.cos(st.el), sel = Math.sin(st.el);
      const S = Math.min(W, H) * 1.04 * st.zoom;
      function proj(p) {
        const x = p[0] - 0.5, y = p[1], z = p[2] - 0.5;
        const x1 = x * caz - z * saz, z1 = x * saz + z * caz;
        const y1 = y * cel - z1 * sel, z2 = y * sel + z1 * cel;
        const persp = 1 / (1 + z2 * 0.3);
        return [W / 2 + x1 * S * persp, H * 0.86 - y1 * S * persp, z2];
      }
      hover = [];
      draw(ctx, proj, W, H, (sx, sy, text) => hover.push([sx, sy, text]));
    }
    let drag = null;
    cv.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY]; cv.setPointerCapture(e.pointerId); });
    cv.addEventListener("pointermove", e => {
      if (drag) {
        st.az += (e.clientX - drag[0]) * 0.008;
        st.el = Math.min(1.35, Math.max(0.15, st.el + (e.clientY - drag[1]) * 0.006));
        drag = [e.clientX, e.clientY]; render(); return;
      }
      const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      let best = null, bd = 20;
      hover.forEach(h => { const d = Math.hypot(h[0] - mx, h[1] - my); if (d < bd) { bd = d; best = h; } });
      if (best) {
        tip.style.display = "block"; tip.textContent = best[2];
        tip.style.left = Math.min(r.width - 190, best[0] + 12) + "px";
        tip.style.top = Math.max(2, best[1] - 32) + "px";
      } else tip.style.display = "none";
    });
    cv.addEventListener("pointerup", () => { drag = null; });
    cv.addEventListener("pointerleave", () => { tip.style.display = "none"; });
    cv.addEventListener("wheel", e => {
      e.preventDefault();
      st.zoom = Math.min(2.6, Math.max(0.5, st.zoom * (e.deltaY > 0 ? 0.9 : 1.11)));
      render();
    }, { passive: false });
    return { render };
  }
  function grid3(ctx, proj) {
    ctx.strokeStyle = "rgba(70,95,125,.5)"; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const t = i / 4;
      let a = proj([t, 0, 0]), b = proj([t, 0, 1]);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      a = proj([0, 0, t]); b = proj([1, 0, t]);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }
  }
  function quad(ctx, pts, fill, stroke) {
    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.6; ctx.stroke(); }
  }
  const ramp = t => { // blue -> teal -> gold -> red
    const stops = [[68, 116, 196], [46, 155, 87], [255, 210, 77], [255, 90, 70]];
    const x = Math.min(0.999, Math.max(0, t)) * 3, i = Math.floor(x), f = x - i;
    const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  };

  /* ---------- forecast surface 3D ---------- */
  const surfCtl = orbit3d($("cv-surface"), $("tip-surface"), (ctx, proj, W, H, addHover) => {
    const Sf = E.surface, vals = [];
    Sf.pred.forEach(row => row.forEach(v => { if (v !== null) vals.push(v); }));
    const vmin = Math.min(...vals), vmax = Math.max(...vals);
    const ny = v => (v - vmin) / ((vmax - vmin) || 1) * 0.58;
    grid3(ctx, proj);
    const node = (i, j) => Sf.pred[i][j] === null ? null : proj([i / 11, ny(Sf.pred[i][j]), j / 6]);
    const quads = [];
    for (let i = 0; i < 11; i++) for (let j = 0; j < 6; j++) {
      const c = [node(i, j), node(i + 1, j), node(i + 1, j + 1), node(i, j + 1)];
      if (c.some(p => p === null)) continue;
      const avg = (Sf.pred[i][j] + Sf.pred[i + 1][j] + Sf.pred[i + 1][j + 1] + Sf.pred[i][j + 1]) / 4;
      quads.push({ c, d: c.reduce((a, p) => a + p[2], 0) / 4, v: avg, i, j });
    }
    quads.sort((a, b) => b.d - a.d);
    quads.forEach(q => {
      quad(ctx, q.c, ramp((q.v - vmin) / ((vmax - vmin) || 1)), "rgba(10,18,28,.35)");
      const cx = q.c.reduce((a, p) => a + p[0], 0) / 4, cy = q.c.reduce((a, p) => a + p[1], 0) / 4;
      addHover(cx, cy, MON[q.i] + " origins · +" + (q.j + 1) + "d · mean forecast " + money(Sf.pred[q.i][q.j]) +
        " · mean actual " + money(Sf.actual[q.i][q.j]) + " · MAE " + money(Sf.mae[q.i][q.j]) +
        " · " + Sf.n[q.i][q.j] + " origins");
    });
    ctx.fillStyle = GREY; ctx.font = "11px Arial";
    for (let i = 0; i < 12; i += 2) { const p = proj([i / 11, 0, 1.06]); ctx.fillText(MON[i], p[0] - 8, p[1]); }
    for (let j = 0; j < 7; j++) { const p = proj([-0.05, 0, j / 6]); ctx.fillText("+" + (j + 1) + "d", p[0] - 22, p[1] + 3); }
    ctx.fillText("mean forecast " + money(vmin) + " → " + money(vmax), 10, 16);
  }, { az: -0.95, el: 0.72, zoom: 0.95 });

  /* ---------- data charts in 3D ---------- */
  let mode3d = "price";
  const c3 = orbit3d($("cv-3d"), $("tip-3d"), (ctx, proj, W, H, addHover) => {
    grid3(ctx, proj);
    if (mode3d === "price") {
      const ser = E.series.slice(-365);
      const vs = ser.map(p => p.v), vmin = Math.min(...vs), vmax = Math.max(...vs);
      const ny = v => (v - vmin) / ((vmax - vmin) || 1) * 0.8;
      const P3 = ser.map((p, i) => proj([i / (ser.length - 1), ny(p.v), 0.5]));
      for (let i = 0; i < ser.length - 1; i++) {
        const a = P3[i], b = P3[i + 1], a0 = proj([i / (ser.length - 1), 0, 0.5]), b0 = proj([(i + 1) / (ser.length - 1), 0, 0.5]);
        quad(ctx, [a, b, b0, a0], "rgba(88,150,230,.16)", null);
      }
      ctx.strokeStyle = BLUE; ctx.lineWidth = 1.8; ctx.beginPath();
      P3.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
      for (let i = 0; i < ser.length; i += 7)
        addHover(P3[i][0], P3[i][1], ser[i].d + " · " + money(ser[i].v) + "/MWh");
      addHover(P3[P3.length - 1][0], P3[P3.length - 1][1], ser[ser.length - 1].d + " · " + money(ser[ser.length - 1].v) + "/MWh");
      ctx.fillStyle = GREY; ctx.font = "11px Arial";
      const a = proj([0, 0.84, 0.5]); ctx.fillText(money(vmax), a[0] - 6, a[1]);
      const b = proj([0, 0, 0.5]); ctx.fillText(money(vmin), b[0] - 6, b[1] + 10);
      const c = proj([0, -0.06, 0.5]); ctx.fillText(ser[0].d, c[0] - 24, c[1] + 8);
      const d = proj([1, -0.06, 0.5]); ctx.fillText(ser[ser.length - 1].d, d[0] - 62, d[1] + 8);
    } else {
      const h = $("fi-h").value, coefs = E.featimp_ridge[h], feats = E.features;
      const rows = feats.map((f, i) => [f, coefs[i]]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
      const mx = Math.max(...rows.map(r => Math.abs(r[1]))) || 1, n = rows.length;
      const bars = rows.map((r, i) => {
        const xc = (i + 0.5) / n, hw = 0.42 / n, y = Math.abs(r[1]) / mx * 0.8;
        const f1 = proj([xc - hw, y, 0.60]), f2 = proj([xc + hw, y, 0.60]),
          f3 = proj([xc + hw, 0, 0.60]), f4 = proj([xc - hw, 0, 0.60]);
        const t1 = proj([xc - hw, y, 0.40]), t2 = proj([xc + hw, y, 0.40]);
        const s3 = proj([xc + hw, 0, 0.40]);
        return { r, faces: [[f1, f2, f3, f4], [t1, t2, f2, f1], [f2, t2, s3, f3]],
                 d: (f1[2] + f3[2]) / 2, cx: (f1[0] + f3[0]) / 2, cy: (f1[1] + f3[1]) / 2 };
      });
      bars.sort((a, b) => b.d - a.d);
      bars.forEach(b => {
        const base = b.r[1] >= 0 ? "67,209,124" : "255,122,107";
        quad(ctx, b.faces[2], `rgba(${base},.55)`, null);
        quad(ctx, b.faces[0], `rgba(${base},.96)`, "rgba(10,18,28,.4)");
        quad(ctx, b.faces[1], `rgba(${base},1)`, "rgba(10,18,28,.4)");
        addHover(b.cx, b.cy, b.r[0] + " · coef " + fmt(b.r[1], 3) + " (+" + h + "d ridge)");
      });
      ctx.fillStyle = GREY; ctx.font = "11px Arial";
      const a = proj([0, 0.86, 0.5]);
      ctx.fillText("standardized ridge coefficients, +" + h + "d · tallest |coef| " + fmt(mx, 3), a[0] - 6, a[1]);
    }
  });
  document.querySelectorAll(".hte3-mode").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".hte3-mode").forEach(x => x.classList.toggle("hte3-on", x === b));
    mode3d = b.dataset.mode; c3.render();
  }));

  /* ---------- market regimes (day clusters) ---------- */
  const PALETTE = ["#8FC1F2", "#43D17C", "#FFD24D", "#FF7A6B", "#C792EA", "#4DD0E1"];
  function drawRegimes() {
    const RG = E.regimes;
    const sorted = [...RG.regimes].sort((a, b) => a.mean_price - b.mean_price);
    const colorOf = {}; sorted.forEach((g, i) => colorOf[g.id] = PALETTE[i % PALETTE.length]);
    $("rgm-tbl").innerHTML =
      "<tr><th>Regime</th><th>Days</th><th>Share</th><th>Mean price $</th><th>Median $</th><th>Std $</th><th>Mean gas $</th><th>Mean 7d temp °C</th><th>Mean flow anomaly</th></tr>" +
      sorted.map(g => `<tr><td><span style="color:${colorOf[g.id]}">■</span> ${g.label}</td><td>${g.n}</td><td>${fmt(g.share_pct, 1)}%</td><td>${fmt(g.mean_price)}</td><td>${fmt(g.median_price)}</td><td>${fmt(g.std_price)}</td><td>${fmt(g.mean_gas)}</td><td>${fmt(g.mean_temp7, 1)}</td><td>${fmt(g.mean_flow_anom, 3)}</td></tr>`).join("");
    $("rgm-legend").innerHTML = sorted.map(g =>
      `<span><span class="sw" style="background:${colorOf[g.id]}"></span>${g.label} (${g.n} days)</span>`).join("") +
      `<span>PC1 ${fmt(RG.pca.explained_pct[0], 1)}% · PC2 ${fmt(RG.pca.explained_pct[1], 1)}% of variance</span>`;
    // PCA scatter
    {
      const cv = $("cv-rgm-pca"); const [ctx, W, H] = setup(cv, 300);
      const pad = { l: 46, r: 10, t: 10, b: 24 };
      const xs = RG.pca.points.map(p => p.x), ys = RG.pca.points.map(p => p.y);
      const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
      axes(ctx, W, H, pad, ymin, ymax, "");
      RG.pca.points.forEach(p => {
        const x = pad.l + (W - pad.l - pad.r) * (p.x - xmin) / ((xmax - xmin) || 1);
        const y = (H - pad.b) - (H - pad.t - pad.b) * (p.y - ymin) / ((ymax - ymin) || 1);
        ctx.fillStyle = colorOf[p.c]; ctx.fillRect(x - 1.6, y - 1.6, 3.2, 3.2);
      });
      ctx.fillStyle = GREY; ctx.font = "11px Arial";
      ctx.fillText("PC1 (" + fmt(RG.pca.explained_pct[0], 1) + "%)", W - pad.r - 70, H - 8);
      ctx.fillText("PC2 (" + fmt(RG.pca.explained_pct[1], 1) + "%)", pad.l + 2, pad.t + 4);
    }
    // timeline
    {
      const cv = $("cv-rgm-tl"); const [ctx, W, H] = setup(cv, 170);
      const pad = { l: 118, r: 10, t: 8, b: 20 };
      const T = RG.timeline, n = T.dates.length;
      const rowOf = {}; sorted.forEach((g, i) => rowOf[g.id] = i);
      const rh = (H - pad.t - pad.b) / sorted.length;
      ctx.font = "11px Arial";
      sorted.forEach((g, i) => {
        const y = pad.t + rh * i + rh / 2;
        ctx.fillStyle = colorOf[g.id]; ctx.fillText("■", 2, y + 3);
        ctx.fillStyle = "#E6EDF5";
        const lab = g.label.length > 22 ? g.label.slice(0, 21) + "…" : g.label;
        ctx.fillText(lab, 14, y + 3);
      });
      T.labels.forEach((c, i) => {
        const x = pad.l + (W - pad.l - pad.r) * i / (n - 1);
        const y = pad.t + rh * rowOf[c] + rh / 2;
        ctx.fillStyle = colorOf[c]; ctx.fillRect(x, y - 2.4, 2.2, 4.8);
      });
      ctx.fillStyle = GREY;
      ctx.fillText(T.dates[0], pad.l, H - 5);
      ctx.fillText(T.dates[n - 1], W - pad.r - 62, H - 5);
    }
    $("rgm-note").textContent =
      "k = " + RG.k + " chosen by silhouette score over " + RG.timeline.dates.length + " forecast origin days and " +
      RG.features.length + " standardized features (" + RG.features.join(", ") + "). Silhouette by k: " +
      Object.entries(RG.silhouette).map(([k, v]) => "k=" + k + ": " + fmt(v, 3)).join(" · ") +
      ". Clusters describe kinds of days in this region's own record; they are not a station grouping.";
  }

  /* ---------- MLOps / retraining status ---------- */
  function drawMlops() {
    const BM = X.build_meta, MR = BM.regions[rid];
    const chosenSummary = obj => {
      const c = {};
      Object.values(obj || {}).forEach(v => { if (v) c[v] = (c[v] || 0) + 1; });
      return Object.entries(c).map(([k, v]) => k + " ×" + v).join(", ") || "—";
    };
    const rows = [
      ["Last retrain (build run)", BM.generated_at + " UTC"],
      ["Retrain sanity vs shipped daily metrics", MR.sanity],
      ["Data span", MR.data_first + " → " + MR.data_last + " · " + MR.n_days + " priced days"],
      ["Daily origins", MR.train_origins + " train · " + MR.holdout_origins + " holdout (from " + MR.holdout_start + ")"],
      ["Daily models selected", chosenSummary(MR.daily_chosen)],
      ["Weekly models selected", chosenSummary(MR.weekly_chosen) + " · holdout from " + MR.weekly_holdout_start],
      ["Monthly models selected", chosenSummary(MR.monthly_chosen) + " · holdout from " + MR.monthly_holdout_start],
      ["Yearly outlook basis", "monthly climatology over " + MR.yearly_years.join(", ")],
      ["Market regimes", "k = " + MR.regimes_k + " day clusters"],
      ["Drift status", MR.drift_status + " — largest shift: " + MR.drift_max_feature + " at " + fmt(MR.drift_max_shift) + " train std"],
    ];
    $("mlops-tbl").innerHTML = rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join("");
  }
  $("fi-h").onchange = () => { drawFeatimp(); if (mode3d === "feat") c3.render(); };

  function drawAll() { drawHistory(); drawMoving(); drawOutlook(); drawLead(); drawFeatimp(); drawCorr(); drawOutlooks(); drawYearly(); drawRegimes(); drawMlops(); surfCtl.render(); c3.render(); }
  window.addEventListener("resize", drawAll);
  drawAll();
})();
