#!/usr/bin/env python3
"""Generate genuine extras for the merged HydroTwin+Energy site.

Reuses the HydroTwin Energy pipeline's OWN code (pipeline/pipeline.py) and its
cached raw data (offline) to compute, per energy region:
  - full daily price series
  - walk-forward holdout backtest: for every holdout origin, the chosen model's
    +1..+7d predictions (the exact models train_region fits/scores), plus the
    persistence anchor and the per-horizon residual sigma
  - feature importance: standardized ridge coefficients per horizon
  - feature-group ablation (ridge): holdout MAE with each group dropped
  - correlations of each feature with the next-day signed-log price change
  - drift: train-origin vs holdout-origin feature mean shift (in train stds)
  - headline stats of the price series
Sanity check: recomputed holdout metrics are compared against the shipped
site/data.js metrics and printed; they must match (same code path).
Output: energy_extra.js  (window.HTE_EXTRA = {...})
"""
import importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

import os
WS = Path(os.environ.get("HTE_WS", Path.home() / "workspace" / "hydrotwin_energy"))
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

def _outlook(feat_df, series, horizons, key_of_target, step_fn):
    """Delta-target outlook trainer shared by the weekly and monthly scales.

    feat_df: features per origin period (first column block must include
    'lvl', the period's mean price). series: the aggregated price series on
    the same index. Candidates per horizon: ridge on standardized features
    vs period climatology (mean realized target for the same calendar slot,
    fit on training origins only). Winner picked on a validation slice (last
    20% of train), refit on the full train, scored once on the holdout (last
    20% of origins). The issued outlook refits the winner on ALL origins.
    Returns (metrics, backtest rows, forecast rows, holdout_start, n_origins).
    """
    base = feat_df.dropna()
    metrics, backtest, forecast = {}, [], []
    holdout_start, n_origins = None, 0
    for h in horizons:
        y = series.shift(-h).reindex(base.index)
        ok = y.notna().values
        O, yv = base[ok], y[ok].values
        n = len(O)
        if n < 20:
            metrics[str(h)] = None
            continue
        n_origins = max(n_origins, n)
        split = O.index[int(n * 0.8)]
        if holdout_start is None:
            holdout_start = str(split.date())
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
        chosen = min(val_mae, key=val_mae.get)

        # refit winner on the full training period, score once on holdout
        if chosen == "ridge":
            sc_t, m_t = ridge_fit(tr_idx)
            pred_ho = to_price(m_t.predict(sc_t.transform(Xv[ho_idx])), lvl[ho_idx])
        else:
            tab_t, ov_t = clim_table(tr_idx)
            pred_ho = clim_pred(ho_idx, tab_t, ov_t)
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
        else:
            tab_a, ov_a = clim_table(all_idx)
            pred_f = float(tab_a.get(key_of_target(base.index[last_i], h), ov_a))
        forecast.append(dict(date=str(step_fn(base.index[last_i], h).date()), h=h,
                             pred=round(pred_f, 2),
                             lo=round(pred_f - 1.2816 * sigma, 2),
                             hi=round(pred_f + 1.2816 * sigma, 2)))
    return metrics, backtest, forecast, holdout_start, n_origins


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
    wm, wbt, wfc, who, wn = _outlook(
        W, wk, list(range(1, 13)),
        key_of_target=lambda d, h: int((d + pd.Timedelta(days=7 * h)).isocalendar().week),
        step_fn=lambda d, h: d + pd.Timedelta(days=7 * h))
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
    mm, mbt, mfc, mho, mn = _outlook(
        M, mo, list(range(1, 7)),
        key_of_target=lambda d, h: int((d + pd.DateOffset(months=h)).month),
        step_fn=lambda d, h: d + pd.DateOffset(months=h))
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


def region_extras(rid, cfg, F):
    has_snow = bool(cfg["snotel"])
    X = P.make_features(F, has_snow)
    origins = X.loc[P.ORIGIN_START:P.END].dropna()
    origins = origins[origins.index + pd.Timedelta(days=7) <= F.index.max()]
    feats = list(X.columns)
    split = origins.index[int(len(origins) * 0.8)]
    train_idx = origins.index[origins.index < split]
    hold_idx = origins.index[origins.index >= split]
    price = F["price"]
    Xv = origins.values
    p_last_all = origins["p_last"].values
    pos = {d: i for i, d in enumerate(origins.index)}
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
        ok = y_all.notna().values
        tr_pos = np.array([pos[d] for d in train_idx if ok[pos[d]]])
        ho_pos = np.array([pos[d] for d in hold_idx if ok[pos[d]]])
        n_val = max(int(len(tr_pos) * 0.2), 30)
        fit_pos, val_pos = tr_pos[:-n_val], tr_pos[-n_val:]
        clip = (float(np.min(yv[tr_pos])), float(np.max(yv[tr_pos])) * 1.25)

        def to_price(d_pred, pl, clip=clip):
            return np.clip(P.slog_inv(P.slog(pl) + d_pred), clip[0], clip[1])

        sc_fit = StandardScaler().fit(Xv[fit_pos])
        val_mae = {}
        for kind in ("ridge", "mlp"):
            mdl = P._fit_candidate(kind, sc_fit.transform(Xv[fit_pos]), delta_all[fit_pos])
            pv = to_price(mdl[1].predict(sc_fit.transform(Xv[val_pos])), p_last_all[val_pos])
            val_mae[kind] = float(np.mean(np.abs(yv[val_pos] - pv)))
        chosen = min(val_mae, key=val_mae.get)
        sc_tr = StandardScaler().fit(Xv[tr_pos])
        mdl_tr = P._fit_candidate(chosen, sc_tr.transform(Xv[tr_pos]), delta_all[tr_pos])
        pred_ho = to_price(mdl_tr[1].predict(sc_tr.transform(Xv[ho_pos])), p_last_all[ho_pos])
        ev = P.evaluate(yv[ho_pos], pred_ho)
        resid = yv[ho_pos] - pred_ho
        resid = resid[np.isfinite(resid)]
        sigma = float(np.std(resid)) if len(resid) else float("nan")
        check[str(h)] = dict(chosen=chosen, mae=round(ev["mae"], 2), rmse=round(ev["rmse"], 2),
                             r2=round(ev["r2"], 3), n=ev["n"], resid_sigma=round(sigma, 2))

        # ridge coefficients (train fit) for feature importance
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
    out["regimes"] = market_regimes(F, X, origins)
    out["surface"] = forecast_surface(backtest)
    return out


def main():
    ice = P.fetch_ice_prices()
    gas = P.fetch_henry_hub()
    shipped = json.loads((WS / "site" / "data.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    shipped_metrics = {r["id"]: r["metrics"] for r in shipped["regions"]}
    extra = {}
    sanity = {}
    for rid, cfg in P.REGIONS.items():
        print(f"=== {rid} ===", flush=True)
        F, _names = P.build_region_frame(cfg, ice, gas)
        ex = region_extras(rid, cfg, F)
        extra[rid] = ex
        # sanity check vs shipped metrics
        bad = []
        for h in range(1, 8):
            c, s = ex["check_metrics"][str(h)], shipped_metrics[rid][str(h)]
            if c["chosen"] != s["chosen"] or abs(c["mae"] - s["mae"]) > 0.05 or abs(c["resid_sigma"] - s["resid_sigma"]) > 0.05:
                bad.append((h, c, {k: s[k] for k in ("chosen", "mae", "resid_sigma")}))
        sanity[rid] = "MATCH" if not bad else "MISMATCH"
        print("  sanity check vs shipped data.js metrics:", "MATCH" if not bad else f"MISMATCH {bad}")
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
