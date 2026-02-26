{{/*
Common labels
*/}}
{{- define "infrawatch.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Selector labels for a component
*/}}
{{- define "infrawatch.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full name with release
*/}}
{{- define "infrawatch.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Service account name
*/}}
{{- define "infrawatch.serviceAccountName" -}}
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- include "infrawatch.fullname" . }}
{{- end }}
{{- end }}

{{/*
PostgreSQL host — internal service or external
*/}}
{{- define "infrawatch.pgHost" -}}
{{- if .Values.postgresql.enabled }}
{{- include "infrawatch.fullname" . }}-postgres
{{- else }}
{{- required "externalPostgresql.host is required when postgresql.enabled=false" .Values.externalPostgresql.host }}
{{- end }}
{{- end }}

{{/*
PostgreSQL port
*/}}
{{- define "infrawatch.pgPort" -}}
{{- if .Values.postgresql.enabled }}5432{{- else }}{{ .Values.externalPostgresql.port }}{{- end }}
{{- end }}

{{/*
PostgreSQL database
*/}}
{{- define "infrawatch.pgDatabase" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.auth.database }}{{- else }}{{ .Values.externalPostgresql.database }}{{- end }}
{{- end }}

{{/*
PostgreSQL username
*/}}
{{- define "infrawatch.pgUser" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.auth.username }}{{- else }}{{ .Values.externalPostgresql.username }}{{- end }}
{{- end }}

{{/*
Valkey URL — internal service or external
*/}}
{{- define "infrawatch.valkeyUrl" -}}
{{- if .Values.valkey.enabled }}
{{- if .Values.valkey.password }}
redis://:{{ .Values.valkey.password }}@{{ include "infrawatch.fullname" . }}-valkey:6379
{{- else }}
redis://{{ include "infrawatch.fullname" . }}-valkey:6379
{{- end }}
{{- else }}
{{- required "externalValkey.url is required when valkey.enabled=false" .Values.externalValkey.url }}
{{- end }}
{{- end }}
