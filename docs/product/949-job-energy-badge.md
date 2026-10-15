# Job energy badge

- **Status:** Rough estimate. Always available as a scorecard. Not a meter.
- **Date:** 2026-10-14
- **Issue:** [#949](https://github.com/BenjaminSRussell/Scrapy/issues/949)

## Estimate

kWh is crawl runtime in hours times a stated wattage. The default is 100 W, one worker, not a measured draw. `JOB_ENERGY_WATTS` overrides it. A blank, non-numeric, or negative value keeps 100 W. Zero watts is a real zero, not a fallback.

| Runtime | Watts | kWh |
|---|---|---|
| 3600 s | 100 | 0.1 |
| 7200 s | 100 | 0.2 |
| unusable (negative, missing, not a number) | any | none |

The function is [`job_energy.py`](../../Scraping_project/src/utils/job_energy.py). The Scrapy extension records `pipeline_estimated_energy_kwh` when a crawl closes with a usable duration. It does not open a socket.

## Scorecard

Control Center Overview has a **Job energy** card. The header badge is the per-job estimate, or the sum when more than one job is in the scrape. Each row has its own badge. With no `pipeline_estimated_energy_kwh` series the badge says **No runtime** and the table stays empty.

## Disclaimer

The card and the scorecard payload say:

> Rough estimate only: runtime hours times a stated wattage (default 100 W, JOB_ENERGY_WATTS). Not a measured kWh reading, not a carbon inventory, and not a cloud bill.

Do not put this number in an ESG report. Do not treat it as grid carbon. `carbon_scheduling` is unchanged and still does not delay Stage 3 or Stage 4. Cost caps stay null unless an operator sets them. No SLO or alert is added.
