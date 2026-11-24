#!/usr/bin/env python3
"""Generate genuine extras for the merged HydroTwin+Energy site.

Reuses the HydroTwin Energy pipeline's OWN code (pipeline/pipeline.py) and its
cached raw data (offline) to compute, per energy region:
  - full daily price series
  - walk-forward holdout backtest: for every holdout origin, the chosen model's
    +1..+7d predictions (the pipeline's full-roster run: prepare_region +
    roster_region + GCN, with the same validation selection finalize_region
    applies), plus the persistence anchor and the per-horizon residual sigma
  - feature importance: standardized ridge coefficients per horizon
  - feature-group ablation (ridge): holdout MAE with each group dropped
  - correlations of each feature with the next-day signed-log price change
  - drift: train-origin vs holdout-origin feature mean shift (in train stds)
  - headline stats of the price series
Sanity check: the chosen-model holdout metrics read from the roster run are
compared against the pipeline's own regenerated snapshot (site/data.js) and
printed; they must match (same run, same selection). Separately, ridge's
holdout MAEs are compared against the ORIGINAL shipped snapshot bundled in
energy_inputs/ — ridge is deterministic, so those must match exactly even
though the roster grew.
Output: energy_extra.js  (window.HTE_EXTRA = {...})
"""
import importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

import os, shutil
WS = Path(os.environ.get("HTE_WS", Path.home() / "workspace" / "hydrotwin_energy"))
MERGED_ROOT = Path(os.environ.get("MERGED_ROOT", Path(__file__).resolve().parent.parent))


def _reconcile_energy_ws():
    """Fill gaps in the energy workspace (HTE_WS) from inputs bundled in this
    repo under build/energy_inputs/.

    Why: the hydrotwin-energy GitHub repo ships only its pipeline code and a
    root-level snapshot — its data/ raw cache (which pipeline.py reads via
    ROOT/data/raw) and site/data.js (read by this script for the sanity
    check) exist only in the local workspace and were never pushed, so a
    build from repo clones alone (e.g. GitHub Actions) cannot find them.
    Copies of both are bundled here. Only files MISSING in the workspace are
    copied; existing files are never overwritten, so runs against the full
    local workspace behave exactly as before. Must run BEFORE the pipeline
    module is imported (its fetchers fall back to network downloads for any
    raw file they cannot find in the cache).
    """
    bundled = MERGED_ROOT / "build" / "energy_inputs"
    if not bundled.is_dir():
        return
    copied = 0
    src = bundled / "site" / "data.js"
    dst = WS / "site" / "data.js"
    if src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    src_data = bundled / "data"
    if src_data.is_dir():
        for f in sorted(src_data.rglob("*")):
            if f.is_file():
                d = WS / "data" / f.relative_to(src_data)
                if not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, d)
                    copied += 1
    if copied:
        print(f"energy workspace reconciled from bundled inputs: "
              f"{copied} file(s) copied into {WS}", flush=True)


_reconcile_energy_ws()
spec = importlib.util.spec_from_file_location("epipe", WS / "pipeline" / "pipeline.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score

GROUPS = {
    "Price lags & rolling means": ["p_last", "p_lag1", "p_lag7", "p_roll7", "p_roll30"],
    "Natural gas (Henry Hub)": ["gas_lag1", "gas_roll7"],
    "Hydro: flow, soil, snow": ["flow7", "flow_anom", "soil", "soil7", "swe"],
    "Weather (7d precip/temp/snow)": ["precip7", "temp7", "snow7"],
    "Calendar cycles": ["dow_sin", "dow_cos", "doy_sin", "doy_cos"],
}


# --------------------------------------------------------------------------
# Longer-horizon outlooks (weekly / monthly / yearly), market regimes and the
# forecast surface. Everything below is computed from the same region frame F
# the daily models use — no literals, no borrowed numbers.
# --------------------------------------------------------------------------

def _outlook(feat_df, series, horizons, key_of_target, step_fn, extra=None):
    """Delta-target outlook trainer shared by the weekly and monthly scales.

    feat_df: features per origin period (first column block must include
    'lvl', the period's mean price). series: the aggregated price series on
    the same index. Candidates per horizon: ridge on standardized features
    vs period climatology (mean realized target for the same calendar slot,
    fit on training origins only) vs any extra roster candidates passed in
    `extra` ({name: {h: {"val": {date: pred}, "ho": {date: pred},
    "fc": pred}}}) — the Transformer and SARIMA candidates from the
    roster shared with the river twin. Winner picked on a validation slice
    (last 20% of train), refit on the full train, scored once on the
    holdout (last 20% of origins). The issued outlook refits the winner on
    ALL origins.
    Returns (metrics, backtest rows, forecast rows, holdout_start, n_origins).
    """
    base = feat_df.dropna()
    metrics, backtest, forecast = {}, [], []
    # one split date for all horizons and all candidates, set on the
    # feature-complete periods (the extra candidates use the same rule,
    # so their date-keyed predictions always cover the holdout origins)
    split = base.index[int(len(base) * 0.8)]
    holdout_start, n_origins = str(split.date()), 0
    for h in horizons:
        y = series.shift(-h).reindex(base.index)
        ok = y.notna().values
        O, yv = base[ok], y[ok].values
        n = len(O)
        if n < 20:
            metrics[str(h)] = None
            continue
        n_origins = max(n_origins, n)
        tr_idx = np.where(O.index < split)[0]
        ho_idx = np.where(O.index >= split)[0]
        if len(tr_idx) < 16 or len(ho_idx) < 3:
            metrics[str(h)] = None
            continue
        lvl = O["lvl"].values
        delta = P.slog(yv) - P.slog(lvl)
        Xv = O.values.astype(float)
        n_val = max(int(len(tr_idx) * 0.2), 5)
        fit_idx, val_idx = tr_idx[:-n_val], tr_idx[-n_val:]
        clip = (float(np.min(yv[tr_idx])), float(np.max(yv[tr_idx])) * 1.25)

        def to_price(dp, lv):
            return np.clip(P.slog_inv(P.slog(lv) + dp), clip[0], clip[1])

        def clim_table(idxs):
            tab = {}
            for i in idxs:
                tab.setdefault(key_of_target(O.index[i], h), []).append(yv[i])
            return {k: float(np.mean(v)) for k, v in tab.items()}, float(np.mean(yv[idxs]))

        def clim_pred(idxs, tab, overall):
            return np.array([tab.get(key_of_target(O.index[i], h), overall) for i in idxs])

        def ridge_fit(idxs):
            sc = StandardScaler().fit(Xv[idxs])
            return sc, Ridge(alpha=10.0).fit(sc.transform(Xv[idxs]), delta[idxs])

        # validation: ridge vs climatology
        sc_v, m_v = ridge_fit(fit_idx)
        pv_ridge = to_price(m_v.predict(sc_v.transform(Xv[val_idx])), lvl[val_idx])
        tab_v, ov_v = clim_table(fit_idx)
        pv_clim = clim_pred(val_idx, tab_v, ov_v)
        val_mae = {"ridge": float(np.mean(np.abs(yv[val_idx] - pv_ridge))),
                   "climatology": float(np.mean(np.abs(yv[val_idx] - pv_clim)))}
        # extra roster candidates (Transformer / SARIMA): their
        # validation and holdout predictions are keyed by origin date
        extra_pred = {}
        for name, cand in (extra or {}).items():
            c = cand.get(h)
            if not c:
                continue
            vp = [(yv[i], c["val"][O.index[i]]) for i in val_idx
                  if O.index[i] in c["val"]]
            if len(vp) >= 3 and all(O.index[i] in c["ho"] for i in ho_idx):
                val_mae[name] = float(np.mean([abs(a - b) for a, b in vp]))
                extra_pred[name] = (
                    np.array([c["ho"][O.index[i]] for i in ho_idx]),
                    float(c["fc"]))
        chosen = min(val_mae, key=val_mae.get)

        # refit winner on the full training period, score once on holdout
        if chosen == "ridge":
            sc_t, m_t = ridge_fit(tr_idx)
            pred_ho = to_price(m_t.predict(sc_t.transform(Xv[ho_idx])), lvl[ho_idx])
        elif chosen == "climatology":
            tab_t, ov_t = clim_table(tr_idx)
            pred_ho = clim_pred(ho_idx, tab_t, ov_t)
        else:
            pred_ho = extra_pred[chosen][0]
        # NOTE: P.evaluate refuses n < 10 (returns NaN) — right for the daily
        # models, wrong here: the monthly holdout has only 5-7 origins. Score
        # directly and let the printed n carry the caveat.
        def _ev(yt, yp):
            m = np.isfinite(yt) & np.isfinite(yp)
            yt, yp = yt[m], yp[m]
            mae = float(np.mean(np.abs(yt - yp)))
            rmse = float(np.sqrt(np.mean((yt - yp) ** 2)))
            ss = float(np.sum((yt - yt.mean()) ** 2))
            r2 = float(1 - np.sum((yt - yp) ** 2) / ss) if ss > 0 else float("nan")
            return dict(n=int(len(yt)), mae=mae, rmse=rmse, r2=r2)
        ev = _ev(yv[ho_idx], pred_ho)
        pers = _ev(yv[ho_idx], lvl[ho_idx])
        resid = yv[ho_idx] - pred_ho
        resid = resid[np.isfinite(resid)]
        sigma = float(np.std(resid)) if len(resid) else float("nan")
        skill = (1 - ev["mae"] / pers["mae"]) * 100 if pers["mae"] else float("nan")
        metrics[str(h)] = dict(
            chosen=chosen, val_mae={k: round(v, 2) for k, v in val_mae.items()},
            mae=round(ev["mae"], 2), rmse=round(ev["rmse"], 2), r2=round(ev["r2"], 3),
            n=ev["n"], pers_mae=round(pers["mae"], 2), skill_pct=round(skill, 1),
            resid_sigma=round(sigma, 2))
        for j, hp in enumerate(ho_idx):
            backtest.append(dict(origin=str(O.index[hp].date()), h=h,
                                 pred=round(float(pred_ho[j]), 2),
                                 actual=round(float(yv[hp]), 2)))
        # issued outlook: winner refit on ALL origins, forecast from the last
        # feature-complete period (its own target is not yet observed)
        last_i = len(base) - 1
        last_row = base.values.astype(float)[last_i]
        last_lvl = float(base["lvl"].iloc[last_i])
        all_idx = np.arange(n)
        if chosen == "ridge":
            sc_a, m_a = ridge_fit(all_idx)
            dp = float(m_a.predict(sc_a.transform(last_row.reshape(1, -1)))[0])
            pred_f = float(to_price(np.array([dp]), np.array([last_lvl]))[0])
        elif chosen == "climatology":
            tab_a, ov_a = clim_table(all_idx)
            pred_f = float(tab_a.get(key_of_target(base.index[last_i], h), ov_a))
        else:
            pred_f = extra_pred[chosen][1]
        forecast.append(dict(date=str(step_fn(base.index[last_i], h).date()), h=h,
                             pred=round(pred_f, 2),
                             lo=round(pred_f - 1.2816 * sigma, 2),
                             hi=round(pred_f + 1.2816 * sigma, 2)))
    return metrics, backtest, forecast, holdout_start, n_origins


def _outlook_transformer(feat_df, series, horizons, lookback):
    """Extra outlook candidate: a small Transformer over windows of the
    aggregated feature frame (the daily roster's window protocol at the
    weekly/monthly scale). Direct multi-horizon signed-log delta targets
    with a masked loss; phase 1 fits on the fit slice with early stopping
    on validation price MAE, phase 2 refits on the training periods for
    the best epoch count. Returns {h: {"val": {date: pred}, "ho":
    {date: pred}, "fc": pred}} or None when the scale has too few
    periods to window."""
    import torch
    import torch.nn as nn
    torch.set_num_threads(3)
    torch.manual_seed(7)
    np.random.seed(7)
    base = feat_df.dropna()
    n = len(base)
    if n < lookback + 24:
        return None
    Xall = base.values.astype(float)
    lvl = base["lvl"].values
    dates = base.index
    W = np.stack([Xall[i - lookback + 1: i + 1]
                  for i in range(lookback - 1, n)])
    rows = np.arange(lookback - 1, n)
    Tm = np.stack([P.slog(series.shift(-h).reindex(dates).values) -
                   P.slog(lvl) for h in horizons], axis=1)[lookback - 1:]
    split_i = int(n * 0.8)
    tr_w = np.where((rows < split_i) & np.isfinite(Tm).any(axis=1))[0]
    if len(tr_w) < 16:
        return None
    n_val = max(int(len(tr_w) * 0.2), 3)
    fit_w, val_w = tr_w[:-n_val], tr_w[-n_val:]
    ho_w = np.where(rows >= split_i)[0]
    clips = {}
    for j, h in enumerate(horizons):
        ytr = series.shift(-h).reindex(dates).values[rows[tr_w]]
        ytr = ytr[np.isfinite(ytr)]
        clips[h] = (float(np.min(ytr)), float(np.max(ytr)) * 1.25)
    sc = StandardScaler().fit(W[fit_w].reshape(-1, Xall.shape[1]))

    def tf(idx):
        return torch.tensor(
            sc.transform(W[idx].reshape(-1, Xall.shape[1])).reshape(
                len(idx), lookback, -1), dtype=torch.float32)

    DM = 32

    def posenc(L, DM):
        pe = torch.zeros(L, DM)
        posv = torch.arange(L).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, DM, 2).float() *
                        (-np.log(10000.0) / DM))
        pe[:, 0::2] = torch.sin(posv * div)
        pe[:, 1::2] = torch.cos(posv * div)
        return pe

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.proj = nn.Linear(Xall.shape[1], DM)
            enc = nn.TransformerEncoderLayer(d_model=DM, nhead=4,
                                             dim_feedforward=64, dropout=0.1,
                                             batch_first=True)
            self.body = nn.TransformerEncoder(enc, num_layers=2)
            self.register_buffer("pe", posenc(64, DM))
            self.head = nn.Linear(DM, len(horizons))

        def forward(self, x):
            z = self.body(self.proj(x) + self.pe[:x.shape[1]])
            return self.head(z.mean(dim=1))

    def to_price(dp, w_idx):
        out = np.empty((len(w_idx), len(horizons)))
        for j, h in enumerate(horizons):
            out[:, j] = np.clip(P.slog_inv(P.slog(lvl[rows[w_idx]]) +
                                           dp[:, j]),
                                clips[h][0], clips[h][1])
        return out

    def val_mae_of(model):
        model.eval()
        with torch.no_grad():
            dp = model(tf(val_w)).numpy()
        pv = to_price(dp, val_w)
        yv = np.stack([series.shift(-h).reindex(dates).values[rows[val_w]]
                       for h in horizons], axis=1)
        m = np.isfinite(yv)
        return float(np.mean(np.abs(yv[m] - pv[m]))) if m.any() else np.inf

    def train_on(idx, epochs):
        model = Net()
        opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
        XP = tf(idx)
        yP = torch.tensor(np.nan_to_num(Tm[idx]), dtype=torch.float32)
        mP = torch.tensor(np.isfinite(Tm[idx]), dtype=torch.float32)
        bs = 32
        for _ in range(epochs):
            model.train()
            perm = torch.randperm(len(idx))
            for i in range(0, len(idx), bs):
                ii = perm[i:i + bs]
                opt.zero_grad()
                out = model(XP[ii])
                loss = ((out - yP[ii]) ** 2 * mP[ii]).sum() / \
                    mP[ii].sum().clamp(min=1.0)
                loss.backward()
                opt.step()
        return model

    model = Net()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    Xf = tf(fit_w)
    yf = torch.tensor(np.nan_to_num(Tm[fit_w]), dtype=torch.float32)
    mf = torch.tensor(np.isfinite(Tm[fit_w]), dtype=torch.float32)
    best, best_ep, wait = np.inf, 0, 0
    for ep in range(40):
        model.train()
        perm = torch.randperm(len(fit_w))
        for i in range(0, len(fit_w), 32):
            ii = perm[i:i + 32]
            opt.zero_grad()
            out = model(Xf[ii])
            loss = ((out - yf[ii]) ** 2 * mf[ii]).sum() / \
                mf[ii].sum().clamp(min=1.0)
            loss.backward()
            opt.step()
        vm = val_mae_of(model)
        if vm < best - 1e-3:
            best, best_ep, wait = vm, ep + 1, 0
        else:
            wait += 1
            if wait >= 8:
                break
    model.eval()
    with torch.no_grad():
        pv_val = to_price(model(tf(val_w)).numpy(), val_w)
    model2 = train_on(tr_w, max(best_ep, 5))
    model2.eval()
    with torch.no_grad():
        pv_ho = to_price(model2(tf(ho_w)).numpy(), ho_w)
        pv_fc = to_price(model2(tf(np.array([len(W) - 1]))).numpy(),
                         np.array([len(W) - 1]))
    out = {}
    for j, h in enumerate(horizons):
        out[h] = {
            "val": {dates[rows[w]]: float(pv_val[k, j])
                    for k, w in enumerate(val_w)},
            "ho": {dates[rows[w]]: float(pv_ho[k, j])
                   for k, w in enumerate(ho_w)},
            "fc": float(pv_fc[0, j])}
    return out


def _outlook_sarima(series, feat_df, horizons, seasonal, freq):
    """Extra outlook candidate: SARIMA (order (1,1,1), the stated
    seasonal order) at the aggregated scale. Fit on the fit slice and
    rolled forward with frozen parameters (refit=False) over the
    validation origins; refit on the training periods and rolled through
    the holdout; the issued outlook is the forecast from the last
    feature-complete period of that same roll — the daily roster's
    operational ARIMA protocol at this scale. A degenerate-fit guard
    rejects absurd forecasts."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    base = feat_df.dropna()
    full = series.reindex(pd.date_range(series.index.min(),
                                        series.index.max(), freq=freq))
    n = len(base)
    split = base.index[int(n * 0.8)]
    tr_dates = base.index[base.index < split]
    n_val = max(int(len(tr_dates) * 0.2), 3)
    fit_dates, val_dates = tr_dates[:-n_val], tr_dates[-n_val:]
    ho_dates = base.index[base.index >= split]
    maxh = max(horizons)
    pmax = float(np.nanmax(full.values))

    def roll(fit_end, want):
        tr = full.loc[full.index <= fit_end]
        r = SARIMAX(tr, order=(1, 1, 1), seasonal_order=seasonal).fit(disp=False)
        fc_at, cur = {}, r
        days = full.index[(full.index > fit_end) &
                          (full.index <= base.index[-1])]
        for d in days:
            cur = cur.apply(np.array([full.loc[d]]), refit=False)
            if d in want:
                fc = np.asarray(cur.forecast(steps=maxh), dtype=float)
                if (not np.isfinite(fc).all()
                        or np.max(np.abs(fc)) > 10 * pmax):
                    raise RuntimeError("degenerate SARIMA forecast")
                fc_at[d] = fc
        return fc_at, cur

    fc_val, _ = roll(fit_dates[-1], set(val_dates))
    fc_ho, cur = roll(tr_dates[-1], set(ho_dates))
    fc_last = np.asarray(cur.forecast(steps=maxh), dtype=float)
    if not np.isfinite(fc_last).all() or np.max(np.abs(fc_last)) > 10 * pmax:
        raise RuntimeError("degenerate SARIMA outlook")
    out = {}
    for h in horizons:
        out[h] = {
            "val": {d: float(fc_val[d][h - 1]) for d in val_dates
                    if d in fc_val},
            "ho": {d: float(fc_ho[d][h - 1]) for d in ho_dates
                   if d in fc_ho},
            "fc": float(fc_last[h - 1])}
    return out



def long_outlooks(F):
    """Weekly + monthly outlook models and the yearly climatology outlook."""
    price = F["price"]
    out = {}
    # ---- weekly: weeks ending Sunday, labelled by their starting Monday ----
    cnt = price.resample("W-SUN").count()
    wk = price.resample("W-SUN").mean()[cnt >= 3]
    wk.index = wk.index - pd.Timedelta(days=6)
    def _wk(s):
        r = s.resample("W-SUN").mean()
        r.index = r.index - pd.Timedelta(days=6)
        return r.reindex(wk.index)
    W = pd.DataFrame(index=wk.index)
    W["lvl"] = wk
    W["w_lag1"] = wk.shift(1); W["w_lag2"] = wk.shift(2); W["w_lag3"] = wk.shift(3)
    W["w_roll4"] = wk.rolling(4).mean(); W["w_roll13"] = wk.rolling(13).mean()
    gw = _wk(F["gas"]); W["gas_w"] = gw; W["gas_roll4"] = gw.rolling(4).mean()
    fw = _wk(F["flow"]); W["flow_w"] = fw; W["flow_roll13"] = fw.rolling(13).mean()
    W["temp_w"] = _wk(F["temp"])
    woy = wk.index.isocalendar().week.astype(float).values
    W["woy_sin"] = np.sin(2 * np.pi * woy / 52.18); W["woy_cos"] = np.cos(2 * np.pi * woy / 52.18)
    # extra roster candidates for the weekly scale (Transformer + SARIMA
    # from the roster shared with the river twin), validation-selected
    # alongside ridge and climatology inside _outlook
    wk_extra = {}
    try:
        cand = _outlook_transformer(W, wk, list(range(1, 13)), 26)
        if cand:
            wk_extra["transformer"] = cand
    except Exception as e:
        print(f"  weekly transformer candidate failed: {e}")
    try:
        wk_extra["sarima"] = _outlook_sarima(wk, W, list(range(1, 13)),
                                             (1, 0, 1, 52), "7D")
    except Exception as e:
        print(f"  weekly sarima candidate failed: {e}")
    wm, wbt, wfc, who, wn = _outlook(
        W, wk, list(range(1, 13)),
        key_of_target=lambda d, h: int((d + pd.Timedelta(days=7 * h)).isocalendar().week),
        step_fn=lambda d, h: d + pd.Timedelta(days=7 * h),
        extra=wk_extra)
    out["weekly"] = dict(
        series=[dict(d=str(i.date()), v=round(float(v), 2)) for i, v in wk.items()],
        metrics=wm, backtest=wbt, forecast=wfc, holdout_start=who, n_origins=wn,
        n_periods=int(len(wk)))
    # ---- monthly ----
    mcnt = price.resample("MS").count()
    mo = price.resample("MS").mean()[mcnt >= 15]
    M = pd.DataFrame(index=mo.index)
    M["lvl"] = mo
    M["m_lag1"] = mo.shift(1); M["m_lag2"] = mo.shift(2); M["m_lag3"] = mo.shift(3)
    M["m_lag6"] = mo.shift(6); M["m_lag12"] = mo.shift(12)
    M["m_roll3"] = mo.rolling(3).mean(); M["m_roll12"] = mo.rolling(12).mean()
    gm = F["gas"].resample("MS").mean().reindex(mo.index)
    M["gas_m"] = gm; M["gas_roll3"] = gm.rolling(3).mean()
    mon = mo.index.month.values.astype(float)
    M["mon_sin"] = np.sin(2 * np.pi * mon / 12); M["mon_cos"] = np.cos(2 * np.pi * mon / 12)
    mo_extra = {}
    try:
        cand = _outlook_transformer(M, mo, list(range(1, 7)), 12)
        if cand:
            mo_extra["transformer"] = cand
    except Exception as e:
        print(f"  monthly transformer candidate failed: {e}")
    try:
        mo_extra["sarima"] = _outlook_sarima(mo, M, list(range(1, 7)),
                                             (1, 0, 1, 12), "MS")
    except Exception as e:
        print(f"  monthly sarima candidate failed: {e}")
    mm, mbt, mfc, mho, mn = _outlook(
        M, mo, list(range(1, 7)),
        key_of_target=lambda d, h: int((d + pd.DateOffset(months=h)).month),
        step_fn=lambda d, h: d + pd.DateOffset(months=h),
        extra=mo_extra)
    out["monthly"] = dict(
        series=[dict(d=str(i.date()), v=round(float(v), 2)) for i, v in mo.items()],
        metrics=mm, backtest=mbt, forecast=mfc, holdout_start=mho, n_origins=mn,
        n_periods=int(len(mo)))
    # ---- yearly: monthly climatology outlook (no fitted accuracy claim) ----
    ps = price.dropna()
    ym = ps.groupby([ps.index.year, ps.index.month]).agg(["mean", "count"])
    ym = ym[ym["count"] >= 15]["mean"]
    years = sorted({int(y) for y, _ in ym.index})
    clim = {}
    for mth in range(1, 13):
        vals = [float(ym[(y, mth)]) for y in years if (y, mth) in ym.index]
        if not vals:
            clim[str(mth)] = None
            continue
        a = np.array(vals)
        clim[str(mth)] = dict(mean=round(float(a.mean()), 2), median=round(float(np.median(a)), 2),
                              p25=round(float(np.percentile(a, 25)), 2),
                              p75=round(float(np.percentile(a, 75)), 2),
                              std=round(float(a.std()), 2), n_years=len(vals),
                              values=[round(v, 2) for v in vals],
                              years=[y for y in years if (y, mth) in ym.index])
    # leave-one-year-out check of the climatology itself: predict each year
    # from the climatology of the years before it (genuine, folds stated)
    folds, errs = [], []
    for y in years:
        prev = [yy for yy in years if yy < y]
        months_y = [mth for mth in range(1, 13) if (y, mth) in ym.index]
        if not prev or len(months_y) < 6:
            continue
        fe = []
        for mth in months_y:
            pv = [float(ym[(yy, mth)]) for yy in prev if (yy, mth) in ym.index]
            if pv:
                fe.append(abs(float(ym[(y, mth)]) - float(np.mean(pv))))
        if fe:
            folds.append(dict(year=int(y), n_months=len(fe), mae=round(float(np.mean(fe)), 2)))
            errs.extend(fe)
    last = ps.index.max()
    start = (pd.Timestamp(last.year, last.month, 1) + pd.DateOffset(months=1))
    outlook = []
    for k in range(12):
        t = start + pd.DateOffset(months=k)
        c = clim[str(t.month)]
        outlook.append(dict(date=str(t.date()), month=int(t.month),
                            mean=c["mean"] if c else None,
                            p25=c["p25"] if c else None, p75=c["p75"] if c else None))
    out["yearly"] = dict(
        clim=clim, outlook=outlook, folds=folds,
        loyo_mae=round(float(np.mean(errs)), 2) if errs else None,
        years=years, first=str(ps.index[0].date()), last=str(ps.index[-1].date()))
    # yearly SARIMA line for comparison with the climatology (the river
    # abstract's yearly winner is SARIMA): fit SARIMA (1,1,1)(1,0,1,12) on
    # the monthly mean series; check it with expanding-window folds (fit
    # on the months before January of the target year, forecast that
    # year); the issued line is a 12-month forecast from a fit on all
    # months. The monthly series here has 39-46 points (3-4 years), so
    # folds are only possible for the last few years — stated on the page.
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        mfull = mo.reindex(pd.date_range(mo.index.min(), mo.index.max(),
                                         freq="MS"))
        sfolds, serrs = [], []
        for y in years[-3:]:
            tr_s = mfull[mfull.index < pd.Timestamp(y, 1, 1)]
            te_s = mfull[(mfull.index >= pd.Timestamp(y, 1, 1)) &
                         (mfull.index < pd.Timestamp(y + 1, 1, 1))]
            if len(tr_s.dropna()) < 24 or len(te_s.dropna()) < 6:
                continue
            r_y = SARIMAX(tr_s, order=(1, 1, 1),
                          seasonal_order=(1, 0, 1, 12)).fit(disp=False)
            fc_y = np.asarray(r_y.forecast(12), dtype=float)
            fe = [abs(float(a) - float(b)) for a, b in
                  zip(te_s.values, fc_y[:len(te_s)])
                  if np.isfinite(a) and np.isfinite(b)]
            if fe:
                sfolds.append(dict(year=int(y), n_months=len(fe),
                                   mae=round(float(np.mean(fe)), 2)))
                serrs.extend(fe)
        r_all = SARIMAX(mfull, order=(1, 1, 1),
                        seasonal_order=(1, 0, 1, 12)).fit(disp=False)
        fc12 = np.asarray(r_all.forecast(12), dtype=float)
        if (not np.isfinite(fc12).all()
                or np.max(np.abs(fc12)) > 10 * float(np.nanmax(mfull.values))):
            raise RuntimeError("degenerate yearly SARIMA forecast")
        s_out = []
        for k in range(12):
            t = start + pd.DateOffset(months=k)
            s_out.append(dict(date=str(t.date()), month=int(t.month),
                              pred=round(float(fc12[k]), 2)))
        out["yearly"]["sarima"] = dict(
            outlook=s_out, folds=sfolds,
            loyo_mae=round(float(np.mean(serrs)), 2) if serrs else None)
    except Exception as e:
        out["yearly"]["sarima"] = dict(error=str(e)[:150])
    return out


def market_regimes(F, X, origins):
    """Cluster DAYS (forecast origins) into market regimes for one region:
    k-means on the standardized daily feature vectors plus a 7-day price
    volatility term. k in {3,4,5} chosen by silhouette score. Regime names
    are assigned by rule from each cluster's computed price statistics."""
    price = F["price"]
    # volatility of the signed-log price change; the price is forward-filled
    # (limit 4) first, the same convention make_features uses, because the ICE
    # peak indices have no weekend values and a raw rolling std would be
    # almost everywhere NaN.
    vol7 = P.slog(price.ffill(limit=4)).diff().rolling(7, min_periods=4).std()
    V = origins.copy()
    V["vol7"] = vol7.reindex(origins.index)
    V = V.dropna()
    feats = list(V.columns)
    Z = StandardScaler().fit_transform(V.values.astype(float))
    n = len(V)
    rng = np.random.RandomState(7)
    sample = rng.choice(n, size=min(800, n), replace=False)
    sil, models = {}, {}
    for k in (3, 4, 5):
        km = KMeans(n_clusters=k, n_init=10, random_state=7).fit(Z)
        sil[k] = float(silhouette_score(Z[sample], km.labels_[sample]))
        models[k] = km
    k_best = max(sil, key=sil.get)
    labels = models[k_best].labels_
    pca = PCA(n_components=2, random_state=7).fit(Z)
    coords = pca.transform(Z)
    pl = V["p_last"].values
    overall_std = float(np.std(pl))
    stats = []
    for c in range(k_best):
        m = labels == c
        stats.append(dict(
            id=int(c), n=int(m.sum()), share_pct=round(float(m.mean() * 100), 1),
            mean_price=round(float(pl[m].mean()), 2), median_price=round(float(np.median(pl[m])), 2),
            std_price=round(float(pl[m].std()), 2),
            mean_gas=round(float(V["gas_lag1"].values[m].mean()), 2),
            mean_temp7=round(float(V["temp7"].values[m].mean()), 1),
            mean_flow_anom=round(float(V["flow_anom"].values[m].mean()), 3),
            mean_precip7=round(float(V["precip7"].values[m].mean()), 1),
            mean_vol7=round(float(V["vol7"].values[m].mean()), 3)))
    order = sorted(stats, key=lambda s: s["mean_price"])
    names = {}
    names[order[0]["id"]] = "Low-price days"
    top = order[-1]
    names[top["id"]] = ("Spike days — high and volatile"
                        if top["std_price"] >= 1.4 * overall_std else "High-price days")
    middles = order[1:-1]
    if len(middles) == 1:
        names[middles[0]["id"]] = "Typical days"
    elif len(middles) >= 2:
        names[middles[-1]["id"]] = "Elevated-price days"
        rest = middles[:-1]
        vol = max(rest, key=lambda s: s["std_price"])
        for s in rest:
            names[s["id"]] = ("Volatile days" if s is vol and s["std_price"] >= 1.2 * overall_std
                              else "Typical days")
    # disambiguate repeated generic labels by the clusters' mean temperature
    seen = {}
    for s in stats:
        seen.setdefault(names[s["id"]], []).append(s)
    for lab, grp in seen.items():
        if len(grp) > 1:
            grp_sorted = sorted(grp, key=lambda s: s["mean_temp7"])
            grp_sorted[0]["label"] = lab + " — cold season"
            grp_sorted[-1]["label"] = lab + " — warm season"
            for s in grp_sorted[1:-1]:
                s["label"] = lab
    for s in stats:
        s.setdefault("label", names[s["id"]])
    pick = rng.choice(n, size=min(550, n), replace=False)
    return dict(
        k=int(k_best), silhouette={str(k): round(v, 3) for k, v in sil.items()},
        features=feats, regimes=stats,
        pca=dict(explained_pct=[round(float(v * 100), 1) for v in pca.explained_variance_ratio_],
                 points=[dict(x=round(float(coords[i, 0]), 2), y=round(float(coords[i, 1]), 2),
                              c=int(labels[i])) for i in sorted(pick.tolist())]),
        timeline=dict(dates=[str(d.date()) for d in V.index],
                      labels=[int(v) for v in labels]))


def forecast_surface(backtest):
    """Mean forecast / actual / |error| by origin calendar month x horizon,
    from the daily holdout backtest rows."""
    grid = {}
    for r in backtest:
        grid.setdefault((int(r["origin"][5:7]), r["h"]), []).append(r)
    surf = dict(months=list(range(1, 13)), horizons=list(range(1, 8)),
                pred=[], actual=[], mae=[], n=[])
    for mth in range(1, 13):
        prow, arow, erow, nrow = [], [], [], []
        for h in range(1, 8):
            rows = grid.get((mth, h), [])
            if rows:
                prow.append(round(float(np.mean([r["pred"] for r in rows])), 2))
                arow.append(round(float(np.mean([r["actual"] for r in rows])), 2))
                erow.append(round(float(np.mean([abs(r["actual"] - r["pred"]) for r in rows])), 2))
                nrow.append(len(rows))
            else:
                prow.append(None); arow.append(None); erow.append(None); nrow.append(0)
        surf["pred"].append(prow); surf["actual"].append(arow)
        surf["mae"].append(erow); surf["n"].append(nrow)
    return surf


def region_extras(rid, cfg, F, D):
    """Diagnostics for one region. D is the pipeline's prepared + rostered
    region dict (prepare_region + roster_region + GCN + finalize inputs):
    the daily backtest and the sanity-check metrics below are read from
    the roster's own chosen-model holdout predictions, so this generator
    and the pipeline can never disagree about the daily models. The
    ridge-only diagnostics (feature importance, group ablation) still
    refit ridge here, exactly as before."""
    origins = D["origins"]
    feats = D["feats"]
    split = D["split"]
    train_idx = D["train_idx"]
    hold_idx = D["hold_idx"]
    price = F["price"]
    Xv = origins.values
    p_last_all = origins["p_last"].values
    pos = D["pos"]
    out = {"holdout_start": str(split.date()), "features": feats}

    # full price series + headline stats
    ps = price.dropna()
    out["series"] = [dict(d=str(i.date()), v=round(float(v), 2)) for i, v in ps.items()]
    out["stats"] = dict(mean=round(float(ps.mean()), 2), median=round(float(ps.median()), 2),
                        max=round(float(ps.max()), 2), min=round(float(ps.min()), 2),
                        last=round(float(ps.iloc[-1]), 2), last_date=str(ps.index[-1].date()),
                        n=int(len(ps)), first_date=str(ps.index[0].date()))

    backtest = []          # per holdout origin
    featimp = {}           # per horizon: ridge standardized coefs
    check = {}             # recomputed metrics for sanity check
    ablation = {}          # per group (ridge, h in {1,7})
    for h in range(1, 8):
        y_all = price.shift(-h).reindex(origins.index)
        yv = y_all.values
        delta_all = P.slog(yv) - P.slog(p_last_all)
        hd = D["horizons"][h]
        tr_pos, ho_pos = hd["tr_pos"], hd["ho_pos"]
        clip = hd["clip"]

        def to_price(d_pred, pl, clip=clip):
            return np.clip(P.slog_inv(P.slog(pl) + d_pred), clip[0], clip[1])

        # chosen model + holdout predictions come straight from the
        # pipeline's full-roster run (validation selection over all
        # roster models, same rule as finalize_region)
        entries = D["roster"][h]
        val_mae = {k: e["val_mae"] for k, e in entries.items()
                   if e.get("status") == "ok" and e.get("val_mae") is not None}
        chosen = min(val_mae, key=val_mae.get)
        pred_ho = entries[chosen]["pred"]
        ev = P.evaluate(yv[ho_pos], pred_ho)
        resid = yv[ho_pos] - pred_ho
        resid = resid[np.isfinite(resid)]
        sigma = float(np.std(resid)) if len(resid) else float("nan")
        ridge_ev = (P.evaluate(yv[ho_pos], entries["ridge"]["pred"])
                    if entries.get("ridge", {}).get("status") == "ok"
                    else None)
        check[str(h)] = dict(chosen=chosen, mae=round(ev["mae"], 2), rmse=round(ev["rmse"], 2),
                             r2=round(ev["r2"], 3), n=ev["n"], resid_sigma=round(sigma, 2),
                             ridge_mae=round(ridge_ev["mae"], 2) if ridge_ev else None)

        # ridge coefficients (train fit) for feature importance
        sc_tr = StandardScaler().fit(Xv[tr_pos])
        rmodel = Ridge(alpha=10.0).fit(sc_tr.transform(Xv[tr_pos]), delta_all[tr_pos])
        featimp[str(h)] = [round(float(c), 4) for c in rmodel.coef_]

        # store per-origin predictions for this horizon
        for j, hp in enumerate(ho_pos):
            d = origins.index[hp]
            rec = dict(origin=str(d.date()), p_last=round(float(p_last_all[hp]), 2),
                       actual=round(float(yv[hp]), 2), pred=round(float(pred_ho[j]), 2),
                       sigma=round(sigma, 2), h=h)
            backtest.append(rec)

        # ablation with ridge at h in {1, 7}: drop each group, refit, holdout MAE
        if h in (1, 7):
            base = P.evaluate(yv[ho_pos], to_price(rmodel.predict(sc_tr.transform(Xv[ho_pos])),
                                                   p_last_all[ho_pos]))
            for gname, gcols in GROUPS.items():
                keep = [i for i, f in enumerate(feats) if f not in gcols]
                sc_a = StandardScaler().fit(Xv[tr_pos][:, keep])
                m_a = Ridge(alpha=10.0).fit(sc_a.transform(Xv[tr_pos][:, keep]), delta_all[tr_pos])
                pa = to_price(m_a.predict(sc_a.transform(Xv[ho_pos][:, keep])), p_last_all[ho_pos])
                ea = P.evaluate(yv[ho_pos], pa)
                ablation.setdefault(gname, {})[str(h)] = dict(
                    mae=round(ea["mae"], 2), base_mae=round(base["mae"], 2),
                    delta_mae=round(ea["mae"] - base["mae"], 2))
    out["backtest"] = backtest
    out["featimp_ridge"] = featimp
    out["check_metrics"] = check
    out["ablation"] = ablation

    # correlations: features vs next-day signed-log price change (all origins)
    y1 = price.shift(-1).reindex(origins.index).values
    d1 = P.slog(y1) - P.slog(p_last_all)
    corr = {}
    for i, f in enumerate(feats):
        x = Xv[:, i]
        m = np.isfinite(x) & np.isfinite(d1)
        corr[f] = round(float(np.corrcoef(x[m], d1[m])[0, 1]), 3) if m.sum() > 30 else None
    out["corr_next_day"] = corr

    # drift: train vs holdout feature means (in train std units)
    tr_rows = np.array([pos[d] for d in train_idx])
    ho_rows = np.array([pos[d] for d in hold_idx])
    drift = {}
    for i, f in enumerate(feats):
        xt, xh = Xv[tr_rows, i], Xv[ho_rows, i]
        sd = float(np.nanstd(xt)) or 1.0
        drift[f] = dict(train_mean=round(float(np.nanmean(xt)), 3),
                        holdout_mean=round(float(np.nanmean(xh)), 3),
                        shift_std=round(float((np.nanmean(xh) - np.nanmean(xt)) / sd), 3))
    out["drift"] = drift

    # origin counts (MLOps panel)
    out["n_origins"] = int(len(origins))
    out["train_origins"] = int(len(train_idx))
    out["holdout_origins"] = int(len(hold_idx))

    # longer-horizon outlooks, day regimes, forecast surface
    out["outlooks"] = long_outlooks(F)
    out["regimes"] = market_regimes(F, P.make_features(F, bool(cfg["snotel"])), origins)
    out["surface"] = forecast_surface(backtest)
    return out


def main():
    ice = P.fetch_ice_prices()
    gas = P.fetch_henry_hub()
    shipped = json.loads((WS / "site" / "data.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    shipped_metrics = {r["id"]: r["metrics"] for r in shipped["regions"]}
    # the ORIGINAL shipped snapshot bundled with the merged build inputs:
    # ridge is deterministic, so its holdout MAEs must reproduce the
    # original twin's numbers exactly even after the roster grew — that
    # is the regression anchor for the whole extension.
    orig_metrics = None
    orig_path = Path(__file__).parent / "energy_inputs" / "site" / "data.js"
    if orig_path.exists() and orig_path.resolve() != (WS / "site" / "data.js").resolve():
        o = json.loads(orig_path.read_text().split("=", 1)[1].rstrip().rstrip(";"))
        # the strict comparison is only meaningful on the SAME data
        # window; once the live window rolls past the bundled snapshot's,
        # ridge is expected to differ and the check is skipped, not failed
        if o["meta"]["data_window"] == shipped["meta"]["data_window"]:
            orig_metrics = {r["id"]: r["metrics"] for r in o["regions"]}
        else:
            print(f"  ridge vs original snapshot: SKIP — data window moved "
                  f"({o['meta']['data_window']} -> {shipped['meta']['data_window']})")
    extra = {}
    sanity = {}
    # pass 1: prepare + run the full roster per region (the GCN needs all
    # three regions prepared before it can run)
    prepared = {}
    for rid, cfg in P.REGIONS.items():
        print(f"=== {rid}: prepare + roster ===", flush=True)
        F, _names = P.build_region_frame(cfg, ice, gas)
        D = P.prepare_region(rid, cfg, F)
        P.roster_region(D)
        prepared[rid] = (cfg, F, D)
    try:
        P.run_gcn({rid: prepared[rid][2] for rid in P.REGIONS})
    except Exception as e:
        print(f"GCN failed ({e}); extras continue without it")
        for rid in P.REGIONS:
            P._fail(prepared[rid][2], ["gcn"], e)
    for rid, cfg in P.REGIONS.items():
        cfg, F, D = prepared[rid]
        print(f"=== {rid}: extras ===", flush=True)
        ex = region_extras(rid, cfg, F, D)
        extra[rid] = ex
        # sanity check vs the pipeline's own snapshot (WS/site/data.js,
        # regenerated by the pipeline in the same workflow run): the
        # chosen model, its MAE and its residual sigma must agree
        bad = []
        for h in range(1, 8):
            c, s = ex["check_metrics"][str(h)], shipped_metrics[rid][str(h)]
            if c["chosen"] != s["chosen"] or abs(c["mae"] - s["mae"]) > 0.05 or abs(c["resid_sigma"] - s["resid_sigma"]) > 0.05:
                bad.append((h, c, {k: s[k] for k in ("chosen", "mae", "resid_sigma")}))
        sanity[rid] = "MATCH" if not bad else "MISMATCH"
        print("  sanity check vs pipeline snapshot:", "MATCH" if not bad else f"MISMATCH {bad}")
        if orig_metrics is not None:
            rbad = [(h, ex["check_metrics"][str(h)]["ridge_mae"],
                     orig_metrics[rid][str(h)]["ridge_mae"])
                    for h in range(1, 8)
                    if ex["check_metrics"][str(h)]["ridge_mae"] is None
                    or abs(ex["check_metrics"][str(h)]["ridge_mae"]
                           - orig_metrics[rid][str(h)]["ridge_mae"]) > 0.011]
            print("  ridge vs original shipped snapshot:",
                  "MATCH" if not rbad else f"DIFF {rbad}")
        print(f"  backtest rows: {len(ex['backtest'])}, series days: {len(ex['series'])}")
        wk = ex["outlooks"]["weekly"]; mo = ex["outlooks"]["monthly"]
        print(f"  weekly: {wk['n_periods']} weeks, holdout from {wk['holdout_start']}; "
              f"monthly: {mo['n_periods']} months, holdout from {mo['holdout_start']}; "
              f"regimes: k={ex['regimes']['k']} sil={ex['regimes']['silhouette']}")

    # ---- build metadata for the energy MLOps panel: every value below is
    # produced by THIS run (timestamp, data spans, model selections, drift) --
    meta_regions = {}
    for rid, ex in extra.items():
        dmax_f, dmax_v = None, 0.0
        for f, d in ex["drift"].items():
            if abs(d["shift_std"]) > dmax_v:
                dmax_f, dmax_v = f, abs(d["shift_std"])
        drift_status = ("strong shift — watch" if dmax_v >= 1.0 else
                        "mild shift" if dmax_v >= 0.5 else "stable")
        wk, mo, yr = ex["outlooks"]["weekly"], ex["outlooks"]["monthly"], ex["outlooks"]["yearly"]
        meta_regions[rid] = dict(
            data_first=ex["stats"]["first_date"], data_last=ex["stats"]["last_date"],
            n_days=ex["stats"]["n"], n_origins=ex["n_origins"],
            train_origins=ex["train_origins"], holdout_origins=ex["holdout_origins"],
            holdout_start=ex["holdout_start"],
            daily_chosen={h: ex["check_metrics"][h]["chosen"] for h in ex["check_metrics"]},
            weekly_chosen={h: (m or {}).get("chosen") for h, m in wk["metrics"].items()},
            monthly_chosen={h: (m or {}).get("chosen") for h, m in mo["metrics"].items()},
            weekly_holdout_start=wk["holdout_start"], monthly_holdout_start=mo["holdout_start"],
            yearly_years=yr["years"], regimes_k=ex["regimes"]["k"],
            drift_max_feature=dmax_f, drift_max_shift=round(dmax_v, 2),
            drift_status=drift_status, sanity=sanity[rid])
    extra["build_meta"] = dict(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        generator="gen_energy_extra.py — the energy pipeline's own training code, "
                  "re-run on its cached raw data as part of the merged build",
        sanity_vs_shipped=sanity, regions=meta_regions)
    out = Path(__file__).parent / "energy_extra.js"
    out.write_text("window.HTE_EXTRA = " + json.dumps(extra) + ";\n")
    print("wrote", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
