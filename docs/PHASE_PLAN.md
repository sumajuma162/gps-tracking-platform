<!-- Exported from the Claude Doc "GPS Tracking Platform — Master Build Specification v2" (https://claude.ai/code/artifact/94741c6e-cdd6-411e-9076-14888fc4ddbd), revision 24, on 2026-10-02. Edit the Doc, then re-export. -->

# Part B — Phase plan

Build in 13 phases, in order. A phase is done only when every item in its "Done when" column has been proven by a command or test whose output is recorded in `PROGRESS.md`. The device path (phases 3–4) comes before most of the UI, because ingestion is the hardest part to get right.

| Phase | Scope | Done when |
|---|---|---|
| 0. Inspect and scaffold | Inspect any existing code; save `MASTER_SPEC.md`; create `PROGRESS.md` and `DECISIONS.md`; repository skeleton; Docker Compose base and dev files with all services; split settings; `.env.example`; Makefile and PowerShell scripts; CI skeleton | `docker compose up --build` brings every service to healthy; `/health` responds; CI runs green |
| 1. Foundation | Users, organizations, roles and permissions, JWT with cookie refresh, lockout, throttling, `TenantModel` and `TenantViewSet`, RLS, audit log base; frontend shell with landing page, login, layout, role-filtered menu, en/sw i18n | Auth tests pass; the automatic tenant-isolation test runs over all routes; login works in the browser |
| 2. Catalog and fleet | Device catalog (manufacturers, protocols, models, IO profiles, listener ports, command templates), devices, SIMs, vehicles, groups, drivers, assignments with history, `positions` hypertable and `device_state`; CRUD pages; bulk import | CRUD and permission tests pass; migrations apply cleanly from empty; the hypertable, compression and indexes exist |
| 3. Gateway and Teltonika | TCP/UDP listeners from the database config, hot-reload registry, Codec 8/8E/12, UDP framing, CRC, ACK after persistence, dedup, validation, IO mapper, raw packets, unregistered inbox, limits, metrics, fixtures and fuzz tests | All gateway tests in A16 pass; fixture packets sent over real TCP and UDP land in `positions` with correct values |
| 4. Onboarding UI and simulator | Add Device wizard, connection test panel over WebSocket, setup instructions, Unregistered Devices page, IO profile editor with live preview, model cloning; simulator with Teltonika clients and the A16 scenarios | Scenario 2, steps 1, 2, 3 and 5 pass |
| 5. Live tracking | Processor service with stream consumer and checkpoints, `device_state`, WebSocket tickets, `/ws/tracking/`, live map, dashboard cards and fleet map, offline/no-GPS detection | A simulated vehicle moves on the live map with under 2 s latency; isolation holds on WebSockets |
| 6. History and playback | Route API with downsampling, route history page with summary, stops and events, playback with all controls | Route tests pass; a 31-day route stays within the point budget |
| 7. Analysis engine | Trips (3 modes), stops, idling, overspeed episodes, geofences with hysteresis and the geofence editor, driving behavior, drift and outlier filters, late-data recompute | All processing tests in A16 pass, including backlog scenarios |
| 8. Alerts, notifications and commands | Alert rules, deduplication, cooldown, quiet hours, delayed flag, notification center, email channel, prepared SMS/WhatsApp/push interfaces, Codec 12 command queue with verified templates | Alert workflow tests pass; a test command round-trips with the simulator |
| 9. Reports and business modules | All reports, async exports, scheduled reports, analytics charts from continuous aggregates, share links, webhooks, API keys, maintenance, settings pages, audit log UI, global search, system health page | Report and export tests pass; Scenario 1 passes end to end |
| 10. Extensibility | GT06 plugin, generic configurable protocol (JSON over HTTP and delimited text) with test-parse UI, data forwarder framework (prepared) | Scenario 2 step 4 and Scenario 3 pass |
| 11. Production hardening | Production Compose, Nginx with TLS and security headers, Prometheus/Grafana, Sentry, backups with PITR and restore drill, load test, dependency and image scanning | Scenario 4 passes; load test results are recorded; `check --deploy` is clean |
| 12. Documentation and acceptance | All A17 documents and diagrams, final full Playwright run, completion report | All A18 scenarios pass; the completion report is delivered |

# Part C — Phase kickoff prompts

Start a fresh agent session for each phase and paste the matching prompt. For Phase 0, attach or paste Part A and Part B, because the repository does not have them yet.

### First session (Phase 0)

```
You are building the GPS tracking platform described in the attached Master Specification (Part A) and Phase Plan (Part B).

1. Inspect the current repository. If code exists, list what works and what is missing before changing anything. Do not destroy working functionality.
2. Save Part A as docs/MASTER_SPEC.md and Part B as docs/PHASE_PLAN.md. Create docs/PROGRESS.md and docs/DECISIONS.md.
3. Implement PHASE 0 only, exactly as defined in Part B, following the execution rules in A1.
4. Prove the "Done when" criteria by running the commands, and record the commands and their output summaries in PROGRESS.md.
5. Stop when Phase 0 is done. Report COMPLETED / CURRENTLY IMPLEMENTING / REMAINING and any decision that needs my input.

Do not start Phase 1 in this session.
```

### Every later phase (replace N and the phase name)

```
Continue the GPS tracking platform. Implement PHASE N — <phase name>.

Before writing code:
- Read docs/MASTER_SPEC.md (especially the sections referenced by this phase), docs/PHASE_PLAN.md, docs/PROGRESS.md and docs/DECISIONS.md.
- Inspect the code that this phase builds on. Confirm that the previous phase's tests still pass; if not, fix them first.

While working:
- Follow the execution rules in A1. No placeholders in this phase's scope.
- Write tests alongside the code. Run them, fix failures, and rerun.

To finish:
- Prove every "Done when" item for Phase N with commands or tests.
- Run the full backend, gateway and frontend test suites, the type checks and `docker compose config`.
- Update PROGRESS.md (COMPLETED / CURRENTLY IMPLEMENTING / REMAINING, commands run, results, known issues) and DECISIONS.md.
- Stop and report. Do not start the next phase.
```

### Recovery prompt (when a phase went wrong)

```
The last phase is not complete. Read docs/PROGRESS.md and run the full test suite and `docker compose up --build`.
List every failure with its root cause before changing code. Fix the root causes, not the symptoms. Do not delete or skip
tests to make them pass. Rerun everything, then update PROGRESS.md with what was broken and how it was fixed.
```

### Review prompt (optional, after phases 3, 7 and 11)

```
Act as an independent reviewer. Do not write new features. Review the code for Phase N against docs/MASTER_SPEC.md:
ingestion correctness (A4), tenant isolation (A8), security (A14) and the prohibited actions (A18).
List concrete defects with file and line references, ordered by severity, then fix the critical and high ones and rerun the tests.
```

# Part D — Human checklist: physical FMC920 test

The AI agent cannot do these steps. Do them after Phase 4, when the gateway and the Add Device wizard exist, and repeat the drive test after Phase 7.

### Before the test

- [ ] Get at least one Teltonika FMC920 and a SIM with an active data plan from a local carrier. Note the carrier's APN, and disable the SIM PIN or note it.
- [ ] Deploy the platform on a VPS with a public IP or DNS name, for example `gps.yourdomain.com`.
- [ ] Open the Teltonika port (default 5027, TCP and UDP) in the VPS firewall and the cloud security group. Confirm it is reachable from outside with `Test-NetConnection gps.yourdomain.com -Port 5027` (PowerShell) or `nc -vz gps.yourdomain.com 5027`.
- [ ] Install Teltonika Configurator on a Windows PC and connect the FMC920 over USB.

### Configure and connect

- [ ] In Configurator, set the APN, the server domain, port 5027, protocol TCP and the data codec (Codec 8 or 8 Extended), then save to the device. Check the parameter names against Teltonika's documentation for your firmware.
- [ ] In the platform, run the **Add Device** wizard with the FMC920's IMEI (printed on the label) and assign it to a test vehicle.
- [ ] Insert the SIM, power the device (or install it in a vehicle), and watch the connection test panel move through each stage to "IO values mapped".
- [ ] If it stalls, follow `TROUBLESHOOTING.md`. Check the Unregistered Devices page (a typo in the IMEI shows up there), SIM data balance, the APN, the firewall, and Configurator's device log.

### Verify behavior

- [ ] Drive a 20–30 minute route that includes a stop of over 3 minutes, an idle with the engine running over 5 minutes, and a pass through a test geofence.
- [ ] Confirm that ignition on/off, speed, voltage and GSM values match reality. If any IO value is wrong, fix it in the IO mapping profile editor (no code needed) and record the correction.
- [ ] Confirm that the trip, stops, idle event, geofence events and alerts appear, and that the route and playback look right.
- [ ] Test a coverage gap: disconnect the GSM antenna or drive through a known dead zone, and confirm the backlog uploads correctly afterwards.
- [ ] Disable the device in the UI and confirm that data stops being accepted. Then re-enable it.

### Feed results back to the agent

- [ ] Export raw packets from the device page and give them to the agent as new test fixtures under `gateway/tests/fixtures/fmc920_real/`.
- [ ] Record the firmware version and any parameter corrections in `TELTONIKA_FMC920.md`.

### Before going live with customers

- [ ] Review your obligations under Tanzania's Personal Data Protection Act, 2022, and inform drivers about tracking.
- [ ] If you serve commercial or public transport operators, check LATRA's current vehicle tracking requirements.
- [ ] Choose a production map tile and geocoding provider, and set it in Settings.
- [ ] Run and verify a backup restore drill.
