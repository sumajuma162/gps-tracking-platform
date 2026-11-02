# Troubleshooting

## Services

| Symptom | Check |
| --- | --- |
| A container keeps restarting | `docker compose logs --tail=100 <service>`; `docker compose ps` health column |
| `/readiness` says not ready | database down (`docker compose logs postgres`), migrations pending (backend logs on start), Redis password mismatch (`REDIS_PASSWORD`) |
| Backend 500 on every page | `DJANGO_SECRET_KEY` missing, `ALLOWED_HOSTS` doesn't include the host, `CSRF_TRUSTED_ORIGINS` missing `https://` |
| Sign-in works, then logs out immediately | `REFRESH_COOKIE_SECURE=1` on plain HTTP (set `0` for local HTTP), or a clock far off |
| Emails don't arrive | `EMAIL_*` settings; Celery worker logs; notifications page shows queued/failed |
| Reports stuck "queued" | Celery worker running? `docker compose logs celery-worker` |
| Map is grey | `MAP_TILE_URL` empty or blocked by the CSP (tile host must be https) |

## Trackers

| Symptom | Check |
| --- | --- |
| Tracker never appears | **Unregistered devices** (typo in the IMEI?); ports open (firewall, NAT, security group); tracker configuration (`TELTONIKA_FMC920.md` decision tree) |
| Connects but no data | gateway logs for `protocol error` with the code (`crc_mismatch`, `bad_codec`, `oversized`); codec 8/8E selected |
| Positions missing on the map but stored | GPS fix: records with 0 satellites or 0,0 are stored with `gps_valid=false` and not drawn |
| Marker didn't move for a backlog upload | expected: the live marker shows the newest record; backlog records are flagged late and recomputed into trips |
| Trip distance too low or too high | device model capabilities (odometer?), IO mapping of the odometer, drift radius and stop thresholds in Settings |
| Gateway logs `persistence failed` | database down or full; trackers get no ACK and resend; check `docker compose logs postgres`, disk space |
| `connection_limit` in metrics | many trackers behind one IP (an APN NAT): raise `GATEWAY_MAX_CONNECTIONS_PER_IP` |
| GT06 time hours off | set the model's **Time offset (minutes)** |
| HTTP tracker gets 401 | wrong or rotated token, identifier in the body doesn't match the token's tracker, or the tracker isn't registered |
| HTTP tracker gets 400 | the response names the field that didn't parse; test the message with **Tracker formats → Test parse** |

## Live updates

| Symptom | Check |
| --- | --- |
| Map doesn't update without reload | WebSocket blocked by a proxy (Nginx must pass `Upgrade`), processor running (`/health` on 8082), Redis reachable |
| Updates seconds late | processor lag in Grafana (`gpsproc_last_lag_ms`); CPU on the server |
| Alerts not notified | rule active and in schedule, cooldown, recipient roles, the user's notification preferences and quiet hours (critical bypasses quiet hours), delayed alerts (from late data) are never notified live |

## Data

| Symptom | Check |
| --- | --- |
| A user sees nothing | membership active, role permissions, vehicle scope of the role |
| Settings change has no effect | the processor caches settings up to 60 s (changes are announced immediately); status thresholds apply to new records |
| Disk filling up | retention (`retention_days`), compression jobs (`SELECT * FROM timescaledb_information.jobs`), raw packet logging left on |
