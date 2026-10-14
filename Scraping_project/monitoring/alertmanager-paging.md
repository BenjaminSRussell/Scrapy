# Alertmanager paging

Paging is off in the chart. `k8s/helm/scraping-pipeline/files/monitoring/alertmanager.yml` has no active `pagerduty_configs`, `opsgenie_configs`, or `webhook_configs`. The commented examples on the `critical` receiver are not loaded. Alertmanager does not call PagerDuty or Opsgenie, and it does not read a paging secret, until an operator does both steps below.

The StatefulSet (`templates/alertmanager-statefulset.yaml`) mounts only the ConfigMap copied from that file. This note does not change that mount. A secret that is not mounted cannot enable a page.

## Critical vs warning

Routes in `alertmanager.yml` already split on `severity`:

| Label | Receiver | Repeat | Page |
|---|---|---|---|
| `critical` | `critical` | 30m | only after one example below is uncommented and its secret file exists |
| `warning` | `warning` | 2h | no |
| `info` | `info` | 12h | no |
| anything else | `default` | 3h | no |

A firing `critical` alert inhibits `warning` for the same `cluster`, `alertname`, and `spider`. A firing `warning` inhibits `info` the same way. Do not attach a paging config to `warning`, `info`, or `default`.

## Inject a secret

Do not commit a routing key, API key, webhook URL, or token. Do not put one in Helm values or in the Alertmanager ConfigMap. Create the Secret outside this repo, in the release namespace (the same namespace as the other chart secrets, `uconn-scraper` in `DEPLOYMENT.md`).

PagerDuty, Events API v2 integration key (one line, the key only):

```bash
kubectl create secret generic alertmanager-paging \
  --from-file=pagerduty-routing-key=./pagerduty-routing-key \
  -n uconn-scraper
```

Opsgenie GenieKey (one line, the key only). Use this instead of the PagerDuty secret, not in addition to a second live receiver:

```bash
kubectl create secret generic alertmanager-paging \
  --from-file=opsgenie-api-key=./opsgenie-api-key \
  -n uconn-scraper
```

The key file stays on the operator workstation. It is not a path in git. The chart image is `prom/alertmanager:v0.28.1`.

Mounting is a separate operator edit of the StatefulSet and is not in this chart today:

- Secret volume `alertmanager-paging`, item mode `0400`
- read-only mount at `/etc/alertmanager/secrets`

Until that directory exists inside the pod, leave both receiver blocks commented. Uncommenting `routing_key_file` or `api_key_file` while the file is absent makes Alertmanager refuse the config. It does not substitute a placeholder key and it does not send a page.

## Turn on one receiver

On the `critical` receiver only, uncomment exactly one block:

- PagerDuty: `routing_key_file: /etc/alertmanager/secrets/pagerduty-routing-key`
- Opsgenie: `api_key_file: /etc/alertmanager/secrets/opsgenie-api-key`

Do not set `routing_key`, `service_key`, `api_key`, `url`, or `api_url`. Do not add `webhook_configs`. Do not uncomment both blocks. Then roll the Alertmanager StatefulSet. `warning` stays a non-paging receiver.
