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
{{- if .Values.services.postgresql.enabled }}
{{- include "infrawatch.fullname" . }}-postgres
{{- else }}
{{- required "externalServices.postgresql.host is required when services.postgresql.enabled=false" .Values.externalServices.postgresql.host }}
{{- end }}
{{- end }}

{{/*
PostgreSQL port
*/}}
{{- define "infrawatch.pgPort" -}}
{{- if .Values.services.postgresql.enabled }}5432{{- else }}{{ .Values.externalServices.postgresql.port }}{{- end }}
{{- end }}

{{/*
PostgreSQL database
*/}}
{{- define "infrawatch.pgDatabase" -}}
{{- if .Values.services.postgresql.enabled }}{{ .Values.services.postgresql.auth.database }}{{- else }}{{ .Values.externalServices.postgresql.database }}{{- end }}
{{- end }}

{{/*
PostgreSQL username
*/}}
{{- define "infrawatch.pgUser" -}}
{{- if .Values.services.postgresql.enabled }}{{ .Values.services.postgresql.auth.username }}{{- else }}{{ .Values.externalServices.postgresql.username }}{{- end }}
{{- end }}

{{/*
Valkey URL — internal service or external
*/}}
{{- define "infrawatch.valkeyUrl" -}}
{{- if .Values.services.valkey.enabled }}
{{- if .Values.services.valkey.password }}
redis://:{{ .Values.services.valkey.password }}@{{ include "infrawatch.fullname" . }}-valkey:6379
{{- else }}
redis://{{ include "infrawatch.fullname" . }}-valkey:6379
{{- end }}
{{- else }}
{{- required "externalServices.valkey.url is required when services.valkey.enabled=false" .Values.externalServices.valkey.url }}
{{- end }}
{{- end }}
