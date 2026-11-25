/* HydroTwin Energy — hub page for the merged twin. All values from data.js
   and energy_extra.js (build_meta from the latest build's retraining run). */
(function () {
  "use strict";
  const D = window.HTE_DATA, X = window.HTE_EXTRA;
  const $ = id => document.getElementById(id);
  const fmt = (n, d) => (n === null || n === undefined || isNaN(n)) ? "—" : Number(n).toFixed(d === undefined ? 2 : d);
  $("hub-cards").innerHTML = D.regions.map(r => {
    const mean = [1, 2, 3, 4, 5, 6, 7].reduce((a, h) => a + r.metrics[h].skill_pct, 0) / 7;
    return `<div class="hts-card">
      <div class="hts-cl">Energy region</div>
      <div class="hts-cv">${r.name}</div>
      <div class="hts-cd">${r.hub}</div>
      <div class="hts-cv">$${fmt(r.latest.last_price)}</div>
      <div class="hts-cd">latest, ${r.latest.last_price_date}</div>
      <div class="hts-cd">+1d MAE $${fmt(r.metrics[1].mae)} · skill ${fmt(r.metrics[1].skill_pct, 1)}% · mean skill +1..+7d ${fmt(mean, 1)}%</div>
      <div style="margin-top:8px"><a class="hts-navbtn" href="${r.id}.html">Open region →</a> <a class="hts-navbtn" href="${r.id}-3d.html">3D terrain</a></div>
    </div>`;
  }).join("");
  $("hub-pool").innerHTML =
    "<tr><th>Region</th><th>+1d MAE $</th><th>+1d RMSE $</th><th>+1d skill</th><th>+7d MAE $</th><th>+7d skill</th><th>Holdout origins</th><th>Holdout from</th></tr>" +
    D.regions.map(r => `<tr><td>${r.name}</td><td>${fmt(r.metrics[1].mae)}</td><td>${fmt(r.metrics[1].rmse)}</td><td>${fmt(r.metrics[1].skill_pct, 1)}%</td><td>${fmt(r.metrics[7].mae)}</td><td>${fmt(r.metrics[7].skill_pct, 1)}%</td><td>${r.holdout_origins}</td><td>${r.holdout_start}</td></tr>`).join("");
  $("hub-note").textContent = D.meta.note;
  D.regions.forEach(r => {
    const el = $("t3-price-" + r.id);
    if (el) el.textContent = "Latest $" + fmt(r.latest.last_price) + " · " + r.latest.last_price_date + " · " + r.hub;
  });

  /* ---------- MLOps: last build retrain (from build_meta) ---------- */
  const BM = X.build_meta;
  const chosenSummary = obj => {
    const c = {};
    Object.values(obj || {}).forEach(v => { if (v) c[v] = (c[v] || 0) + 1; });
    return Object.entries(c).map(([k, v]) => k + " ×" + v).join(", ") || "—";
  };
  $("hub-mlops").innerHTML =
    "<tr><th>Region</th><th>Data span</th><th>Daily models</th><th>Weekly models</th><th>Monthly models</th><th>Drift status</th><th>Retrain sanity</th></tr>" +
    D.regions.map(r => { const m = BM.regions[r.id]; return `<tr><td>${r.name}</td>` +
      `<td>${m.data_first} → ${m.data_last} (${m.n_days} priced days)</td>` +
      `<td>${chosenSummary(m.daily_chosen)}</td><td>${chosenSummary(m.weekly_chosen)}</td>` +
      `<td>${chosenSummary(m.monthly_chosen)}</td>` +
      `<td>${m.drift_status} (max ${m.drift_max_feature} ${fmt(m.drift_max_shift)}σ)</td>` +
      `<td>${m.sanity} vs shipped metrics</td></tr>`; }).join("") +
    `<tr><td colspan="7">Last retrain (this build): <b>${BM.generated_at}</b> UTC · ${BM.generator}</td></tr>`;
})();
