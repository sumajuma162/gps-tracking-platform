# Scaling

## Targets (master spec A15)

5,000 connected trackers, 1,000 packets/s sustained, live updates within 2 s, on 4 vCPU / 8 GB, single server.

## What was measured

**Environment:** the build sandbox, **1 vCPU and 4 GB RAM shared** by PostgreSQL 16 + TimescaleDB, Redis, the
gateway, the processor and the load generator itself. Load mode: `python -m gpssim.load` (real Teltonika Codec 8E
over TCP). Steady state after a 50 s warm-up. 2026-10-04.

| Run | Connected | Throughput | ACK latency p50 / p95 / p99 | Live-update lag (processor) |
| --- | --- | --- | --- | --- |
| 2,000 trackers, 3 records per packet every 15 s (400 records/s offered, 133 packets/s) | 2,000, 0 errors | 400 records/s sustained | 37 ms / 754 ms / 1.76 s | median 21 ms, max 1.6 s |
| 3,000 trackers, 900 records/s offered | 2,892 | saturated at ~400 records/s | seconds (overloaded) | seconds |
| 2,000 trackers before the one-statement write (same load) | 2,000 | 320 records/s | 6.75 s p50 | median 4.6 s |

**Not measured:** the 4 vCPU / 8 GB target. On this sandbox CPU was split roughly equally between the gateway, the
processor and PostgreSQL, so the bottleneck is CPU, not locking or I/O. Run the load test on the production-sized
server before go-live and record the result here.

## Running the load test

```bash
docker compose exec backend python manage.py create_load_devices --count 5000 --organization <slug> --force   # staging
PYTHONPATH=gateway:simulator python -m gpssim.load --host <server> --port 5027 --devices 5000 \
  --interval 15 --records-per-packet 3 --duration 600 --warmup 120 --ramp 200 \
  --processor-health http://<processor>:8082/health
```

Run the generator from another machine (it uses CPU too); raise `GATEWAY_MAX_CONNECTIONS_PER_IP` on staging when all
trackers come from one generator IP. Watch Grafana while it runs. Delete the load devices afterwards.

## Where it scales next

| Part | How |
| --- | --- |
| Gateway | Stateless apart from open connections: run more instances behind a TCP load balancer (or several ports), raise `GATEWAY_MAX_CONNECTIONS`. Per-packet cost is one SQL statement and one XADD. |
| Processor | Consumer group: run several consumers; records are ordered per device (shard by device). |
| PostgreSQL | Most important: SSD, `shared_buffers` ~25% of RAM, compression after 7 days (on), chunk interval 1 day. Then a dedicated database server, then read replicas for reports and history. |
| Redis | The positions stream is capped at about 200,000 entries (the database is the source of truth); AOF every second. Move to its own server before the database does. |
| Backend / Celery | More Uvicorn workers and Celery concurrency; they're stateless. |
| Live map | The browser receives only its organization's (and scope's) updates, batched per sweep. |

## Capacity rule of thumb

Positions: **about 600 bytes per record uncompressed, including indexes** (measured: 240,000 records in 146 MB).
TimescaleDB compression after 7 days typically shrinks this several-fold (not measured here; check
`SELECT * FROM chunk_compression_stats('positions')` on your data). 5,000 trackers at one record every 30 s ≈
14.4 million records/day ≈ 8.6 GB/day before compression. Plan retention (`retention_days`) and disk accordingly.
