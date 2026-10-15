{{/*
Expand the name of the chart.
*/}}
{{- define "scraping-pipeline.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "scraping-pipeline.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "scraping-pipeline.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "scraping-pipeline.labels" -}}
helm.sh/chart: {{ include "scraping-pipeline.chart" . }}
{{ include "scraping-pipeline.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "scraping-pipeline.selectorLabels" -}}
app.kubernetes.io/name: {{ include "scraping-pipeline.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "scraping-pipeline.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "scraping-pipeline.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Exposure / durability guardrails (#176, #233, #451). Returns a newline-separated
list of problems. validate.yaml fails the render on them when
.Values.profile is "production"; NOTES.txt prints them as warnings otherwise.
*/}}
{{- define "scraping-pipeline.guardrailProblems" -}}
{{- $problems := list -}}
{{- if .Values.kafka.enabled -}}
{{- $rf := int (.Values.kafka.config.defaultReplicationFactor | default 1) -}}
{{- $isr := int (.Values.kafka.config.minInsyncReplicas | default 1) -}}
{{- if lt $rf 2 -}}
{{- $problems = append $problems (printf "kafka.config.defaultReplicationFactor=%d: one broker disk loss loses topic data; use >=2 (3 recommended)" $rf) -}}
{{- end -}}
{{- if and (gt $rf 1) (ge $isr $rf) -}}
{{- $problems = append $problems (printf "kafka.config.minInsyncReplicas=%d must be below defaultReplicationFactor=%d or acks=all writes stop when one broker is down" $isr $rf) -}}
{{- end -}}
{{- end -}}
{{- if and .Values.grafana.enabled (eq (.Values.grafana.service.type | default "ClusterIP") "LoadBalancer") (not .Values.grafana.service.allowPublicLoadBalancer) -}}
{{- $problems = append $problems "grafana.service.type=LoadBalancer exposes the Grafana admin UI publicly; use ClusterIP + TLS ingress, or set grafana.service.allowPublicLoadBalancer=true behind SSO/IP allowlist" -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not .Values.ingress.tls) -}}
{{- $problems = append $problems "ingress.enabled without ingress.tls serves Grafana logins over plain HTTP; add a TLS secret (see values-prod.yaml)" -}}
{{- end -}}
{{- if not .Values.networkPolicy.breakGlass -}}
{{- if not .Values.networkPolicy.enabled -}}
{{- $problems = append $problems "networkPolicy.enabled=false leaves Redis, Kafka and Postgres open to the namespace; set networkPolicy.enabled=true (values-prod.yaml) or networkPolicy.breakGlass=true for a short debug session (#190)" -}}
{{- end -}}
{{- end -}}
{{- $extHost := include "scraping-pipeline.redisExternalHost" . -}}
{{- $extCIDRs := list -}}
{{- if .Values.redis.external -}}
{{- $extCIDRs = .Values.redis.external.egressCIDRs | default (list) -}}
{{- end -}}
{{- if not $extHost -}}
{{- $problems = append $problems "redis.external.host is empty: production must point REDIS_HOST at an external Redis or Sentinel/HA endpoint (redis.external.host) and set redis.enabled=false. The chart replica is a single node; AOF can recover data after the node returns, and it does not keep the queue available during node loss (#216)" -}}
{{- end -}}
{{- if .Values.redis.enabled -}}
{{- $problems = append $problems "redis.enabled=true still deploys the single-replica StatefulSet; set redis.enabled=false and redis.external.host for production (#216)" -}}
{{- end -}}
{{- if and $extHost .Values.networkPolicy.enabled (not .Values.networkPolicy.breakGlass) (not $extCIDRs) -}}
{{- $problems = append $problems "redis.external.egressCIDRs is empty while networkPolicy is on: default-deny drops traffic to the external Redis endpoint; set the endpoint CIDR(s) (#216)" -}}
{{- end -}}
{{- join "\n" $problems -}}
{{- end }}

{{/*
Hard errors in any profile: configurations that cannot work.
*/}}
{{- define "scraping-pipeline.configErrors" -}}
{{- $errors := list -}}
{{- if .Values.kafka.enabled -}}
{{- $rf := int (.Values.kafka.config.defaultReplicationFactor | default 1) -}}
{{- $brokers := int (.Values.kafka.replicas | default 1) -}}
{{- if gt $rf $brokers -}}
{{- $errors = append $errors (printf "kafka.config.defaultReplicationFactor=%d is greater than kafka.replicas=%d; topic creation would fail" $rf $brokers) -}}
{{- end -}}
{{- end -}}
{{- $redisHost := include "scraping-pipeline.redisExternalHost" . -}}
{{- if or (contains "://" $redisHost) (contains ":" $redisHost) (contains "/" $redisHost) -}}
{{- $errors = append $errors "redis.external.host must be a hostname only; set the port on redis.external.port. Do not use a redis:// URL or REDIS_URL (#216)" -}}
{{- end -}}
{{- if and .Values.redis.external .Values.redis.external.egressCIDRs (not (kindIs "slice" .Values.redis.external.egressCIDRs)) -}}
{{- $errors = append $errors "redis.external.egressCIDRs must be a list of CIDRs (#216)" -}}
{{- end -}}
{{- $resErr := include "scraping-pipeline.resourceErrors" . -}}
{{- if $resErr -}}
{{- $errors = append $errors $resErr -}}
{{- end -}}
{{- if not (kindIs "slice" (.Values.networkPolicy.crawlProfiles | default (list))) -}}
{{- $errors = append $errors "networkPolicy.crawlProfiles must be a list (#677)" -}}
{{- else -}}
{{- range .Values.networkPolicy.crawlProfiles | default (list) -}}
{{- $profileName := .name | default "" | toString -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $profileName) -}}
{{- $errors = append $errors (printf "networkPolicy crawl profile name %q must be a lowercase DNS label (#677)" $profileName) -}}
{{- end -}}
{{- if not (kindIs "slice" (.allowedDomains | default (list))) -}}
{{- $errors = append $errors (printf "networkPolicy crawl profile %s allowedDomains must be a list of hostnames (#677)" $profileName) -}}
{{- else -}}
{{- range .allowedDomains | default (list) -}}
{{- if not (regexMatch "^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$" (. | toString)) -}}
{{- $errors = append $errors (printf "networkPolicy allowedDomain %q is not a hostname. No scheme, port, path, or wildcard. NetworkPolicy cannot match a DNS name; put resolved IPv4 CIDRs in egressCIDRs (#677)" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not (kindIs "slice" (.egressCIDRs | default (list))) -}}
{{- $errors = append $errors (printf "networkPolicy crawl profile %s egressCIDRs must be a list of IPv4 CIDRs (#677)" $profileName) -}}
{{- else -}}
{{- range .egressCIDRs | default (list) -}}
{{- if not (regexMatch "^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[1-2][0-9]|3[0-2])$" (. | toString)) -}}
{{- $errors = append $errors (printf "networkPolicy egress CIDR %q is not an IPv4 CIDR. NetworkPolicy cannot match a DNS name (#677)" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if .Values.networkPolicy.profileAllowlist -}}
{{- if not (include "scraping-pipeline.crawlProfileEgressCIDRs" .) -}}
{{- $errors = append $errors "networkPolicy.profileAllowlist=true but no crawlProfiles egressCIDRs are set. Resolve allowedDomains to IPv4 CIDRs first. An empty allowlist with default-deny drops every crawl. UDP/TCP 53 to kube-dns stays a separate rule and does not allow HTTP (#677)" -}}
{{- end -}}
{{- end -}}
{{- join "\n" $errors -}}
{{- end }}

{{/*
#1104: every enabled workload must set cpu and memory requests and limits.
Returns one line, or empty when the chart is complete. PVCs are storage only
and are not checked here. The kafka-topics Job hardcodes its own resources.
*/}}
{{- define "scraping-pipeline.resourceErrors" -}}
{{- $missing := list -}}
{{- $workloads := list
  (dict "name" "redis" "enabled" .Values.redis.enabled "resources" .Values.redis.resources)
  (dict "name" "postgresql" "enabled" .Values.postgresql.enabled "resources" .Values.postgresql.resources)
  (dict "name" "zookeeper" "enabled" .Values.zookeeper.enabled "resources" .Values.zookeeper.resources)
  (dict "name" "kafka" "enabled" .Values.kafka.enabled "resources" .Values.kafka.resources)
  (dict "name" "prometheus" "enabled" .Values.prometheus.enabled "resources" .Values.prometheus.resources)
  (dict "name" "alertmanager" "enabled" .Values.alertmanager.enabled "resources" .Values.alertmanager.resources)
  (dict "name" "grafana" "enabled" .Values.grafana.enabled "resources" .Values.grafana.resources)
  (dict "name" "scrapyApp" "enabled" .Values.scrapyApp.enabled "resources" .Values.scrapyApp.resources)
  (dict "name" "stage2Worker" "enabled" .Values.stage2Worker.enabled "resources" .Values.stage2Worker.resources)
  (dict "name" "stage3Worker" "enabled" .Values.stage3Worker.enabled "resources" .Values.stage3Worker.resources)
  (dict "name" "stage4Worker" "enabled" .Values.stage4Worker.enabled "resources" .Values.stage4Worker.resources)
  (dict "name" "kafkaDeltaIngestor" "enabled" .Values.kafkaDeltaIngestor.enabled "resources" .Values.kafkaDeltaIngestor.resources)
  (dict "name" "metricsExporter" "enabled" .Values.metricsExporter.enabled "resources" .Values.metricsExporter.resources)
  (dict "name" "exporters.redis" "enabled" .Values.exporters.redis.enabled "resources" .Values.exporters.redis.resources)
  (dict "name" "exporters.postgres" "enabled" .Values.exporters.postgres.enabled "resources" .Values.exporters.postgres.resources)
  (dict "name" "exporters.kafkaJmx" "enabled" .Values.exporters.kafkaJmx.enabled "resources" .Values.exporters.kafkaJmx.resources)
  (dict "name" "exporters.statsd" "enabled" .Values.exporters.statsd.enabled "resources" .Values.exporters.statsd.resources)
-}}
{{- range $workloads -}}
{{- if .enabled -}}
{{- $res := .resources | default dict -}}
{{- $cpuReq := dig "requests" "cpu" "" $res -}}
{{- $memReq := dig "requests" "memory" "" $res -}}
{{- $cpuLim := dig "limits" "cpu" "" $res -}}
{{- $memLim := dig "limits" "memory" "" $res -}}
{{- if or (not $cpuReq) (not $memReq) (not $cpuLim) (not $memLim) -}}
{{- $missing = append $missing .name -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if $missing -}}
enabled workloads missing cpu and memory requests and limits: {{ join ", " $missing }} (#1104)
{{- end -}}
{{- end -}}

{{/*
#216: external Redis hostname, or empty when the chart replica is in use.
Hostname only. Callers map it to REDIS_HOST. Never a redis:// URL.
*/}}
{{- define "scraping-pipeline.redisExternalHost" -}}
{{- if .Values.redis.external }}{{ .Values.redis.external.host | default "" }}{{ end -}}
{{- end -}}

{{/*
REDIS_HOST: external hostname when set, otherwise the in-cluster Service.
*/}}
{{- define "scraping-pipeline.redisHost" -}}
{{- $ext := include "scraping-pipeline.redisExternalHost" . -}}
{{- if $ext -}}
{{- $ext -}}
{{- else -}}
{{- printf "%s-redis" (include "scraping-pipeline.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
REDIS_PORT: redis.external.port when an external host is set, else the Service port.
*/}}
{{- define "scraping-pipeline.redisPort" -}}
{{- if include "scraping-pipeline.redisExternalHost" . -}}
{{- if .Values.redis.external }}{{ .Values.redis.external.port | default .Values.redis.service.port }}{{ else }}{{ .Values.redis.service.port }}{{ end -}}
{{- else -}}
{{- .Values.redis.service.port -}}
{{- end -}}
{{- end -}}

{{/*
Extra egress to redis.external.egressCIDRs. Empty when no CIDRs are set.
NetworkPolicy cannot select a DNS name (#216).
*/}}
{{- define "scraping-pipeline.redisExternalEgress" -}}
{{- $cidrs := list -}}
{{- $port := .Values.redis.service.port -}}
{{- if .Values.redis.external -}}
{{- $cidrs = .Values.redis.external.egressCIDRs | default (list) -}}
{{- $port = .Values.redis.external.port | default .Values.redis.service.port -}}
{{- end -}}
{{- if $cidrs -}}
- to:
{{- range $cidrs }}
  - ipBlock:
      cidr: {{ . | quote }}
{{- end }}
  ports:
  - protocol: TCP
    port: {{ $port }}
{{- end -}}
{{- end -}}

{{/*
#677: unique IPv4 CIDRs from networkPolicy.crawlProfiles, one per line.
Empty when none are set. Hostnames are not accepted.
*/}}
{{- define "scraping-pipeline.crawlProfileEgressCIDRs" -}}
{{- $cidrs := list -}}
{{- range .Values.networkPolicy.crawlProfiles | default (list) -}}
{{- if kindIs "slice" (.egressCIDRs | default (list)) -}}
{{- range .egressCIDRs | default (list) -}}
{{- if and . (not (has . $cidrs)) -}}
{{- $cidrs = append $cidrs . -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- join "\n" $cidrs -}}
{{- end -}}

