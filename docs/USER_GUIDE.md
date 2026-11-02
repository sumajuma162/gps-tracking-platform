# User guide

The web app works in English and Kiswahili (switch under your profile). What you see depends on your role.

## Signing in

Use the email your administrator invited. **Forgot password** sends a reset link (valid 24 h). Under **Profile**
you can change your password, language, time zone, quiet hours and notification preferences, and sign out other
devices.

## Live map

- Every vehicle with its status: moving (green), idling, stopped, offline, no GPS. Markers move as trackers report
  (about a second behind).
- Search by registration, filter by status or group, click a vehicle for its speed, ignition, address, driver,
  voltages and last update. **Follow** keeps the map on it.
- **Share** (if allowed) creates a time-limited public link to one vehicle's live position.

## Dashboard

Vehicles by status, trackers online, today's trips and distance, a fleet map, the status distribution, alerts today
and the latest open alerts, and trackers needing attention (never connected, offline over 24 h, weak signal, low
battery).

## Drivers: My vehicle

Drivers sign in to a simple portal: the vehicle assigned to them, its live status (speed, ignition, last update) and
their trips of the last 7 days. They see nothing else of the fleet.

## Route history

Pick a vehicle and a period (up to the limit your administrator set, 31 days by default). You get the route with
stops, distance, driving and stopped time and top speed. **Play** replays it at up to 900×; the speed chart follows.
Download CSV, GPX or KML if your role allows exports.

## Trips, stops, geofences

**Trips** lists every journey with distance, duration and top speed. **Geofences**: draw circles or polygons; entries,
exits and long stays are recorded and can raise alerts.

## Alerts and notifications

The bell shows new notifications live. **Alerts** lists open alerts; **Acknowledge** to show you're on it,
**Resolve** with a note when done. Repeats of the same alert are counted, not duplicated. "Delayed" alerts come from
data that arrived late (after a coverage gap) and weren't notified live.

## Reports

14 reports (trips, daily movement, distance, speeding, stops, idling, offline, geofences, alerts, drivers, fleet
summary, utilization, device health, maintenance). Filter, view on screen, export CSV, Excel or PDF. Large exports
run in the background and stay downloadable for 24 hours. **Schedules** email a report daily, weekly or monthly.

## Analytics

Trends over 7 to 90 days: distance, trips, utilization, alerts, speeding, idling, tracker uptime, records received.

## Maintenance

Service plans per vehicle (every N km, engine hours or days) show when each is due; you're alerted when one is due
soon or overdue. Record each service with cost and garage; the plan resets.

## Commands (authorized users)

From a tracker's **Commands** panel, send commands such as `getinfo`. They wait until the tracker is online; the
answer appears in the panel.
