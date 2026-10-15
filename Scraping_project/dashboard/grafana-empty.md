# Grafana empty home

`http://localhost:3000` is the monitoring entrypoint. The home page should still tell you what to do when no pipeline dashboards were provisioned, or when provisioning failed after a restart. Compose mounts [`grafana-home.json`](grafana-home.json) as Grafana's default home (`GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/etc/grafana/home.json`) so those steps are on the page you land on. An organization or user home dashboard, if one was saved in Grafana, overrides that file.

`serve.py` does not serve this note (`.md` returns 404). Read it in the repo, or use the home panel inside Grafana.

## Next steps

1. **Control Center** (this ops UI; it does not need Grafana): [http://127.0.0.1:8080](http://127.0.0.1:8080)
2. **Open a dashboard** when provisioning worked and only the home page looks bare:
   - [Scraping Pipeline Health](http://localhost:3000/d/scraping-pipeline-health) (`scraping-pipeline-health`)
   - [Pipeline SLOs](http://localhost:3000/d/pipeline-slos) (`pipeline-slos`)
3. **Import** when the dashboard list is empty. In Grafana open [Dashboards → New → Import](http://localhost:3000/dashboard/import) and upload the JSON under `Scraping_project/monitoring/dashboards/`:
   - `scraping_pipeline_health.json`
   - `pipeline_slos.json`
   - `ops_lag_depth_commits.json` (uid `ops-lag-depth-commits`)
   - `unified_dashboard.json` (off-site and streaming panels)
4. **Fix provisioning** so the next start loads that directory:
   - Compose (`monitoring` profile) mounts `monitoring/grafana_dashboards.yml` at `/etc/grafana/provisioning/dashboards/dashboards.yml` and `monitoring/dashboards/` at `/var/lib/grafana/dashboards` (read-only).
   - `docker compose --profile monitoring logs grafana` should not report a provisioning failure. The provider path is `/var/lib/grafana/dashboards`.
   - Helm uses the copies in `k8s/helm/scraping-pipeline/files/monitoring/dashboards/`. The chart does not mount `grafana-home.json`; the import path and Control Center URL are the same.

Sign in as `admin`. The password is `GRAFANA_ADMIN_PASSWORD` from `.env` (default `admin` for local dev only).
