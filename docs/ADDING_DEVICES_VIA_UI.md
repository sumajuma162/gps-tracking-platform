# Adding trackers through the UI

## Case 1: a tracker of a model the platform already knows (e.g. FMC920, GT06)

1. **Trackers → Add tracker.** Pick the model, enter the IMEI (checked: 15 digits, Luhn, not used anywhere on the
   platform), optional SIM and name.
2. **Install in a vehicle** (or create one).
3. **Setup instructions** show your server, port and APN filled in; configure the tracker.
4. **Connection test** turns green as the tracker registers, connects, sends data, gets a GPS fix and its readings
   are understood. The gateway accepts a new tracker within seconds; no restart.

Many at once: **Trackers → Import** a CSV (preview before saving).

## Case 2: a tracker that showed up on its own

Super Admin → **Unregistered devices** lists identifiers that tried to connect, with protocol, port and time.
**Register** one into an organization in a single step; typos are easy to spot next to the real IMEI.

## Case 3: a new model that speaks a supported protocol

Super Admin → **Device catalog**: clone the closest model (e.g. FMC920 → FMC130), set its capabilities (odometer,
ignition source), trip detection mode, time offset and setup instructions. In the **IO mapping editor**, map IO ids to
fields (ignition, voltages, odometer...), sensors or events, and check the live preview against real data from one of
its trackers. Every save is a version you can roll back.

## Case 4: a simple tracker with its own JSON or text format (no code)

Super Admin → **Tracker formats → Add format**:

1. **Kind:** *JSON over HTTP* or *Delimited text (TCP/UDP)*.
2. **Definition:** where each value is. JSON uses paths (`gps.lat`, `fixes.0.speed`); text uses column numbers from 0.
   Time format (`iso8601`, `unix`, `unix_ms` or a pattern like `%d%m%y%H%M%S`), speed unit, ignition and voltage
   fields, custom sensors, authentication (`token` or `identifier`) and the ACK string.
3. **Test parse:** paste a real message; the table shows what each record becomes, or which field is wrong.
4. Use the format: on a **device model** (clone *Generic HTTP JSON*), and for text, on a **listener port** in the
   5100-5109 range (**Listener ports**). HTTP trackers post to `http://<host>:5055/<format short name>`.
5. Register trackers as in case 1. For token auth, open the tracker's **HTTP token** to issue its token (shown once).

## Case 5: a new binary protocol

That needs a decoder plugin: see `DEVICE_INTEGRATION.md`. Once installed, the protocol appears in the catalog and
cases 1-3 apply.
