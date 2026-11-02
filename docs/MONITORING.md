# Monitoring

## Health

| Endpoint | Meaning |
| --- | --- |
| `https://<host>/health` | backend process alive |
| `https://<host>/readiness` | database reachable, migrations applied, Redis reachable |
| gateway `:8081/health` (internal) | listeners, connections, stored/duplicate/late/outlier counts, persist failures |
| processor `:8082/health` (internal) | stream consumer stats, live-update lag; 503 when Redis or the database is down |

`docker compose ps` uses these as container health checks. Super Admin → **System health** shows services, connections
per listener, packets per second, decode errors by protocol, ingestion lag, stream backlog, Celery queue, database
size and WebSocket clients.

## Metrics

| Source | Endpoint | Highlights |
| --- | --- | --- |
| backend | `backend:8000/metrics` (Bearer `METRICS_TOKEN`) | `gps_http_requests_total{route,status}`, `gps_http_request_duration_seconds`, `gps_websocket_clients`, `gps_devices{status}`, `gps_queue_items{queue}` |
| gateway | `gateway:8081/metrics` | `gpsgw_connections`, `gpsgw_packets_total`, `gpsgw_records_total{outcome}`, `gpsgw_protocol_errors_total{protocol,code}`, `gpsgw_persist_failures_total`, `gpsgw_ingest_lag_seconds` |
| processor | `processor:8082/metrics` | `gpsproc_entries_total`, `gpsproc_last_lag_ms`, `gpsproc_errors_total`, `gpsproc_caught_up_total`, `gpsproc_alerts_touched_total` |

## Prometheus and Grafana

```bash
echo -n "$METRICS_TOKEN" > monitoring/prometheus/metrics_token
docker compose --profile monitoring up -d prometheus grafana
```

Grafana listens on 127.0.0.1:3000 only (use an SSH tunnel). The *GPS platform overview* dashboard is provisioned.
Alert rules (`monitoring/prometheus/alerts.yml`):

| Alert | Fires when |
| --- | --- |
| ServiceDown | a scrape target is down for 2 min |
| PositionsNotStored | any persist failure in 5 min (trackers aren't getting ACKs) |
| NoIngestion | trackers connected but nothing stored for 10 min |
| HighIngestLag | p95 device-to-server lag above 2 min for 10 min |
| ProcessorFallingBehind | live updates more than 5 s behind for 5 min |
| ProcessorErrors | more than 5 failed batches in 10 min |
| QueueBacklog | notifications, webhooks or Celery above 500 items for 15 min |
| ApiErrors | more than 2% of API requests are 5xx for 10 min |
| ManyProtocolErrors | sustained malformed input on a protocol |

Route them with Grafana alerting (contact points: email, Slack, SMS gateway) or add Alertmanager.

## Errors

Set `SENTRY_DSN` to report exceptions from the backend, gateway and processor (no personal data is sent).

## Logs

All services log JSON lines to stdout (`docker compose logs -f <service>`), rotated at 20 MB × 5 in production.
IMEIs are masked in gateway logs.
