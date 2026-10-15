# Stage 1 job mode

Sitemap-first discovery (rustmapper, then Stage 1) is the job mode operators
will use when `stage1.discovery.rustmapper_enabled` is on. The job id is
`stage1.discovery.job_id`. This page does not start a crawl and does not
implement the failure policy (that is #587).

When discovery degrades, Control Center and the CLI use one shared set of
status strings and next-action hints:

[rustmapper discovery degraded modes](../../docs/runbooks/rustmapper_discovery_degraded.md)

Print the same copy without crawling:

```bash
python cli.py stage1 job-mode
```

Control Center shows it on Pipeline Stages → Stage 1 (URL Discovery), under
the heading **Stage 1 job mode**.
