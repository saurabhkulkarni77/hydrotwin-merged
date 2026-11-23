#!/usr/bin/env python3
"""Live-refresh the HydroTwin Energy raw data cache before the merged build.

Used by the v2 deploy workflow (deploy-hydrotwin-merged-aws-v2.yml) BEFORE
gen_energy_extra.py / build_site.py, so each scheduled run is a genuine
live pull + retrain + redeploy.

How it works: for every feed, the cached raw file(s) are moved aside and the
energy pipeline's OWN fetch function (HTE_WS/pipeline/pipeline.py — the same
code and the same source URLs that produced the shipped snapshot) is called
to re-download them:

  feed                              pipeline fetcher        source
  --------------------------------  ----------------------  -------------------------
  EIA ICE day-ahead prices          fetch_ice_prices()      eia.gov wholesale xls
  EIA Henry Hub natural gas         fetch_henry_hub()       eia.gov hist xls
  CAISO OASIS NP15 day-ahead LMP    fetch_caiso_daily()     oasis.caiso.com
  USGS daily flows                  fetch_usgs(site)        waterservices.usgs.gov
  NRCS SNOTEL snow (SWE)            fetch_nrcs(..., WTEQ)   wcc.sc.egov.usda.gov
  NRCS SCAN soil moisture           fetch_nrcs(..., SMS)    wcc.sc.egov.usda.gov
  Open-Meteo weather                fetch_meteo(lat, lon)   archive-api.open-meteo.com

No API key is required by any feed: pipeline.py reads no environment
variables and calls only these public endpoints.

Per-feed fallback: if a live fetch raises, or its output file is missing,
empty, or (for CSVs) drastically shorter than the cached file it replaced,
the cached file is restored and a warning naming the feed is echoed — the
build still succeeds, using the bundled cache for that feed. The bundled
cache itself is guaranteed present because this script first reconciles the
workspace from build/energy_inputs/ (same fill-gaps step as the build
scripts).

Scope note: the fetchers pull the pipeline's configured window
(FETCH_START..END constants in pipeline.py). This script refreshes the data
inside that window (revised/final values, late postings); it does not
extend the window — END lives in the hydrotwin-energy repo's pipeline.py.

Env: HTE_WS (energy workspace), MERGED_ROOT (hydrotwin-merged checkout).
Optional HTE_REFRESH_ONLY: comma-separated substrings; only feeds whose
name contains one of them are refreshed (testing aid).
Exit code: 0 unless the pipeline module cannot be imported at all.
"""
import importlib.util, os, shutil, sys
from pathlib import Path

WS = Path(os.environ.get("HTE_WS", Path.home() / "workspace" / "hydrotwin_energy"))
MERGED_ROOT = Path(os.environ.get("MERGED_ROOT", Path(__file__).resolve().parent.parent))


def _reconcile_energy_ws():
    """Fill gaps in the energy workspace from build/energy_inputs/ (same
    fill-gaps-only step as gen_energy_extra.py / build_site.py) so the
    cached fallback files exist before any live fetch is attempted."""
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


def _lines(p):
    try:
        with open(p, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def run_job(feed, files, fetch, backup_dir):
    """One refresh job: back up `files`, delete them, call fetch(), verify.
    Returns True on a genuine live refresh, False when the cache was kept."""
    backups = {}
    for p in files:
        if p.exists():
            b = backup_dir / p.name
            shutil.copy2(p, b)
            backups[p] = b
            p.unlink()
    err = None
    try:
        fetch()
    except Exception as e:  # network down, bad payload, parse error, ...
        err = f"{type(e).__name__}: {e}"
    if err is None:
        for p in files:
            if not p.exists() or p.stat().st_size == 0:
                err = f"fetch produced no usable {p.name}"
                break
            if p.suffix == ".csv" and p in backups:
                old, new = _lines(backups[p]), _lines(p)
                if new < max(10, int(old * 0.5)):
                    err = f"refreshed {p.name} implausibly short ({new} lines vs cached {old})"
                    break
    if err is not None:
        for p in files:
            if p in backups:
                shutil.copy2(backups[p], p)
            elif p.exists():
                p.unlink()
        print(f"::warning::Live refresh FAILED for feed '{feed}': {err} — "
              f"keeping cached data for this feed.", flush=True)
        print(f"WARNING: live refresh failed for feed '{feed}' ({err}); "
              f"cached data kept.", flush=True)
        return False
    sizes = ", ".join(f"{p.name} {p.stat().st_size} B" for p in files)
    print(f"refreshed '{feed}': {sizes}", flush=True)
    return True


def main():
    _reconcile_energy_ws()
    spec = importlib.util.spec_from_file_location("epipe", WS / "pipeline" / "pipeline.py")
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)
    RAW = P.RAW
    backup_dir = RAW / ".refresh_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    regions = P.REGIONS
    usgs_sites = sorted({s for cfg in regions.values() for s in cfg["usgs"]})
    snotel = sorted({t for cfg in regions.values() for t in cfg["snotel"]})
    scan = sorted({t for cfg in regions.values() for t in cfg["scan"]})
    meteo_pts = sorted({pt for cfg in regions.values() for pt in cfg["weather"]})
    caiso_node = regions["np15"]["caiso_node"]

    jobs = []  # (feed name, [files], fetch callable)
    jobs.append(("EIA ICE day-ahead prices",
                 [RAW / f"ice_electric-{y}.xlsx" for y in (2023, 2024, 2025, 2026)],
                 P.fetch_ice_prices))
    jobs.append(("EIA Henry Hub natural gas",
                 [RAW / "henryhub.xls"], P.fetch_henry_hub))
    jobs.append(("CAISO OASIS NP15 day-ahead LMP",
                 [RAW / f"caiso_{caiso_node}.csv"],
                 lambda: P.fetch_caiso_daily(caiso_node)))
    for site in usgs_sites:
        jobs.append((f"USGS daily flows",
                     [RAW / f"usgs_{site}.csv"],
                     lambda site=site: P.fetch_usgs(site)))
    for t in snotel:
        jobs.append(("NRCS SNOTEL snow water equivalent",
                     [RAW / f"nrcs_{t.replace(':', '_')}.csv"],
                     lambda t=t: P.fetch_nrcs(t, "WTEQ::value", "swe")))
    for t in scan:
        jobs.append(("NRCS SCAN soil moisture",
                     [RAW / f"nrcs_{t.replace(':', '_')}.csv"],
                     lambda t=t: P.fetch_nrcs(t, "SMS:-8:value", "soil")))
    for lat, lon in meteo_pts:
        tag = f"{abs(lat):.2f}_{abs(lon):.2f}"
        jobs.append(("Open-Meteo weather",
                     [RAW / f"meteo_{tag}.csv"],
                     lambda lat=lat, lon=lon, tag=tag: P.fetch_meteo(lat, lon, tag)))

    only = os.environ.get("HTE_REFRESH_ONLY", "").strip()
    if only:
        subs = [s.strip().lower() for s in only.split(",") if s.strip()]
        jobs = [j for j in jobs if any(s in j[0].lower() for s in subs)]
        print(f"HTE_REFRESH_ONLY filter active: {len(jobs)} job(s) selected", flush=True)

    ok = fail = 0
    failed_feeds = []
    for feed, files, fetch in jobs:
        if run_job(feed, files, fetch, backup_dir):
            ok += 1
        else:
            fail += 1
            failed_feeds.append(feed)
    shutil.rmtree(backup_dir, ignore_errors=True)
    print(f"live refresh summary: {ok} job(s) refreshed live, {fail} job(s) "
          f"kept cached data" + (f" — feeds on cache: {sorted(set(failed_feeds))}"
                                 if failed_feeds else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
