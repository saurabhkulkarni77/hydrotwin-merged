# hydrotwin-merged — build scripts for the merged twin

Build scripts that assemble the merged **HydroTwin (river) + HydroTwin Energy**
site into a new bucket (`hydrotwin-merged-2026`) behind a new CloudFront
distribution, without touching either existing site.

- `build/gen_energy_extra.py` — reuses the HydroTwin Energy pipeline's own
  training code on its cached raw data to compute the walk-forward holdout
  backtest, feature importance, feature-group ablation, correlations and
  drift diagnostics (`energy_extra.js`). Its recomputed holdout metrics are
  checked against the shipped HydroTwin Energy metrics and must match.
  It also trains the longer-horizon outlooks at their own scales (weekly
  ridge-vs-climatology models over 12 weekly horizons, monthly over 6,
  both with validation selection and holdout scores; a monthly climatology
  for the yearly outlook with a leave-one-year-out check), clusters days
  into market regimes (k-means on the standardized daily features, k by
  silhouette), builds the month x horizon forecast surface from the
  backtest, and emits `build_meta` (retrain timestamp, data spans, model
  selections, drift status) for the MLOps panels — all from the same run.
- `build/build_site.py` — copies a fresh hydrotwin-site tree verbatim, adds
  `energy/` (hub + 3 region pages in the river station-page panel format,
  plus a photorealistic 3D scene page per region at the hub's real
  geography, using the river pages' own vendored tile renderer and Ion
  token), and adds the energy regions to the map page's station list.
- `build/energy_region.js`, `build/energy_hub.js` — page renderers; every
  number is rendered at runtime from the energy project's own data files.

Deploy: the workflow `deploy-hydrotwin-merged-aws.yml` belongs in
`saurabhkulkarni77/hydrotwin-site/.github/workflows/` (that repo holds the AWS
secrets, same convention as `deploy-hydrotwin-energy-aws.yml`). It clones this
repo plus hydrotwin-site and hydrotwin-energy, builds, and deploys.
