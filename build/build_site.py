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


def _reconcile_energy_ws():
    """Fill gaps in the energy workspace (HTE_WS) from inputs bundled in this
    repo under build/energy_inputs/.

    Why: the hydrotwin-energy GitHub repo does not contain site/data.js (the
    shipped snapshot this script copies into energy/data.js) — it exists only
    in the local workspace. A copy is bundled here. Only files MISSING in the
    workspace are copied; existing files are never overwritten. (In the
    Actions workflow gen_energy_extra.py runs first and already reconciles,
    so this is a no-op there; it keeps build_site.py runnable standalone.)
    """
    bundled = ROOT / "build" / "energy_inputs"
    if not bundled.is_dir():
        return
    copied = 0
    src = bundled / "site" / "data.js"
    dst = ENERGY_WS / "site" / "data.js"
    if src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    src_data = bundled / "data"
    if src_data.is_dir():
        for f in sorted(src_data.rglob("*")):
            if f.is_file():
                d = ENERGY_WS / "data" / f.relative_to(src_data)
                if not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, d)
                    copied += 1
    if copied:
        print(f"energy workspace reconciled from bundled inputs: "
              f"{copied} file(s) copied into {ENERGY_WS}")

# The shared panel CSS lives in the river station pages themselves (ht-statsview).
def _statsview_css():
    page = (RIVER / "stations" / "USGS-01463500.html").read_text()
    m = re.search(r"/\* ht-statsview v1 \*/(.*?)</style>", page, re.S)
    assert m, "ht-statsview CSS block not found in river station page"
    return m.group(1)


def _river_css_block(marker, src="stations/USGS-01463500.html"):
    """One <style> block of a river page, verbatim, found by a marker string.
    The energy pages reuse the river twin's own panel/button CSS blocks
    (lazypanels bar, htc3 data-chart chrome, clusters legend, 3D scene
    chrome) extracted from the river pages at build time, exactly like the
    ht-statsview block above — never retyped here."""
    page = (RIVER / src).read_text()
    for m in re.finditer(r"<style>(.*?)</style>", page, re.S):
        if marker in m.group(1):
            return m.group(1)
    raise AssertionError(f"river CSS block {marker!r} not found in {src}")


CSS = _statsview_css()
# River-verbatim blocks: the panel toggle bar (ht-lazypanels), the 3D data
# charts chrome (#htc3-root), the same chrome re-scoped to the forecast
# surface panel's root (#htsfc-root — identical declarations, second scope
# because a page cannot repeat an id), and the clusters panel (#htclusters-root).
HTC3_CSS = _river_css_block("#htc3-root .htc3-ctl")
EXTRA_CSS = (
    _river_css_block("/* ht-lazypanels v1 */")
    + HTC3_CSS
    + HTC3_CSS.replace("#htc3-root", "#htsfc-root")
    + _river_css_block("#htclusters-root .htclusters-h")
    + """
a.hts-navbtn.on{background:#23466E}
""")
# River 3D scene chrome (hud / links / status / attr), verbatim from the
# river station 3D page's first style block.
SCENE_CSS = _river_css_block("height:100%;overflow:hidden",
                             src="stations/USGS-01463500-3d.html")

# Real hub geography for the 3D scene pages. elev/geoid are approximate site
# values used ONLY to seat the tile scene at local ground level (the same
# role REFE + GEOID18 play on the river 3D pages); they are not data.
HUB_GEO = {
    "palo": dict(lat=33.3886, lon=-112.8617, elev=290, geoid=-33.4,
                 place="Palo Verde Nuclear Generating Station, Wintersburg, Arizona",
                 note="The Palo Verde hub is named for this switchyard: the largest "
                      "generating station in the US, whose output anchors Southwest "
                      "day-ahead prices."),
    "np15": dict(lat=38.5816, lon=-121.4944, elev=10, geoid=-31.8,
                 place="Sacramento, California",
                 note="NP15 is a virtual trading hub — an average of generator nodes "
                      "across Northern California, with no single physical site. The "
                      "scene is placed at Sacramento, the region's load centre, beside "
                      "the Sacramento River gauges this twin's features draw on."),
    "midc": dict(lat=45.5946, lon=-121.1787, elev=35, geoid=-22.8,
                 place="The Dalles, Oregon — Columbia River",
                 note="The Mid-Columbia hub trades around the Columbia River dams; "
                      "this scene sits at The Dalles, the same reach as river station "
                      "USGS 14105700 (Columbia River at The Dalles) in this twin."),
}


def _ion_token():
    """The Cesium Ion client token the river 3D pages themselves ship with
    (asset 2275207, Google Photorealistic 3D Tiles) — copied from the river
    source so the energy scene pages use the project's own public token."""
    page = (RIVER / "stations" / "USGS-01463500-3d.html").read_text()
    m = re.search(r"HT_T3_TOKEN\s*=\s*'([^']+)'", page)
    assert m, "Ion token not found in river 3D page"
    return m.group(1)

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
      <div class="hts-sub2">Wholesale electricity price twin — same panel set as the river station pages, driven by the HydroTwin Energy models (the full shared roster per horizon, selected on validation, scored on a time based holdout against persistence).</div>
      <span class="hts-span" id="rg-span"></span>
    </div>
    <nav class="hts-nav">
      <a class="hts-navbtn" href="../index.html">← River map</a>
      <a class="hts-navbtn" href="index.html">Energy regions</a>
      {others}
      <a class="hts-navbtn" href="{rid}-3d.html">3D scene</a>
      <a class="hts-navbtn" href="../mlops.html">MLOps</a>
    </nav>
  </header>

  <section class="hts-cards" id="rg-cards" aria-label="Region statistics"></section>

  <section class="hts-sec htlp-off" id="hts-sec-terrain">
    <h2>3D terrain — this hub in photorealistic 3D</h2>
    <p class="hts-note">The same Google Photorealistic 3D Tiles terrain the river 3D pages use (Three.js + the NASA AMMOS tile renderer, Cesium ion), seated at this hub's real geography: {HUB_GEO[rid]["place"]} · {HUB_GEO[rid]["lat"]:.4f}° N, {abs(HUB_GEO[rid]["lon"]):.4f}° W — with a gold pin on the hub and this region's live price summary, and Enter VR on a headset. <a href="{rid}-3d.html" style="color:#8FC1F2">Open the 3D terrain scene →</a></p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-hydro">
    <h2>Price history</h2>
    <div class="hts-ctl"><button id="btn-hist-1y">Last 365 days</button><button id="btn-hist-all">Full record</button></div>
    <canvas id="cv-history" class="hts-cv" width="1320" height="250"></canvas>
    <p class="hts-note">Daily day-ahead price for this hub over the full record held by the energy twin. The shaded span is the time based holdout: origins the shipped metrics were scored on, never used in training.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-moving">
    <h2>Moving forecast — holdout walk-forward</h2>
    <div class="hts-ctl">
      <button id="mv-play">▶ Play</button><button id="mv-prev">◀ Step</button><button id="mv-next">Step ▶</button>
      <label>Horizon <select id="mv-h"><option>1</option><option>2</option><option>3</option><option selected>4</option><option>5</option><option>6</option><option>7</option></select> days</label>
    </div>
    <canvas id="cv-moving" class="hts-cv" width="1320" height="300"></canvas>
    <p class="hts-note" id="mv-read"></p>
    <p class="hts-note">Blue: observed prices up to the forecast origin. Green: the model forecast issued at that origin (the model selected per horizon on validation, refit on the training period only). Gold: what actually happened next. Red dashed: persistence (price stays at the last observed value). Shaded band: ±1.2816 × holdout residual σ. Every forecast here was recomputed by the energy pipeline's own training code on its cached raw data for this merged build, and the resulting holdout metrics reproduce the shipped HydroTwin Energy metrics exactly.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-fc">
    <h2>7-day forecast</h2>
    <canvas id="cv-outlook" class="hts-cv" width="1320" height="240"></canvas>
    <table class="hts-tbl" id="fc-tbl" style="margin-top:10px"></table>
    <p class="hts-note">The latest forecast issued by the energy twin, with its ±1.2816σ band. This is a static snapshot (see the snapshot date above), not a live feed — the same status the standalone HydroTwin Energy site carries.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-models">
    <h2>Model statistics</h2>
    <table class="hts-tbl" id="ms-tbl"></table>
    <h3>MAE by forecast lead — this region</h3>
    <canvas id="cv-lead" class="hts-cv" width="1320" height="220"></canvas>
    <h3>All energy regions (pooled view)</h3>
    <table class="hts-tbl" id="pool-tbl"></table>
    <p class="hts-note">Honest headline: skill against persistence is small (a few percent on MAE for the best models) and is negative at some horizons and for some models. The tables show every horizon and every model, good and bad.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-compare">
    <h2>Model compare — the full shared roster</h2>
    <table class="hts-tbl" id="cmp-tbl"></table>
    <p class="hts-note">This twin runs the SAME model roster as the HydroTwin river twin, so the two are directly comparable: ridge, MLP, Random Forest and MiniRocket, plus the paper roster — LSTM, physics informed LSTM, Transformer, Temporal Fusion Transformer, PatchTST, two Gaussian process models, a graph convolutional network, ARIMA and seasonal ARIMA. Per horizon, every model is fit on the training period and compared on a validation slice (the last 20 percent of training); the winner is refit on the full training period and scored once on the holdout, and the table shows every model's holdout MAE with the selected model in bold. Two adaptations are energy specific and stated plainly: the physics informed LSTM's penalty pulls predictions toward an equilibrium price implied by the exogenous drivers (gas price, temperature, hydro flow) fit on training data only — a driver equilibrium penalty, not river physics; and the graph convolutional network's nodes are the three price regions, linked by the similarity of their training period price and feature regimes, the energy analogue of the river net's snow regime links. Models predict the signed log price change from the last observed price, so persistence is the anchor they must beat; predictions are clipped to the training period's observed price range (maximum extended 25 percent).</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-ablation">
    <h2>Feature ablation</h2>
    <table class="hts-tbl" id="ab-tbl"></table>
    <p class="hts-note">Ridge model refit on the training origins with one feature group removed at a time, scored on the same holdout. A positive Δ means the group was helping. Computed for this merged build by the energy pipeline's own feature code.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-weekly">
    <h2>Weekly outlook</h2>
    <canvas id="cv-weekly" class="hts-cv" width="1320" height="250"></canvas>
    <table class="hts-tbl" id="wk-tbl" style="margin-top:10px"></table>
    <h3>Issued outlook — next 12 weeks</h3>
    <table class="hts-tbl" id="wk-fc"></table>
    <p class="hts-note">Weekly mean price models trained for this merged build on the region's real daily series, aggregated to weeks (weeks ending Sunday, at least 3 priced days each). Per horizon, four candidates compete on a validation slice: a ridge on lagged weekly means, gas, flow and calendar features, a week of year climatology, a small Transformer over weekly feature windows, and a seasonal ARIMA (weekly period 52) rolled forward operationally; the winner is refit on the training weeks and scored once on the holdout (<span id="wk-ho"></span>). The issued outlook refits the winner on all weeks. Same protocol as the daily models, one scale up — the river twin's weekly outlook slot, with energy models genuinely trained at the weekly scale.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-monthly">
    <h2>Monthly outlook</h2>
    <canvas id="cv-monthly" class="hts-cv" width="1320" height="250"></canvas>
    <table class="hts-tbl" id="mo-tbl" style="margin-top:10px"></table>
    <h3>Issued outlook — next 6 months</h3>
    <table class="hts-tbl" id="mo-fc"></table>
    <p class="hts-note">Monthly mean price models (months with at least 15 priced days), trained the same way as the weekly models: ridge on lagged monthly means, gas and calendar features against a calendar month climatology, a small Transformer and a seasonal ARIMA (monthly period 12), winner by validation, scored once on the holdout (<span id="mo-ho"></span>). Read the n column before trusting a line: with only a few years of history the monthly holdout holds a handful of origins, so these scores are indicative, not precise.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-yearly">
    <h2>Yearly outlook</h2>
    <canvas id="cv-yearly" class="hts-cv" width="1320" height="250"></canvas>
    <table class="hts-tbl" id="yr-tbl" style="margin-top:10px"></table>
    <p class="hts-note" id="yr-note"></p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-drift">
    <h2>Drift</h2>
    <table class="hts-tbl" id="dr-tbl"></table>
    <p class="hts-note">Feature means on training origins versus holdout origins, with the shift expressed in training standard deviations. Large shifts mark regime change between the period the models learned and the period they were scored on.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-band">
    <h2>Confidence band</h2>
    <table class="hts-tbl" id="band-tbl"></table>
    <p class="hts-note">The energy twin's interval method: a constant ±1.2816 × per horizon holdout residual σ around each forecast (about 80 percent if residuals were normal). This is the energy equivalent of the river pages' DACP confidence panels — the river twin fits distribution adjusted intervals per station, while the energy models ship residual σ bands, so the band shown here is the energy project's own published method rather than a refit of the river one.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-3dchart">
    <h2>Forecast surface in 3D</h2>
    <div id="htsfc-root"><div class="htc3-view">
      <canvas id="cv-surface" class="htc3-canvas" aria-label="Interactive 3D surface: mean holdout forecast price by origin month and forecast horizon. Drag to orbit, scroll to zoom, hover for exact values"></canvas>
      <div class="htc3-tip" id="tip-surface"></div>
    </div></div>
    <p class="hts-note">Mean holdout forecast price by origin calendar month (x) and forecast horizon +1 to +7 days (z), computed from this region's genuine walk-forward backtest — every cell averages the actual forecasts the models issued from origins in that month. Months with no holdout origins stay empty: the holdout covers only part of the year (see the moving forecast panel), so this surface describes the holdout months, not a full year of seasons. Drag to orbit, scroll to zoom, hover a cell for its values. This is the energy counterpart of the river pages' 3D forecast chart.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-corr">
    <h2>Multi-factor correlation</h2>
    <canvas id="cv-corr" class="hts-cv" width="1320" height="300"></canvas>
    <p class="hts-note">Pearson correlation of each input feature with the next day signed log price change, over all forecast origins. Correlation is not causation — it shows which signals move with tomorrow's price change in this record.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-featimp">
    <h2>Features affecting price</h2>
    <div class="hts-ctl"><label>Horizon <select id="fi-h"><option selected>1</option><option>2</option><option>3</option><option>4</option><option>5</option><option>6</option><option>7</option></select> days</label></div>
    <canvas id="cv-featimp" class="hts-cv" width="1320" height="300"></canvas>
    <p class="hts-note">Standardized ridge coefficients for the selected horizon (features are standardized before fitting, so coefficient size is comparable). Green pushes the price change up, red pushes it down. Shown for the ridge model, whose coefficients are directly readable; every roster model is compared in the model compare table instead.</p>
  </section>

  <section class="hts-sec" id="hts-sec-charts3d">
    <h2>Data charts in 3D</h2>
    <p class="hts-note">The two charts above, rendered in 3D from exactly the same data: the price history is the last 365 days of this region's price record, and the bars are the ridge feature importances from the same data as the 2D chart (horizon follows the selector in the features panel). Drag to orbit, scroll to zoom, hover a point or a bar to read its exact values. The photorealistic terrain scene for this hub is on the region's <a href="{rid}-3d.html" style="color:#8FC1F2">3D scene page</a>.</p>
    <div id="htc3-root">
    <div class="htc3-ctl">
      <button type="button" class="htc3-mode htc3-on" data-mode="price">Price history 3D</button>
      <button type="button" class="htc3-mode" data-mode="feat">Feature importance 3D</button>
    </div>
    <div class="htc3-view">
      <canvas id="cv-3d" class="htc3-canvas" aria-label="Interactive 3D chart: drag to orbit, scroll to zoom, hover for exact values"></canvas>
      <div class="htc3-tip" id="tip-3d"></div>
    </div>
    </div>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-clusters">
    <h2>Market regimes — day clusters</h2>
    <div id="htclusters-root">
    <p class="hts-note">The river twin clusters its 14 stations by how their rivers behave; three regions cannot be clustered meaningfully, so this panel clusters DAYS instead. Every forecast origin day is a point in this region's standardized daily feature space (the model features plus 7 day price volatility), grouped by k means with k chosen by silhouette score. Days in one regime share a market character. The table gives each regime's size and price statistics; regime names are assigned by rule from those statistics, not by hand. The scatter shows the days in the first two principal components of the same features, and the timeline shows when each regime occurred.</p>
    <table class="hts-tbl" id="rgm-tbl"></table>
    <h3>Days in feature space (PCA)</h3>
    <canvas id="cv-rgm-pca" class="hts-cv" width="1320" height="300"></canvas>
    <div class="htclusters-legend" id="rgm-legend"></div>
    <h3>Regime timeline</h3>
    <canvas id="cv-rgm-tl" class="hts-cv" width="1320" height="170"></canvas>
    <p class="hts-note" id="rgm-note"></p>
    </div>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-cond">
    <h2>Conditions — flow, snow, soil, gas</h2>
    <div class="hts-panels">
      <div class="hts-card"><div class="hts-cl">River flow (region gauges)</div><canvas id="sp-flow" width="520" height="64" style="width:100%;height:auto;display:block;margin-top:6px"></canvas></div>
      <div class="hts-card"><div class="hts-cl">Snow water equivalent</div><canvas id="sp-swe" width="520" height="64" style="width:100%;height:auto;display:block;margin-top:6px"></canvas></div>
      <div class="hts-card"><div class="hts-cl">Soil moisture</div><canvas id="sp-soil" width="520" height="64" style="width:100%;height:auto;display:block;margin-top:6px"></canvas></div>
      <div class="hts-card"><div class="hts-cl">Henry Hub gas</div><canvas id="sp-gas" width="520" height="64" style="width:100%;height:auto;display:block;margin-top:6px"></canvas></div>
    </div>
    <h3>Feature values at the latest forecast origin</h3>
    <table class="hts-tbl" id="fl-tbl"></table>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-mlops">
    <h2>MLOps — retraining status</h2>
    <table class="hts-tbl" id="mlops-tbl"></table>
    <p class="hts-note">The energy models are retrained every time this merged site is built: the build re-runs the energy pipeline's own training code on its cached raw data and regenerates every forecast, backtest and diagnostic on these pages, at the daily, weekly and monthly scales. Every value above is read from that run's own outputs — the retrain timestamp is when this build's training run executed, and the sanity line records whether the retrained daily models reproduced the shipped HydroTwin Energy holdout metrics. This mirrors the river twin's MLOps status presentation (see the <a href="../mlops.html" style="color:#8FC1F2">river MLOps page</a>); the energy side has no separate live feed because its retraining happens inside the build itself.</p>
  </section>

  <section class="hts-sec htlp-off" id="hts-sec-notebook">
    <h2>Region notebook</h2>
    <h3>Stations and feeds behind this region</h3>
    <ul class="hts-note" id="nb-stations" style="margin:0;padding-left:18px"></ul>
    <h3>Known limitations</h3>
    <ul class="hts-note" id="nb-limits" style="margin:0;padding-left:18px"></ul>
    <h3>What still differs from the river pages</h3>
    <p class="hts-note">Yearly outlook: the river twin fits yearly models on 25 years of flow; this region has only a few years of price history (the yearly panel states the exact span), so the yearly outlook here is a monthly climatology with its year to year spread, checked by leave-one-year-out validation instead of holdout scores — no fitted yearly model is claimed; a seasonal ARIMA line fit on the monthly series is drawn next to it for comparison, with its own expanding window fold scores, because SARIMA is the river roster's yearly winner. 3D scene: the region's 3D page uses the same Google Photorealistic 3D Tiles scene as the river 3D pages, placed at the hub's real geography, but a price hub has no gauge reach, water surface or rating curve, so the scene carries the hub marker and the price panels rather than flow ribbons and flood layers. Clusters: the river panel clusters stations; the energy panel clusters days into market regimes (above), which is the genuine equivalent for three regions.</p>
  </section>

  <!-- ht-lazypanels v1 -->
  <div id="htlp-bar" role="group" aria-label="Region panels"><span class="htlp-t">PANELS</span><button type="button" class="htlp-btn" aria-pressed="false">3D terrain</button><button type="button" class="htlp-btn" aria-pressed="false">Price history</button><button type="button" class="htlp-btn" aria-pressed="false">Moving forecast</button><button type="button" class="htlp-btn" aria-pressed="false">7-day forecast</button><button type="button" class="htlp-btn" aria-pressed="false">Model statistics</button><button type="button" class="htlp-btn" aria-pressed="false">Model compare</button><button type="button" class="htlp-btn" aria-pressed="false">Feature ablation</button><button type="button" class="htlp-btn" aria-pressed="false">Weekly outlook</button><button type="button" class="htlp-btn" aria-pressed="false">Monthly outlook</button><button type="button" class="htlp-btn" aria-pressed="false">Yearly outlook</button><button type="button" class="htlp-btn" aria-pressed="false">Drift</button><button type="button" class="htlp-btn" aria-pressed="false">Confidence band</button><button type="button" class="htlp-btn" aria-pressed="false">Forecast surface in 3D</button><button type="button" class="htlp-btn" aria-pressed="false">Multi-factor correlation</button><button type="button" class="htlp-btn" aria-pressed="false">Features affecting price</button><button type="button" class="htlp-btn" aria-pressed="false">Market regimes</button><button type="button" class="htlp-btn" aria-pressed="false">Conditions</button><button type="button" class="htlp-btn" aria-pressed="false">MLOps</button><button type="button" class="htlp-btn" aria-pressed="false">Region notebook</button></div>
  <script>
  // ht-lazypanels v1 toggle (energy port of the river loader): the river
  // loader additionally lazy-boots each panel's own scripts on first open;
  // the energy panels boot eagerly with energy_region.js, so opening a panel
  // fires the window resize handler instead (drawAll) to size canvases that
  // were hidden at load. Toggle semantics otherwise match the river pages.
  (function () {{
    var CFG = [{{sec:"hts-sec-terrain"}},{{sec:"hts-sec-hydro"}},{{sec:"hts-sec-moving"}},{{sec:"hts-sec-fc"}},{{sec:"hts-sec-models"}},{{sec:"hts-sec-compare"}},{{sec:"hts-sec-ablation"}},{{sec:"hts-sec-weekly"}},{{sec:"hts-sec-monthly"}},{{sec:"hts-sec-yearly"}},{{sec:"hts-sec-drift"}},{{sec:"hts-sec-band"}},{{sec:"hts-sec-3dchart"}},{{sec:"hts-sec-corr"}},{{sec:"hts-sec-featimp"}},{{sec:"hts-sec-clusters"}},{{sec:"hts-sec-cond"}},{{sec:"hts-sec-mlops"}},{{sec:"hts-sec-notebook"}}];
    CFG.forEach(function (cfg, i) {{
      var btn = document.querySelectorAll('.htlp-btn')[i];
      var el = document.getElementById(cfg.sec);
      if (!btn || !el) return;
      btn.addEventListener('click', function () {{
        var opening = el.classList.contains('htlp-off');
        if (opening) {{
          el.classList.remove('htlp-off');
          btn.classList.add('htlp-on'); btn.setAttribute('aria-pressed', 'true');
          window.dispatchEvent(new Event('resize'));
          el.scrollIntoView({{ block: 'start' }});
        }} else {{
          el.classList.add('htlp-off');
          btn.classList.remove('htlp-on'); btn.setAttribute('aria-pressed', 'false');
        }}
      }});
    }});
  }})();
  </script>
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
      <div class="hts-sub2">Three hubs, each with the same panel set as a river station page: price history, a moving walk-forward forecast, 7 day, weekly, monthly and yearly outlooks, model statistics and compare, feature ablation, drift, a 3D forecast surface, correlation, feature importance, 3D data charts, market regime day clusters, conditions, MLOps retraining status and a region notebook — plus a photorealistic 3D scene page at each hub's real geography.</div>
      <span class="hts-span" id="hub-note"></span>
    </div>
    <nav class="hts-nav">
      <a class="hts-navbtn" href="../index.html">← River map</a>
      <a class="hts-navbtn" href="../mlops.html">MLOps</a>
    </nav>
  </header>

  <section class="hts-cards" id="hub-cards"></section>

  <section class="hts-sec" id="sec-terrain">
    <h2>3D terrain — photorealistic scene at each hub</h2>
    <p class="hts-note">The energy twin uses the same 3D terrain as the river twin: Google Photorealistic 3D Tiles streamed through the NASA AMMOS tile renderer (Three.js, Cesium ion asset 2275207), seated at each hub's real geography with a gold pin on the hub and the region's live price summary. Drag to orbit, scroll to zoom; on a VR headset the scene pages offer Enter VR, like the river 3D pages.</p>
    <div class="hts-cards">
      <div class="hts-card">
        <div class="hts-cl">3D terrain</div>
        <div class="hts-cv">Palo Verde</div>
        <div class="hts-cd">Palo Verde Nuclear Generating Station, Wintersburg, Arizona · 33.3886° N, 112.8617° W</div>
        <div class="hts-cd" id="t3-price-palo"></div>
        <div style="margin-top:8px"><a class="hts-navbtn" href="palo-3d.html">Open 3D terrain →</a></div>
      </div>
      <div class="hts-card">
        <div class="hts-cl">3D terrain</div>
        <div class="hts-cv">CAISO NP15</div>
        <div class="hts-cd">Sacramento, California — NP15 is a virtual hub, so the scene sits at Sacramento, the region's load centre · 38.5816° N, 121.4944° W</div>
        <div class="hts-cd" id="t3-price-np15"></div>
        <div style="margin-top:8px"><a class="hts-navbtn" href="np15-3d.html">Open 3D terrain →</a></div>
      </div>
      <div class="hts-card">
        <div class="hts-cl">3D terrain</div>
        <div class="hts-cv">Mid-Columbia</div>
        <div class="hts-cd">The Dalles, Oregon — Columbia River, at the Mid-Columbia dam reach · 45.5946° N, 121.1787° W</div>
        <div class="hts-cd" id="t3-price-midc"></div>
        <div style="margin-top:8px"><a class="hts-navbtn" href="midc-3d.html">Open 3D terrain →</a></div>
      </div>
    </div>
  </section>

  <section class="hts-sec">
    <h2>Pooled leaderboard — all 3 energy regions</h2>
    <table class="hts-tbl" id="hub-pool"></table>
    <p class="hts-note">Honest headline: skill against persistence is small (a few percent on MAE for the best models) and negative at some horizons and for some models. Every region page shows all seven horizons and the full model roster, including the negative lines.</p>
  </section>

  <section class="hts-sec">
    <h2>Energy MLOps — last build retrain</h2>
    <table class="hts-tbl" id="hub-mlops"></table>
    <p class="hts-note">Every merged build retrains the energy models from the pipeline's cached raw data — daily, weekly and monthly scales — and regenerates all forecasts and diagnostics. The timestamp and per region status above come from the latest build run's own outputs, mirroring the river twin's MLOps status page.</p>
  </section>

  <section class="hts-sec">
    <h2>How the merge works</h2>
    <p class="hts-note">This merged twin keeps the river twin exactly as it was — all 14 stations, their 3D scenes and every panel — and adds the energy twin's three regions as first class pages next to them, reachable from the same station list on the map page. Energy forecasts come from the HydroTwin Energy models: the same model roster as the river twin (ridge, MLP, Random Forest, MiniRocket, LSTM, physics informed LSTM, Transformer, Temporal Fusion Transformer, PatchTST, two Gaussian processes, a graph convolutional network over the three regions, ARIMA and SARIMA) on the signed log price change, selected per horizon on a validation slice and scored once on a time based holdout against persistence, using daily features built from USGS flow, NRCS soil and snow, Open-Meteo weather and Henry Hub gas — the same public feeds the river twin uses. Weekly and monthly outlooks are trained the same way at their own scales, with ridge, climatology, Transformer and SARIMA as candidates; the yearly outlook is a monthly climatology shown next to a SARIMA line for comparison, because a few years of prices cannot support a fitted yearly model on their own.</p>
    <p class="hts-note">Three honest differences from the river pages remain, and each region page states them in its notebook: the yearly outlook is a climatology (with a SARIMA comparison line) rather than a fitted yearly model; the 3D scene pages show the hub's real geography with price panels but no flow ribbons or flood layers (a price hub has no gauge reach); and the clusters panel clusters days into market regimes rather than stations, which three regions cannot support.</p>
  </section>
</div>
<script src="data.js"></script>
<script src="energy_extra.js"></script>
<script src="energy_hub.js"></script>
</body></html>
"""


SCENE_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HydroTwin 3D · __NAME__</title>
<style>__SCENE_CSS__
 #scene{position:fixed;inset:0;width:100%;height:100%;display:block;cursor:grab;touch-action:none}
 #vrbtn{position:absolute;bottom:20px;left:calc(50% - 60px);width:120px;padding:12px 6px;border:1px solid #fff;border-radius:4px;background:rgba(0,0,0,.1);color:#fff;font:normal 13px sans-serif;text-align:center;opacity:.5;outline:none;z-index:999;cursor:pointer;display:none}
 #vrbtn:hover{opacity:1}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"},"scopes":{"../vendor3d/":{"three":"../vendor3d/three.module.min.js"}}}</script>
</head>
<body>
<canvas id="scene" aria-label="Photorealistic 3D scene at the hub. Drag to orbit, scroll to zoom."></canvas>
<div id="hud"><span class="tg" id="hud-tg" title="Collapse / expand">–</span>
  <div class="t" id="rg-name">__NAME__</div>
  <div class="more">
    <div>__PLACE__ · __LAT__, __LON__</div>
    <div>__NOTE__</div>
    <div>The gold pin marks the hub location in the same Google Photorealistic 3D Tiles
  scene the river 3D pages use. A price hub has no gauge reach, water surface or
  rating curve, so this scene carries the hub marker and the live price summary —
  the forecasts, ribbons and flood layers stay on the river pages, and every
  energy panel is on the region page.</div>
    <div id="live"></div>
  </div>
</div>
<div id="links">
  <a href="__RID__.html">← Region page</a>
  <a href="index.html">Energy regions</a>
  <a href="../index.html">River map</a>
  __SWITCH__
</div>
<button id="vrbtn" type="button">ENTER VR</button>
<div id="status">Loading 3D tiles…</div>
<div id="attr">Basemap: Google Maps · 3D tiles access: Cesium ion · Prices: HydroTwin Energy</div>
<script>
document.getElementById('hud-tg').addEventListener('click', function () {
  var hud = document.getElementById('hud');
  hud.classList.toggle('min');
  this.textContent = hud.classList.contains('min') ? '+' : '–';
});
</script>
<script src="data.js"></script>
<script>
  (function () {
    var R = window.HTE_DATA.regions.find(function (r) { return r.id === "__RID__"; });
    var f = R.forecast.slice(0, 3).map(function (x) {
      return '<div><b>Forecast ' + x.date + '</b> $' + x.pred.toFixed(2) + '/MWh</div>';
    }).join("");
    document.getElementById("live").innerHTML =
      '<div><b>Latest price</b> $' + R.latest.last_price.toFixed(2) + '/MWh · ' +
      R.latest.last_price_date + '</div>' + f +
      '<div><b>Forecast issued</b> ' + R.forecast_origin + '</div>';
  })();
</script>
<script type="module">
  const stat = document.getElementById("status");
  const LAT = __LATV__, LON = __LONV__, ELEV = __ELEVV__, GEOID = __GEOIDV__;
  window.addEventListener("error", function (e) {
    stat.textContent = "3D scene error: " + (e.message || "failed to load") +
      " — the region page panels are unaffected.";
  });
  try {
    const T3 = await import("../vendor3d/three.module.min.js");
    const dep = await import("../vendor3d/dependencies.js");
    if (typeof T3.Material.prototype.onBuild !== "function")
      T3.Material.prototype.onBuild = function () {};
    const canvas = document.getElementById("scene");
    const renderer = new T3.WebGLRenderer({ canvas: canvas, antialias: true });
    renderer.xr.enabled = true;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const scene = new T3.Scene();
    scene.background = new T3.Color(0x0e1a24);
    const camera = new T3.PerspectiveCamera(55, 1, 1, 30000);
    // local East/Up/South frame at the hub, same construction as the river
    // 3D pages (ht-tiles3d v1): tiles are placed by an ECEF->local matrix,
    // with the up translation lowered by the site elevation + geoid height
    // so the ground sits at local y = 0.
    function ecef(lat, lon, h) {
      const p = lat * Math.PI / 180, l = lon * Math.PI / 180, a = 6378137,
        e2 = 6.69437999014e-3, n = a / Math.sqrt(1 - e2 * Math.sin(p) ** 2);
      return [(n + h) * Math.cos(p) * Math.cos(l), (n + h) * Math.cos(p) * Math.sin(l),
              (n * (1 - e2) + h) * Math.sin(p)];
    }
    function localFrame(lat, lon) {
      const p = lat * Math.PI / 180, l = lon * Math.PI / 180, o = ecef(lat, lon, 0);
      const east = [-Math.sin(l), Math.cos(l), 0],
        up = [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)],
        south = [Math.sin(p) * Math.cos(l), Math.sin(p) * Math.sin(l), -Math.cos(p)];
      const dot = (a, b) => a.reduce((v, x, i) => v + x * b[i], 0);
      const m = [...east, -dot(east, o), ...up, -dot(up, o), ...south, -dot(south, o), 0, 0, 0, 1];
      m[7] -= (ELEV + GEOID);
      return m;
    }
    const tiles = new dep.TilesRenderer(undefined);
    const draco = new dep.DRACOLoader().setDecoderPath("../vendor3d/draco/");
    tiles.registerPlugin(new dep.CesiumIonAuthPlugin({
      apiToken: "__TOKEN__", assetId: 2275207, autoRefreshToken: true,
      useRecommendedSettings: false }));
    tiles.registerPlugin(new dep.GLTFExtensionsPlugin({ dracoLoader: draco }));
    tiles.registerPlugin(new dep.TileCompressionPlugin({
      disableMipmaps: true, compressIndex: true, compressPosition: false,
      compressUvs: false, compressNormals: false }));
    tiles.errorTarget = 16;
    tiles.lruCache.minSize = 450; tiles.lruCache.maxSize = 1600;
    tiles.lruCache.minBytesSize = 256 * 1024 * 1024;
    tiles.lruCache.maxBytesSize = 512 * 1024 * 1024;
    tiles.group.matrixAutoUpdate = false;
    tiles.group.matrix.set(...localFrame(LAT, LON));
    tiles.group.updateWorldMatrix(true, false);
    scene.add(tiles.group);
    // WebXR, as on the river 3D pages: the camera rides in a rig so the
    // headset pose is relative to the scene. On desktop the rig sits at
    // the origin, so the orbit controls below drive the camera exactly
    // as before; in a headset the visitor stands at the hub (see the
    // session handlers further down).
    const rig = new T3.Group();
    scene.add(rig);
    rig.add(camera);
    // hub marker (basic materials: the tile scene is unlit)
    const gold = new T3.MeshBasicMaterial({ color: 0xffd24d });
    const pole = new T3.Mesh(new T3.CylinderGeometry(2.5, 2.5, 150, 10), gold);
    pole.position.set(0, 75, 0); scene.add(pole);
    const ball = new T3.Mesh(new T3.SphereGeometry(13, 20, 14), gold);
    ball.position.set(0, 158, 0); scene.add(ball);
    const ring = new T3.Mesh(new T3.RingGeometry(38, 46, 48),
      new T3.MeshBasicMaterial({ color: 0xffd24d, side: T3.DoubleSide }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(0, 2, 0); scene.add(ring);
    // simple orbit controls (drag / wheel / touch), target at the hub
    let az = 0.7, el = 0.9, dist = 2100;
    const target = new T3.Vector3(0, 0, 0);
    function placeCam() {
      camera.position.set(
        target.x + dist * Math.cos(el) * Math.sin(az),
        target.y + dist * Math.sin(el),
        target.z + dist * Math.cos(el) * Math.cos(az));
      camera.lookAt(target);
    }
    let drag = null;
    canvas.addEventListener("pointerdown", e => {
      drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", e => {
      if (!drag) return;
      az -= (e.clientX - drag[0]) * 0.005;
      el = Math.min(1.45, Math.max(0.12, el + (e.clientY - drag[1]) * 0.005));
      drag = [e.clientX, e.clientY]; placeCam(); });
    canvas.addEventListener("pointerup", () => { drag = null; });
    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      dist = Math.min(7000, Math.max(120, dist * (e.deltaY > 0 ? 1.12 : 0.89)));
      placeCam(); }, { passive: false });
    let models = 0, errors = 0;
    setTimeout(() => {
      if (!models) stat.textContent = "3D tiles have not arrived yet — they stream live from " +
        "Cesium ion / Google Photorealistic 3D Tiles; if this persists, check the connection " +
        "and keep the page open. The region page panels are unaffected.";
    }, 25000);
    tiles.addEventListener("load-model", () => {
      models++;
      stat.textContent = "3D tiles streaming · " + models + " tile models loaded" +
        (errors ? " · " + errors + " errors" : "") + " · drag to orbit, scroll to zoom";
    });
    tiles.addEventListener("load-error", () => { errors++; });
    const resV = new T3.Vector2(1, 1);
    function resize() {
      renderer.setSize(window.innerWidth, window.innerHeight, false);
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
    }
    window.addEventListener("resize", resize);
    resize(); placeCam();
    // Enter VR: the button appears only when an immersive-vr session is
    // available. On session start the camera drops to eye height near
    // the hub pin; the desktop orbit position is restored on exit.
    const vrbtn = document.getElementById("vrbtn");
    let savedCamPos = null;
    renderer.xr.addEventListener("sessionstart", () => {
      savedCamPos = camera.position.clone();
      camera.position.set(0, 1.7, 55);
    });
    renderer.xr.addEventListener("sessionend", () => {
      if (savedCamPos) camera.position.copy(savedCamPos);
      placeCam();
    });
    if (navigator.xr && navigator.xr.isSessionSupported) {
      navigator.xr.isSessionSupported("immersive-vr").then(ok => {
        if (!ok) return;
        vrbtn.style.display = "block";
        vrbtn.addEventListener("click", async () => {
          try {
            const cur = renderer.xr.getSession();
            if (cur) { cur.end(); return; }
            const session = await navigator.xr.requestSession("immersive-vr",
              { optionalFeatures: ["local-floor", "bounded-floor"] });
            session.addEventListener("end", () => { vrbtn.textContent = "ENTER VR"; });
            await renderer.xr.setSession(session);
            vrbtn.textContent = "EXIT VR";
          } catch (e) {
            stat.textContent = "VR session failed: " +
              String(e && e.message || e).slice(0, 160);
          }
        });
      }).catch(() => {});
    }
    window.__htEnergyTiles = { models: () => models, errors: () => errors };
    renderer.setAnimationLoop(function tick() {
      try {
        tiles.setCamera(camera);
        renderer.getDrawingBufferSize(resV);
        tiles.setResolution(camera, resV);
        tiles.update();
        renderer.render(scene, camera);
      } catch (err) {
        stat.textContent = "3D tiles failed: " + String(err && err.message || err).slice(0, 200);
      }
    });
  } catch (err) {
    stat.textContent = "3D tiles failed to load: " +
      String(err && err.message || err).slice(0, 200) +
      " — the region page panels are unaffected.";
  }
</script>
</body></html>
"""


def scene_page(rid, token):
    g = HUB_GEO[rid]
    name = dict(REGIONS)[rid]
    s = SCENE_PAGE
    s = s.replace("__TOKEN__", token)
    s = s.replace("__SCENE_CSS__", SCENE_CSS)
    s = s.replace("__NAME__", name)
    s = s.replace("__PLACE__", g["place"])
    s = s.replace("__NOTE__", g["note"])
    s = s.replace("__LAT__", f"{g['lat']:.4f}° N")
    s = s.replace("__LON__", f"{abs(g['lon']):.4f}° W")
    s = s.replace("__LATV__", repr(g["lat"]))
    s = s.replace("__LONV__", repr(g["lon"]))
    s = s.replace("__ELEVV__", repr(g["elev"]))
    s = s.replace("__GEOIDV__", repr(g["geoid"]))
    s = s.replace("__RID__", rid)
    switch = "".join(
        f'<button type="button" class="on">{n} · this scene</button>'
        if o == rid else
        f'<a href="{o}-3d.html">{n} 3D terrain</a>'
        for o, n in REGIONS)
    s = s.replace("__SWITCH__", switch)
    return s


def main():
    _reconcile_energy_ws()
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(RIVER, SITE, ignore=shutil.ignore_patterns(".git"))
    en = SITE / "energy"
    en.mkdir()
    shutil.copy(ENERGY_WS / "site" / "data.js", en / "data.js")
    shutil.copy(BUILD / "energy_extra.js", en / "energy_extra.js")
    shutil.copy(BUILD / "energy_region.js", en / "energy_region.js")
    shutil.copy(BUILD / "energy_hub.js", en / "energy_hub.js")
    token = _ion_token()
    for rid, _ in REGIONS:
        (en / f"{rid}.html").write_text(region_page(rid))
        (en / f"{rid}-3d.html").write_text(scene_page(rid, token))
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
