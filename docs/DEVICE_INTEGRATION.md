# Device integration

## How a tracker's data travels

`bytes → protocol plugin (framing, CRC, decode) → RawRecord → validation → NormalizedLocation (IO mapping profile of
the device model) → jump filter / late flag → PostgreSQL (one atomic statement) → ACK → Redis stream → processor`.

The core never knows protocol details; plugins never touch the database or network.

## Writing a decoder plugin

A plugin is a class implementing `gpsgw.protocols.base.BaseProtocolDecoder`:

| Member | Purpose |
| --- | --- |
| `name`, `transports`, `supports_commands`, `max_frame_size` | identity and limits |
| `can_handle(first_bytes)` | quick check used in diagnostics |
| `extract_frame(buffer, ctx)` | return a `Frame` (`HANDSHAKE`, `DATA`, `HEARTBEAT`) and remove its bytes, or `None` to wait for more; raise `ProtocolError(code)` for garbage (the connection is closed and counted) |
| `identify_device(frame)` | `DeviceIdentity(identifier)` from a handshake frame |
| `handshake_response(accepted, frame)` | bytes to send after the registry decision (or `None`) |
| `decode(frame, ctx)` | list of `RawRecord` (time, lat, lon, speed, heading, satellites, `io={id: value}`, `gps_valid`) |
| `acknowledge(frame, accepted, ctx)` | bytes to send **after** the records are stored |
| `parse_datagram(data)` (UDP) | identity and frame from one datagram |
| `encode_command` / `parse_command_response` | optional, for tracker commands |

Rules: pure functions of their input (no I/O), never raise anything but `ProtocolError`, bound every length you read.
IO values are your protocol's own ids; the device model's IO mapping profile turns them into fields, sensors and
events, so mappings stay editable in the UI.

Register it through the entry point group in your package metadata:

```toml
[project.entry-points."gps_platform.protocols"]
myproto = "my_package.decoder:MyDecoder"
```

The gateway loads it at start and marks the protocol *implemented* in the catalog. Then add a listener port
(**Listener ports**) and a device model.

The GT06 plugin is a complete example (frames, CRC-ITU, login, location, heartbeat, alarm), added without changing
the gateway core. Tests to copy: `gateway/tests/test_gt06.py` (official sample packets, corrupt frames, partial reads,
Hypothesis fuzzing, a real TCP session).

## Correctness rules every protocol gets for free

ACK only after commit; duplicates ignored (unique `(device_id, device_time, record_hash)`) and still ACKed; records
older than the newest stored are flagged late (live marker never moves back, trips recomputed); 0 satellites, 0,0 or
invalid flag → stored with `gps_valid=false`; coordinates and times outside limits rejected; jumps above 300 km/h
flagged as outliers; packet size, handshake and idle timeouts, per-IP and global connection limits.

## Generic formats instead of code

For trackers that send JSON over HTTP or delimited text, define a format in the UI (`ADDING_DEVICES_VIA_UI.md`,
case 4). Parsing, validation and the test-parse box all run in the gateway.

## Data forwarding (Prepared)

`apps/integrations/forwarders.py` defines the interface and a declarative field mapping for forwarding positions to
another system (a regulator, an insurer). No delivery loop runs yet, and no regulator's format is assumed: implement
one from the official specification, consuming the positions stream as the processor does.
