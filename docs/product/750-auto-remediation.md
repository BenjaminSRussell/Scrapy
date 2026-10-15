# Restart a wedged stage on a critical SLO burn

- **Status:** Design only. No controller, CronJob, Helm value, alert, workflow, or metric is added here.
- **Date:** 2026-10-14
- **Issue:** [#750](https://github.com/BenjaminSRussell/Scrapy/issues/750)

Issue #750 asked for an operator or a cron that restarts a Deployment when an SLO burn is critical, with approval defaulting to manual. This file is that design. It does not implement it.

Nothing here restarts a pod. The tree has no controller, CronJob, or Helm value for this procedure, and this file does not add one. Do not read it as a claim that a controller exists.

## What ships today

Published objectives are only the three in [Pipeline SLOs](../../Scraping_project/docs/guides/PIPELINE_SLOS.md):

| SLO | Objective | Shipped comparison | Alert | Severity |
|---|---|---|---|---|
| Stage 2 success | at least 99% over 24h | `stage2:success_ratio:24h < 0.99` while the 24h window has rows | `Stage2SuccessSLO` | critical, `slo: "true"` |
| QueueBacklog | `redis_queue_length` stays at or below 10000 | `max by (queue, site_id) (redis_queue_length) > 10000` | `QueueBacklog` | warning |
| Kafka consumer lag | max lag stays at or below 250000 messages | `max_over_time(kafka_consumergroup_lag_max[5m]) > 250000` | `KafkaConsumerLagSLO` | critical, `slo: "true"` |

No fourth SLO. `KafkaHighConsumerLag` (10000 messages) and `KafkaVeryHighConsumerLag` (100000 messages) are earlier warnings, not the Kafka objective. The second one is severity critical and still not an SLO. `DeltaWriteQueueBacklog` is the in-memory writer queue (`delta_write_queue_depth > 100`). It is not `QueueBacklog`. Do not retune 99%, 10000, or 250000.

The rules are in [alerting_rules.yml](../../Scraping_project/monitoring/alerting_rules.yml) and [recording_rules.yml](../../Scraping_project/monitoring/recording_rules.yml). The chart carries the same files at [files/monitoring/alerting_rules.yml](../../Scraping_project/k8s/helm/scraping-pipeline/files/monitoring/alerting_rules.yml) and [files/monitoring/recording_rules.yml](../../Scraping_project/k8s/helm/scraping-pipeline/files/monitoring/recording_rules.yml). This design does not edit them.

`stage2:success_ratio:24h` emits nothing when the 24h window has no rows. An idle worker is not 0% and not a breach. `Stage2SuccessSLO` has no `bool` modifier. A present value is the ratio under 0.99, not 1.

## Critical burn

Only Stage 2 has an error budget. 99% success leaves 1% of `stage2_rows_total` over that same 24h. The budget is exhausted when `Stage2SuccessSLO` fires. [Pipeline SLOs](../../Scraping_project/docs/guides/PIPELINE_SLOS.md) says no 2x or 14.4x burn rate is published. This design does not add a multi-window burn alert, a burn-rate multiplier, or a fourth objective.

**Critical burn** means `Stage2SuccessSLO` is firing: the ratio is under 99%, the window has rows, and the alert has held for its shipped `for: 15m`.

`KafkaConsumerLagSLO` is a critical SLO breach, not a ratio burn. Redis and Kafka are threshold objectives. This design does not give them a percentage. A restart for that alert is allowed only under the Kafka gates below. The audit must call it a critical SLO breach, not a burn.

`QueueBacklog` does not authorize a restart. It is severity warning. `redis_queue_length` is the JS render priority queue, not `stage2_queue` and not a stage Deployment's depth ([Redis runbook](../../Scraping_project/docs/runbooks/redis.md)).

## Wedged

A breach is not a wedge. A wedge is the process stuck while that critical alert is firing. Restarting a live worker that is quarantining bad rows does not fix the ratio. The Stage 2 alert's own action is to check `stage2_rows_total` by outcome and the `stage2_errors` quarantine, and not to raise 99%.

Stages are the five names on the [stage map](../STAGE_MAP.md): 1 Scout, 2, 3, 4, and Orch. This procedure restarts at most one Deployment, from the allow-list only. It does not restart Scout, stage 3, stage 4, or the orchestrator. Those workloads are not the process behind these two alerts.

### Stage 2 burn

The chart Deployment is `{{ fullname }}-stage2` in [stage-workers-deployments.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/templates/stage-workers-deployments.yaml). The container command is `python -u src/stage2/stage2_worker.py`. The [README install](../../Scraping_project/k8s/README.md) uses release `scraping-pipeline` in namespace `scraping-pipeline`. That release name contains the chart name, so [fullname](../../Scraping_project/k8s/helm/scraping-pipeline/templates/_helpers.tpl) is `scraping-pipeline` and the Deployment is `scraping-pipeline-stage2`. Another release name changes the prefix. Read `metadata.name` from the cluster. Do not guess.

Stage 2 is wedged only when `Stage2SuccessSLO` is firing and either:

- at least one pod of that Deployment is not Ready, or
- `Stage2QueueUnreadable` is also firing (`increase(stage2_queue_read_failures_total[30m]) > 0`, held 5m).

Liveness is `python -m src.utils.probe alive stage2_worker.py`. A live process passes it. Readiness opens a TCP connection to Postgres. Neither probe measures the success ratio. If every pod is Ready and `Stage2QueueUnreadable` is not firing, the worker is up and can read `stage2_queue`. Refuse the restart.

`Stage2StarvedWhileStage1Active` means Stage 1 output is not reaching `stage2_queue`. Restarting stage 2 does not write that table. Refuse.

`up{job="stage2_worker"}` is a scrape of the worker metrics port, not a pod condition. Compose discovers `stage2-worker` ([prometheus.yml](../../Scraping_project/monitoring/prometheus.yml)). The chart discovers `<fullname>-stage2-metrics.<namespace>.svc` when `workerMetrics.enabled` and `stage2Worker.enabled` ([prometheus-statefulset.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/templates/prometheus-statefulset.yaml)). Both default on. `0` means that scrape failed. Absence is not NotReady. The wedge test does not use `up`. A missing series cannot authorize a restart.

### Kafka critical breach

The only chart consumer of the ingest topic is [kafka-delta-ingestor-deployment.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/templates/kafka-delta-ingestor-deployment.yaml): Deployment `{{ fullname }}-delta-ingestor`. On the README install that name is `scraping-pipeline-delta-ingestor`. Its args subscribe to `kafkaDeltaIngestor.config.topic` (default `scraped-items`) with `--app-id` `kafkaDeltaIngestor.config.appId` (default `scrapy-ingestor`). The binary sets Kafka `group.id` to that app id and `client.id` to `{app-id}-{pid}` ([main.rs](../../Scraping_project/kafka-delta-ingest/src/main.rs)). [config.yml](../../Scraping_project/config.yml) `kafka.consumer.groups.ingest` is that same id. The Deployment does not pass `kafka.consumer.group_id`.

`kafka_consumergroup_lag_max` sets the `group` label from JMX `client_id` by stripping the last hyphen segment ([recording_rules.yml](../../Scraping_project/monitoring/recording_rules.yml)). With this `client.id`, the label equals the ingest `group.id` (`scrapy-ingestor`). That is not proof this Deployment is the only member: another process can use the same prefix. Do not treat a match as the only signal, and do not treat a mismatch as proof the Deployment is healthy. `kafka.consumer.group_id` stays the retired shared id `scraping-pipeline`. Do not pass it as `--app-id`. DQ (`scrapy-dq`) and replay (`scrapy-replay`) are other groups; lag on those groups is not this Deployment.

The ingestor is wedged only when all of these are true:

- `KafkaConsumerLagSLO` is firing (max lag over 250000 messages, held 15m).
- The firing sample's `topic` equals the topic argument on that Deployment, read from the live spec. The values default is `scraped-items`. A live override wins over the default.
- No other Deployment in this release has that topic in its container args. If one does, refuse. This design has no rule for choosing between two consumers.
- At least one pod of `<fullname>-delta-ingestor` is not Ready. Liveness is `pgrep -f ingest`. Readiness is `test -w /delta`.

If those pods are Ready, the process is up and the volume is writable. Lag over 250000 is then a backlog, not a dead consumer. The alert says to investigate, scale consumers, or pause producers. This procedure does not scale and does not pause producers. Refuse.

Do not restart the Kafka or Zookeeper StatefulSet. The alert's `component: kafka` names the signal, not the target.

## Allow-list

| Firing alert | Only Deployment | Never |
|---|---|---|
| `Stage2SuccessSLO`, and the Stage 2 wedge | `<fullname>-stage2` | scrapy, stage3, stage4, delta-ingestor, any StatefulSet, any exporter |
| `KafkaConsumerLagSLO`, and the Kafka wedge | `<fullname>-delta-ingestor` | Kafka brokers, Zookeeper, stage2, scrapy, stage3, stage4 |

Stage 4 is `enabled: false` by default ([values.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/values.yaml)) and is not a target. One incident, one Deployment, one restart.

## Safety gates

Every gate is required. The first failure stops the procedure. Record the denial. Do not skip ahead.

1. **Manual approval is the only mode.** A restart with no approval line written first does not happen. There is no automatic mode and no Helm key to turn one on. This file does not add a key. A script, a controller, or a CronJob approving itself is not approval.

2. **The alert is one of the two critical SLO alerts**, still firing, with `slo: "true"` and `severity: critical`. `QueueBacklog`, `KafkaVeryHighConsumerLag`, `KafkaHighConsumerLag`, and every other critical page (broker down, Redis down, Postgres down, app down) do not qualify.

3. **The workload is wedged**, as defined above. A ratio under 99% with Ready pods and a readable queue is not a wedge. Kafka lag with a Ready ingestor is not a wedge.

4. **The target is the one allow-list Deployment** for that alert. Match `metadata.name` in the release namespace. The README namespace is `scraping-pipeline`. Do not use the alert's `component` label as a Deployment name.

5. **Do not take down the last Ready pod.** The chart does not set a rolling `strategy` on these Deployments, and this procedure does not patch one.
   - If `spec.replicas` is under 2 and any pod is Ready, refuse. Do not add a surge and do not scale.
   - If no pod is Ready, the Deployment is already down. A restart is allowed for that NotReady wedge. Do not scale it up first.
   - If `spec.replicas` is at least 2, `kubectl rollout restart` is the only mutation. Do not delete pods by hand.
   Stage 2's chart default is `replicaCount: 3` with an HPA (`minReplicas: 2`). The ingestor default is `replicaCount: 2` and it has no HPA. Defaults are not the live spec. Read the live spec.

6. **Do not join a rollout already in progress.** If updated replicas are not yet available, or this procedure already restarted that Deployment for this incident, refuse.

7. **One restart per incident.** An incident is one alert name plus one Deployment, from the firing until that alert has stayed not-firing for its own `for` (15m). A restart that leaves the wedge in place is not a reason to restart again. Stop.

8. **Leave the pod lifecycle alone.** Do not pass `--force` or `--grace-period=0`. Do not delete the ReplicaSet. Do not patch `terminationGracePeriodSeconds` (120 on both targets in [values.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/values.yaml)). Stage 2 `preStop` runs `LakeDrainer().drain_transient_queues()` and then `sleep 10` (the Python is under `|| true`, so a drain error does not block the sleep). The ingestor `preStop` sleeps 20 seconds. Both must be allowed to finish.

9. **Do not touch the volume.** Both targets mount the delta-lake PVC. Do not delete, resize, or replace that claim. Do not run `helm upgrade` or `helm rollback` as the remediation.

10. **Respect the worker PDB.** Stage 2 has a PodDisruptionBudget with `maxUnavailable: 1` ([poddisruptionbudgets.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/templates/poddisruptionbudgets.yaml)). The ingestor is not in that list. Do not delete every pod. Do not set a budget to zero.

11. **Do not retune and do not silence.** Do not change 99%, 10000, or 250000. Do not add an Alertmanager silence to make a gate look clear. That is a denial, and the denial is audited.

12. **Snapshot the three objectives before approval.** Record the Stage 2 ratio (or no series), `max by (queue) (redis_queue_length)`, and `max_over_time(kafka_consumergroup_lag_max[5m])` with `topic` and `group` when those labels are present. The snapshot is context. Only the wedge gates authorize a restart. `QueueBacklog` over 10000 in the snapshot is not a trigger.

## Audit

Every action is written down before the next one, including a denial and an approval that does not lead to a restart. This design adds no log pipeline, metric, audit webhook, or ticket system. The record is the incident note the operator already has. If there is nowhere to write it, the restart does not proceed.

Kubernetes events from a rollout, when the cluster already records them, are not a substitute. They do not contain the denial or the approval.

Each line has:

- time in UTC
- person (not a service account, and not a controller)
- action: `propose`, `approve`, `deny`, `restart`, or `verify`
- alert name, `severity`, `slo` label, and for Kafka the firing `topic` and `group`
- the three-objective snapshot
- wedge evidence (Ready pod count, or `Stage2QueueUnreadable`)
- namespace and Deployment name, or `none` on a denial
- the word `approved`, or the denial reason
- the command run, or `not run`
- the result (rollout finished, refused, or still firing at verify)

`approve` is a separate line from `propose`. Writing the proposal does not approve it. The approver may be the same person. The line still has to be saved, with its time, before any restart command.

## Procedure

A person runs this. Nothing in the repo runs it.

1. Open the firing alert. If gate 2 fails, write `deny` and stop.
2. Write the three-objective snapshot.
3. Apply the wedge test for that alert. If it fails, write `deny`, quote the alert's published action, and stop.
4. Read the live Deployment name, `spec.replicas`, and Ready count. If the allow-list or the last-Ready-pod gate fails, write `deny` and stop.
5. Write `approve` with the Deployment name. Until that line is saved, do not type a restart.
6. Rolling restart of that one Deployment. For the README install and a Stage 2 wedge:

```bash
kubectl rollout restart deployment/scraping-pipeline-stage2 -n scraping-pipeline
kubectl rollout status deployment/scraping-pipeline-stage2 -n scraping-pipeline
```

Use `scraping-pipeline-delta-ingestor` only when a Kafka wedge passed every gate. If the release name is not `scraping-pipeline`, substitute the live `metadata.name` and namespace. Do not restart both.

7. Write `restart` with the command and the status result.
8. Wait the alert's `for` (15m). Write `verify`: firing or not, and whether pods are Ready. Then stop. Do not run step 6 again for this incident.

The ingestor commits offsets only after the Delta batch commits. A crash before that offset commit can re-deliver a batch. It does not skip one ([main.rs](../../Scraping_project/kafka-delta-ingest/src/main.rs)). That duplicate is why a Ready ingestor is not restarted. Do not change `autoOffsetReset` (chart default `earliest`) as part of this procedure.

## Out of scope

- A Kubernetes controller, operator, CronJob, Helm value, chart template, or workflow.
- Any claim that those objects exist. They do not.
- Editing manifests, alerts, recording rules, dashboards, `config.yml`, or compose.
- A fourth SLO, a crawl-freshness number, a minute-valued Kafka lag, or a burn-rate multiplier.
- Restarting because `QueueBacklog` is over 10000, or because `stage2_queue_pending` is high. Pending rows are not an SLO ([ops assistant](780-ops-assistant.md)).
- Scaling, pausing producers, deleting PVCs, or a chart upgrade.
- An automatic approval mode. Manual is the only mode this design defines.
