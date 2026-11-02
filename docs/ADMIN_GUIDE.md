# Administration guide

## Organization administrators

### Users and roles

**Users → Invite**: email and role. Built-in roles:

| Role | Can |
| --- | --- |
| Organization Admin | everything in the organization, including users, roles, settings and integrations |
| Fleet Manager | vehicles, trackers, drivers, geofences, alerts, reports, maintenance |
| Monitoring Officer | live map, alerts (acknowledge and resolve), history, reports |
| Viewer | read-only live map, history and reports |
| Auditor | audit log and reports, read-only |
| Driver | the driver portal only: **My vehicle** (the vehicle assigned to their driver profile, its live status and their trips of the last 7 days), and notifications |

**Roles** lets you create custom roles from permissions. Permissions apply to the whole organization; there is no
per-vehicle or per-group restriction of what a role sees (alert rules and reports can be scoped to vehicles or
groups).

For the driver portal, link the driver's record to their user account (**Drivers → edit → Driver portal login**) and assign the
driver to a vehicle.

### Fleet

Vehicles (with groups), trackers (**Add tracker** wizard, CSV import, setup instructions, connection test,
diagnostics, raw packet logging, commands), drivers (assignments with history) and SIM cards (expiry alerts).

### Alert rules

**Alerts → Rules**: type (overspeed, geofence entry/exit/dwell, offline, GPS lost, unauthorized movement, ignition,
low battery, power cut, tampering, jamming, towing, crash, harsh driving, long idle, long stop, maintenance due, SIM
expiry), threshold, severity, vehicles or groups, schedule (days and hours), recipients (roles or people) and cooldown.

### Settings

**Settings**: time zone, language, units, every detection threshold (offline after, trip start speed, minimum trip,
stop and idle times, drift radius, overspeed warning and critical speeds, geofence confirmation), map start
position, how long to keep location history (`retention_days`, minimum 30) and the minimum severity for alert emails.
Each value shows the platform default; **Use default** removes your override. Changes are audited and reach the
processor within seconds.

### Integrations

- **Webhooks:** your URL (https, public), events, then copy the signing secret (shown once). **Send test** and the
  delivery log help debugging. Verifying signatures: `API.md`.
- **API keys:** name, permissions (only ones you have), optional expiry. Copy the key (shown once). Revoke at any
  time. Keys act as you, limited to their permissions, in this organization only.

### Audit log

Every change, sign-in, export and command, with who, when, IP and details. Search by text, action, user, resource,
IP and dates.

## Super Administrators

- **Organizations**: create, suspend, organization admins.
- **Device catalog**: manufacturers, protocols, models (clone, capabilities, trip detection mode, time offset,
  generic format, setup instructions), IO mapping profiles with live preview and versions.
- **Tracker formats**: define JSON-over-HTTP and delimited-text formats with test parse (`ADDING_DEVICES_VIA_UI.md`).
- **Listener ports**: which protocol listens where; changes apply within seconds. Ports must also be published
  (`5100-5109` reserved for this) and open in the firewall.
- **Unregistered devices**: identifiers that tried to connect; register them into an organization.
- **Settings → Platform defaults**: defaults every organization inherits, the map tile provider and geocoder.
- **System health**: services, connections, throughput, decode errors, ingestion lag, queues, database size.

## Routine tasks

| When | Task |
| --- | --- |
| Daily | Glance at System health and Grafana alerts |
| Weekly | Unregistered devices; failing webhooks (switch off after 50 failures in a row) |
| Monthly | Restore drill (`BACKUP_RESTORE.md`); review users and API keys; update the server (`PRODUCTION_DEPLOYMENT.md`) |
| Quarterly | Load test on staging after big growth (`SCALING.md`) |
