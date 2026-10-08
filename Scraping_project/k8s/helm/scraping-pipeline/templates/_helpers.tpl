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
{{- join "\n" $errors -}}
{{- end }}

