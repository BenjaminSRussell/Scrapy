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
{{- if and .Values.metricsExporter.enabled .Values.metricsExporter.mtls.enabled -}}
{{- if .Values.metricsExporter.mtls.issueCerts -}}
{{- if not .Values.metricsExporter.mtls.issuerRef.name -}}
{{- $errors = append $errors "metricsExporter.mtls.enabled with issueCerts requires metricsExporter.mtls.issuerRef.name (a cert-manager Issuer or ClusterIssuer backed by an internal CA). See k8s/README.md (#678)" -}}
{{- end -}}
{{- else -}}
{{- if or (not .Values.metricsExporter.mtls.serverSecretName) (not .Values.metricsExporter.mtls.clientSecretName) -}}
{{- $errors = append $errors "metricsExporter.mtls.enabled with issueCerts=false requires metricsExporter.mtls.serverSecretName and metricsExporter.mtls.clientSecretName (#678)" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and .Values.redis.external .Values.redis.external.egressCIDRs (not (kindIs "slice" .Values.redis.external.egressCIDRs)) -}}
{{- $errors = append $errors "redis.external.egressCIDRs must be a list of CIDRs (#216)" -}}
{{- end -}}
{{- join "\n" $errors -}}
{{- end }}

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
#678: TLS secret the metrics exporter presents, and the CA it uses to check
Prometheus. Empty serverSecretName uses <fullname>-metrics-exporter-tls.
*/}}
{{- define "scraping-pipeline.metricsExporterMtlsServerSecret" -}}
{{- if .Values.metricsExporter.mtls.serverSecretName -}}
{{- .Values.metricsExporter.mtls.serverSecretName | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-metrics-exporter-tls" (include "scraping-pipeline.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
#678: client certificate Prometheus presents on the metrics-exporter scrape.
*/}}
{{- define "scraping-pipeline.metricsExporterMtlsClientSecret" -}}
{{- if .Values.metricsExporter.mtls.clientSecretName -}}
{{- .Values.metricsExporter.mtls.clientSecretName | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-prometheus-scrape-client" (include "scraping-pipeline.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
DNS name Prometheus checks on the exporter certificate. Default is the
in-cluster Service name, which the Certificate also lists.
*/}}
{{- define "scraping-pipeline.metricsExporterMtlsServerName" -}}
{{- if .Values.metricsExporter.mtls.serverName -}}
{{- .Values.metricsExporter.mtls.serverName -}}
{{- else -}}
{{- printf "%s-metrics-exporter.%s.svc" (include "scraping-pipeline.fullname" .) .Release.Namespace -}}
{{- end -}}
{{- end -}}

