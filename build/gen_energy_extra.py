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

GROUPS = {
    "Price lags & rolling means": ["p_last", "p_lag1", "p_lag7", "p_roll7", "p_roll30"],
    "Natural gas (Henry Hub)": ["gas_lag1", "gas_roll7"],
    "Hydro: flow, soil, snow": ["flow7", "flow_anom", "soil", "soil7", "swe"],
    "Weather (7d precip/temp/snow)": ["precip7", "temp7", "snow7"],
    "Calendar cycles": ["dow_sin", "dow_cos", "doy_sin", "doy_cos"],
}


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
    return out


def main():
    ice = P.fetch_ice_prices()
    gas = P.fetch_henry_hub()
    shipped = json.loads((WS / "site" / "data.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    shipped_metrics = {r["id"]: r["metrics"] for r in shipped["regions"]}
    extra = {}
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
        print("  sanity check vs shipped data.js metrics:", "MATCH" if not bad else f"MISMATCH {bad}")
        print(f"  backtest rows: {len(ex['backtest'])}, series days: {len(ex['series'])}")
    out = Path(__file__).parent / "energy_extra.js"
    out.write_text("window.HTE_EXTRA = " + json.dumps(extra) + ";\n")
    print("wrote", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
