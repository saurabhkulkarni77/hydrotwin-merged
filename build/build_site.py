#!/usr/bin/env python3
"""Assemble the merged HydroTwin + HydroTwin Energy site.

- River side: fresh hydrotwin-site@main tree copied verbatim.
- Energy side: region pages + hub generated here; every number on them is
  rendered at runtime from the energy project's own data files
  (energy/data.js = shipped HTE_DATA snapshot; energy/energy_extra.js =
  backtest/diagnostics computed by gen_energy_extra.py from the pipeline).
- Index: the river station panel gains an 'Energy price regions' group and
  the header gains an Energy link. Nothing else in the river tree changes.
"""
import json, re, shutil
from pathlib import Path

import os
ROOT = Path(os.environ.get("MERGED_ROOT", Path.home() / "workspace" / "hydrotwin_merged"))
RIVER = Path(os.environ.get("MERGED_RIVER_SRC", ROOT / "river_src" / "hydrotwin-site-main"))
SITE = ROOT / "site"
BUILD = ROOT / "build"
ENERGY_WS = Path(os.environ.get("HTE_WS", Path.home() / "workspace" / "hydrotwin_energy"))

# The shared panel CSS lives in the river station pages themselves (ht-statsview).
def _statsview_css():
    page = (RIVER / "stations" / "USGS-01463500.html").read_text()
    m = re.search(r"/\* ht-statsview v1 \*/(.*?)</style>", page, re.S)
    assert m, "ht-statsview CSS block not found in river station page"
    return m.group(1)

CSS = _statsview_css()
EXTRA_CSS = """
.hts-sparks{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.hts-spark{background:#101E30;border:1px solid #22344C;border-radius:12px;padding:10px 12px}
.hts-spark h4{margin:0 0 6px;font-size:12.5px;color:#8FC1F2;text-transform:uppercase;letter-spacing:.04em}
.hts-spark canvas{width:100%;height:auto;display:block}
a.hts-navbtn.on{background:#23466E}
"""

REGIONS = [("palo", "Palo Verde"), ("np15", "CAISO NP15"), ("midc", "Mid-Columbia")]

def region_page(rid):
    others = "".join(
        f'<a class="hts-navbtn" href="{o}.html">{n}</a>' for o, n in REGIONS if o != rid)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Energy region · HydroTwin (merged twin)</title>
<style>/* ht-statsview v1 (shared with the river station pages) */
{CSS}
{EXTRA_CSS}</style></head>
<body><div class="hts-wrap">
  <header class="hts-header">
    <div class="hts-title">
      <h1 id="rg-name">Loading…</h1>
      <div class="hts-sub" id="rg-hub"></div>
      <div class="hts-sub2">Wholesale electricity price twin — same panel set as the river station pages, driven by the HydroTwin Energy models (ridge / MLP per horizon, selected on validation, scored on a time based holdout against persistence).</div>
      <span class="hts-span" id="rg-span"></span>
    </div>
    <nav class="hts-nav">
      <a class="hts-navbtn" href="../index.html">← River map</a>
      <a class="hts-navbtn" href="index.html">Energy regions</a>
      {others}
      <a class="hts-navbtn" href="../mlops.html">MLOps</a>
    </nav>
  </header>

  <section class="hts-cards" id="rg-cards" aria-label="Region statistics"></section>

  <section class="hts-sec" id="sec-history">
    <h2>Price history</h2>
    <div class="hts-ctl"><button id="btn-hist-1y">Last 365 days</button><button id="btn-hist-all">Full record</button></div>
    <canvas id="cv-history" class="hts-cv" width="1320" height="250"></canvas>
    <p class="hts-note">Daily day-ahead price for this hub over the full record held by the energy twin. The shaded span is the time based holdout: origins the shipped metrics were scored on, never used in training.</p>
  </section>

  <section class="hts-sec" id="sec-moving">
    <h2>Moving forecast — holdout walk-forward</h2>
    <div class="hts-ctl">
      <button id="mv-play">▶ Play</button><button id="mv-prev">◀ Step</button><button id="mv-next">Step ▶</button>
      <label>Horizon <select id="mv-h"><option>1</option><option>2</option><option>3</option><option selected>4</option><option>5</option><option>6</option><option>7</option></select> days</label>
    </div>
    <canvas id="cv-moving" class="hts-cv" width="1320" height="300"></canvas>
    <p class="hts-note" id="mv-read"></p>
    <p class="hts-note">Blue: observed prices up to the forecast origin. Green: the model forecast issued at that origin (the model selected per horizon on validation, refit on the training period only). Gold: what actually happened next. Red dashed: persistence (price stays at the last observed value). Shaded band: ±1.2816 × holdout residual σ. Every forecast here was recomputed by the energy pipeline's own training code on its cached raw data for this merged build, and the resulting holdout metrics reproduce the shipped HydroTwin Energy metrics exactly.</p>
  </section>

  <section class="hts-sec" id="sec-fc">
    <h2>7-day forecast</h2>
    <canvas id="cv-outlook" class="hts-cv" width="1320" height="240"></canvas>
    <table class="hts-tbl" id="fc-tbl" style="margin-top:10px"></table>
    <p class="hts-note">The latest forecast issued by the energy twin, with its ±1.2816σ band. This is a static snapshot (see the snapshot date above), not a live feed — the same status the standalone HydroTwin Energy site carries.</p>
  </section>

  <section class="hts-sec" id="sec-models">
    <h2>Model statistics</h2>
    <table class="hts-tbl" id="ms-tbl"></table>
    <h3>MAE by forecast lead — this region</h3>
    <canvas id="cv-lead" class="hts-cv" width="1320" height="220"></canvas>
    <h3>All energy regions (pooled view)</h3>
    <table class="hts-tbl" id="pool-tbl"></table>
    <p class="hts-note">Honest headline, carried over from the original HydroTwin Energy build: skill against persistence is small (about 0 to 6 percent on MAE) and is negative at some horizons — the +5 day line for CAISO NP15 is the known miss, where the validation selected MLP scored worse than persistence on the holdout. The tables above show every horizon, good and bad.</p>
  </section>

  <section class="hts-sec" id="sec-compare">
    <h2>Model compare — ridge vs MLP</h2>
    <table class="hts-tbl" id="cmp-tbl"></table>
    <p class="hts-note">Per horizon, ridge and MLP are both fit on the training period and compared on a validation slice (the last 20 percent of training). The winner is refit on the full training period and scored once on the holdout. Models predict the signed log price change from the last observed price, so persistence is the anchor they must beat; predictions are clipped to the training period's observed price range (maximum extended 25 percent).</p>
  </section>

  <section class="hts-sec" id="sec-ablation">
    <h2>Feature ablation</h2>
    <table class="hts-tbl" id="ab-tbl"></table>
    <p class="hts-note">Ridge model refit on the training origins with one feature group removed at a time, scored on the same holdout. A positive Δ means the group was helping. Computed for this merged build by the energy pipeline's own feature code.</p>
  </section>

  <section class="hts-sec" id="sec-drift">
    <h2>Drift</h2>
    <table class="hts-tbl" id="dr-tbl"></table>
    <p class="hts-note">Feature means on training origins versus holdout origins, with the shift expressed in training standard deviations. Large shifts mark regime change between the period the models learned and the period they were scored on.</p>
  </section>

  <section class="hts-sec" id="sec-band">
    <h2>Confidence band</h2>
    <table class="hts-tbl" id="band-tbl"></table>
    <p class="hts-note">The energy twin's interval method: a constant ±1.2816 × per horizon holdout residual σ around each forecast (about 80 percent if residuals were normal). This is the energy equivalent of the river pages' DACP confidence panels — the river twin fits distribution adjusted intervals per station, while the energy models ship residual σ bands, so the band shown here is the energy project's own published method rather than a refit of the river one.</p>
  </section>

  <section class="hts-sec" id="sec-corr">
    <h2>Multi-factor correlation</h2>
    <canvas id="cv-corr" class="hts-cv" width="1320" height="300"></canvas>
    <p class="hts-note">Pearson correlation of each input feature with the next day signed log price change, over all forecast origins. Correlation is not causation — it shows which signals move with tomorrow's price change in this record.</p>
  </section>

  <section class="hts-sec" id="sec-featimp">
    <h2>Features affecting price</h2>
    <div class="hts-ctl"><label>Horizon <select id="fi-h"><option selected>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option></select> days</label></div>
    <canvas id="cv-featimp" class="hts-cv" width="1320" height="300"></canvas>
    <p class="hts-note">Standardized ridge coefficients for the selected horizon (features are standardized before fitting, so coefficient size is comparable). Green pushes the price change up, red pushes it down. Shown for the ridge model, whose coefficients are directly readable; the MLP horizons are compared in the model compare table instead.</p>
  </section>

  <section class="hts-sec" id="sec-cond">
    <h2>Conditions — flow, snow, soil, gas</h2>
    <div class="hts-sparks">
      <div class="hts-spark"><h4>River flow (region gauges)</h4><canvas id="sp-flow" width="520" height="64"></canvas></div>
      <div class="hts-spark"><h4>Snow water equivalent</h4><canvas id="sp-swe" width="520" height="64"></canvas></div>
      <div class="hts-spark"><h4>Soil moisture</h4><canvas id="sp-soil" width="520" height="64"></canvas></div>
      <div class="hts-spark"><h4>Henry Hub gas</h4><canvas id="sp-gas" width="520" height="64"></canvas></div>
    </div>
    <h3>Feature values at the latest forecast origin</h3>
    <table class="hts-tbl" id="fl-tbl"></table>
  </section>

  <section class="hts-sec" id="sec-notebook">
    <h2>Region notebook</h2>
    <h3>Stations and feeds behind this region</h3>
    <ul class="hts-note" id="nb-stations" style="margin:0;padding-left:18px"></ul>
    <h3>Known limitations</h3>
    <ul class="hts-note" id="nb-limits" style="margin:0;padding-left:18px"></ul>
    <h3>Panels on the river pages with no energy equivalent</h3>
    <p class="hts-note">Weekly, monthly and yearly outlooks: the energy models are daily models with horizons of +1 to +7 days only — no weekly, monthly or yearly energy models were ever trained, so those outlook panels have nothing genuine to show here. The 3D scene and 3D data charts: a wholesale price hub has no river reach or terrain scene in either project, and no 3D data was built for the regions. Station clusters: the river twin clusters 14 stations by traits; three regions do not support a meaningful clustering. Live retraining badges (load new model, tuning status): the energy twin is a static snapshot by design, with no retraining feed.</p>
  </section>

  <p class="hts-note" style="margin-top:14px">Merged twin: river pages and energy pages share one index. River data: USGS daily flow and weather, Oct 2001 to Sep 2026. Energy data: ICE day-ahead indices republished by EIA, CAISO OASIS, EIA Henry Hub, USGS, NRCS SNOTEL and SCAN, Open-Meteo.</p>
</div>
<script src="data.js"></script>
<script src="energy_extra.js"></script>
<script>window.REGION_ID = "{rid}";</script>
<script src="energy_region.js"></script>
</body></html>
"""

HUB = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Energy price regions · HydroTwin (merged twin)</title>
<style>/* ht-statsview v1 (shared with the river station pages) */
{CSS}
{EXTRA_CSS}</style></head>
<body><div class="hts-wrap">
  <header class="hts-header">
    <div class="hts-title">
      <h1>Energy price regions</h1>
      <div class="hts-sub">HydroTwin Energy — wholesale electricity price forecasting, merged into the river twin</div>
      <div class="hts-sub2">Three hubs, each with the same panel set as a river station page: price history, a moving walk-forward forecast, a 7 day outlook, model statistics and compare, feature ablation, drift, correlation, feature importance, conditions and a region notebook.</div>
      <span class="hts-span" id="hub-note"></span>
    </div>
    <nav class="hts-nav">
      <a class="hts-navbtn" href="../index.html">← River map</a>
      <a class="hts-navbtn" href="../mlops.html">MLOps</a>
    </nav>
  </header>

  <section class="hts-cards" id="hub-cards" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))"></section>

  <section class="hts-sec">
    <h2>Pooled leaderboard — all 3 energy regions</h2>
    <table class="hts-tbl" id="hub-pool"></table>
    <p class="hts-note">Honest headline from the original build: skill against persistence is small (about 0 to 6 percent on MAE), with one disclosed miss — CAISO NP15 at +5 days, where the validation selected MLP scored well below persistence on the holdout. Every region page shows all seven horizons, including the negative ones.</p>
  </section>

  <section class="hts-sec">
    <h2>How the merge works</h2>
    <p class="hts-note">This merged twin keeps the river twin exactly as it was — all 14 stations, their 3D scenes and every panel — and adds the energy twin's three regions as first class pages next to them, reachable from the same station list on the map page. Energy forecasts come from the HydroTwin Energy models: per horizon ridge or MLP on the signed log price change, selected on a validation slice and scored once on a time based holdout against persistence, using 19 daily features built from USGS flow, NRCS soil and snow, Open-Meteo weather and Henry Hub gas — the same public feeds the river twin uses.</p>
    <p class="hts-note">Panels with no genuine energy equivalent are not faked: weekly, monthly and yearly outlooks (energy models are +1 to +7 days only), the 3D terrain scene and 3D data charts (a price hub has no river reach), station clustering (three regions cannot be clustered meaningfully), and live retraining badges (the energy twin is a static snapshot by design). Each region page states this in its notebook section.</p>
  </section>
</div>
<script src="data.js"></script>
<script src="energy_hub.js"></script>
</body></html>
"""


def main():
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(RIVER, SITE, ignore=shutil.ignore_patterns(".git"))
    en = SITE / "energy"
    en.mkdir()
    shutil.copy(ENERGY_WS / "site" / "data.js", en / "data.js")
    shutil.copy(BUILD / "energy_extra.js", en / "energy_extra.js")
    shutil.copy(BUILD / "energy_region.js", en / "energy_region.js")
    shutil.copy(BUILD / "energy_hub.js", en / "energy_hub.js")
    for rid, _ in REGIONS:
        (en / f"{rid}.html").write_text(region_page(rid))
    (en / "index.html").write_text(HUB)

    # --- patch the merged index only ---
    idx = SITE / "index.html"
    s = idx.read_text()
    # 1) energy group in the station panel, right after the river list
    anchor = "</ul>"
    pos = s.find('id="stpanel"')
    end = s.find(anchor, pos)
    assert pos != -1 and end != -1, "stpanel list not found"
    energy_group = (
        '</ul><h3>Energy price regions</h3><ul>'
        '<li><a href="energy/palo.html"><span class="sid">PALO VERDE</span><br>Palo Verde — Southwest (Colorado River)</a></li>'
        '<li><a href="energy/np15.html"><span class="sid">CAISO NP15</span><br>CAISO NP15 — Northern California</a></li>'
        '<li><a href="energy/midc.html"><span class="sid">MID-COLUMBIA</span><br>Mid-Columbia — Pacific Northwest</a></li>'
        '<li><a href="energy/index.html"><span class="sid">ENERGY</span><br>All energy regions →</a></li>')
    s = s[:end] + energy_group + s[end:]
    # 2) energy link beside the MLOps link
    mlops = 'MLOps &mdash; Level 2 pipeline status &rarr;</a>'
    assert mlops in s, "mlops link not found"
    s = s.replace(mlops, mlops + ' &nbsp;·&nbsp; <a href="energy/index.html" style="color:#8FC1F2;font-weight:bold">Energy Price Twin &rarr;</a>', 1)
    # 3) title note: keep original title; add merged marker in the help card text
    s = s.replace("HydroTwin VR · US river gauges",
                  "HydroTwin VR · US river gauges + 3 energy price regions (merged twin)", 1)
    idx.write_text(s)
    print("site assembled at", SITE)
    total = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file())
    print("total bytes:", total)


if __name__ == "__main__":
    main()
