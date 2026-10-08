# SEV1: crawler abusing a target site, kill switch (#456)

Use this when a target site reports abuse, a domain starts banning us, or a bad config or seed flood is hammering a site.

## 1. Stop all downloads (≤ 5 s)

```bash
cd Scraping_project
python cli.py killswitch on --reason "abuse report from <site>, ticket <id>" --actor "<your name>"
```

- **Stage 1** spiders drop every queued request and close with reason `kill_switch`.
- **Stage 2** stops starting batches. URLs stay `pending`; nothing is marked failed.
- **SLA:** workers re-read the switch every `crawl_safety.kill_switch_check_secs` (default 5 s), so new downloads stop within that interval. In-flight requests finish.
- **Redis down?** Set `CRAWL_KILL_SWITCH=1` on the workers (env / Helm value) and restart them. The env switch works without Redis.

Verify:

- `python cli.py killswitch status` shows `"engaged": true`.
- Grafana / Prometheus: `crawl_kill_switch_engaged == 1`, and `rate(crawl_guard_blocked_total[1m]) > 0` while queued work drains.

## 2. Contain

- Remove or disable the offending seeds: `python cli.py seeds disable <url> --actor <you>`.
- Tighten politeness for the domain (crawl-delay / concurrency) before resuming.
- Optionally set budgets in `config.yml` so a repeat can't run away:

  ```yaml
  crawl_safety:
    max_requests_per_day: 200000     # all workers, all domains (0 = unlimited)
    max_bytes_per_day: 20000000000
    per_crawl_max_requests: 50000    # per CRAWL_JOB_ID
    per_crawl_max_bytes: 5000000000
  ```

  When a budget is spent, Stage 1 closes with `budget_exceeded:<cap>` and Stage 2 defers, the same behaviour as the switch but scoped to that budget.

## 3. Resume

```bash
python cli.py killswitch off --actor "<your name>" --reason "seeds fixed, delay raised"
```

Restart the Stage 1 spiders, which were closed. Stage 2 picks up the pending URLs on its next loop.

## 4. Audit

Every engage/release is recorded with actor, reason, and UTC time:

```bash
python cli.py killswitch audit --limit 50
```

Attach this output to the incident ticket.
