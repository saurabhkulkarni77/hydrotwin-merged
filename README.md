# hydrotwin-merged — build scripts for the merged twin

Build scripts that assemble the merged **HydroTwin (river) + HydroTwin Energy**
site into a new bucket (`hydrotwin-merged-2026`) behind a new CloudFront
distribution, without touching either existing site.

- `build/gen_energy_extra.py` — reuses the HydroTwin Energy pipeline's own
  training code on its cached raw data to compute the walk-forward holdout
  backtest, feature importance, feature-group ablation, correlations and
  drift diagnostics (`energy_extra.js`). Its recomputed holdout metrics are
  checked against the shipped HydroTwin Energy metrics and must match.
- `build/build_site.py` — copies a fresh hydrotwin-site tree verbatim, adds
  `energy/` (hub + 3 region pages in the river station-page panel format),
  and adds the energy regions to the map page's station list.
- `build/energy_region.js`, `build/energy_hub.js` — page renderers; every
  number is rendered at runtime from the energy project's own data files.

Deploy: the workflow `deploy-hydrotwin-merged-aws.yml` belongs in
`saurabhkulkarni77/hydrotwin-site/.github/workflows/` (that repo holds the AWS
secrets, same convention as `deploy-hydrotwin-energy-aws.yml`). It clones this
repo plus hydrotwin-site and hydrotwin-energy, builds, and deploys.
