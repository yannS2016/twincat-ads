# Plan
Bring selected features from the ads-actions Python module into the twincat-ads EPICS module.

## Implementation Strategy
Implement the work in phases so each feature set can be tested and reviewed independently.

### Phase 0: Improve Configurability of IOC
Add more configuration parameters instead of so many magic numbers.

#### Required configuration
- targetIp

#### Optional configuration

- targetAmsNetId                 default to discovery if absent
- localAmsNetId                  default from local IOC server IP + .1.1
- systemServicePort              default 10000
- plcRuntimePorts                default [851]
- ncConfigPort                   default 500
- ncSymbolPort                   default 501
- etherCatMasterPort             default 0xFFFF
- loggerPort                     default 100
- adsTcpPort                     default 48898
- adsDiscoveryUdpPort            default 48899

#### Behavior configuration
- enableNcParameterWrites        default true
- enableCoeParameterWrites       default true
- enableEcStateRequests          default true
- enableAdsStateRequests         default true
- enableIcmpPingCheck            default true
- continueIfPingFails            default false
- enableAdsReachabilityCheck     default true
- enableAutoRouteDiagnostics     default true
- enableRouteCreationCommands    default true
- enableEventLoggerConnection    default true
- allowIndexOffsetFallback       default true
- allowSlowRecoveryRetry         default true

#### Timing configuration
- heartbeatPeriodSec             default 5.0
- heartbeatFailureThreshold      default 2
- routeCheckInitialBackoffSec    default 1.0
- routeCheckMaxBackoffSec        default 64.0
- bulkReadRetryWindowSec         default 0.1
- bulkWriteGroupWindowSec        default 0.1
- slowRecoveryRetryPeriodSec     default 30.0
- adsRequestTimeoutSec           default 5000 ms

#### Limits
- maxBulkReadBatchSize           default 500
- maxBulkWriteBatchSize          default 500
- maxFailedSymbolsPerCycle       default 500
- maxEventLoggerMessagesPerSec   default 100
- maxDiagnosticLogRate           default 100

### Phase 1: Route checking and connection diagnostics
Implement diagnostic shell commands and reusable diagnostic APIs.

- New function adsCheckRoute with helpers for: 
    - AMS Net ID discovery support
    - ADS TCP reachability checks
    - ADS state checks for system and PLC runtime ports
    - Optional NC and EtherCAT discovery during diagnostics
    - Structured human-readable and machine-readable diagnostic output
    - Route creation function with shell command

#### Add shell command
shellAdsCheckRoute(targetName, verbosity, outputFormat)

verbosity = summary | normal | verbose
outputFormat = text | json

#### Diagnostic sequence
For a configured target:

1. Optionally attempt ICMP ping.
    - If ping fails:
    - Report warning:
        - ICMP ping failed. This may indicate network failure, firewall configuration, or ICMP being disabled. Continue with ADS checks if continueIfPingFails=true.
2. Optionally Check ADS TCP reachability on port 48898.
    - If ADS TCP port times out, suggest that the device cannot be reach via ADS possible due to firewall settings.
    - If ADS TCP port connection is rejected, suggest that the target device might not be a TwinCAT device or that it if offline.
3. If AMS Net ID is not explicitly configured:
    - Attempt ADS/AMS discovery.
    - If discovery fails, report possible causes:
        - TwinCAT not running,
        - UDP discovery "adsDiscoveryUdpPort" blocked,
        - firewall issue,
        - target not reachable,
        - unsupported discovery path.
    - If discover succeeds, report discovered AMS Net ID.
4. Read ADS state from system service port, "systemServicePort"
    - If system service state read fails:
        - Report likely missing/malformed AMS route, wrong AMS Net ID, firewall issue, or ADS router issue.
        - If route creation commands are enabled, suggest explicit route creation command.
    - If system service state read succeeds and it is not RUN:
        - Report current state.
        - Do not automatically request RUN.
        - If state-changing commands are enabled, suggest explicit command.
5. Read ADS state from all configured PLC runtime ports, "plcRuntimePorts".
    - For each runtime port:
        - Report ADS/device state.
        - If not RUN, report that PLC runtime is not ready.
        - If required runtime ports are RUN, report PLC ADS communication as functional.
6. Optionally list NC axes.
7. Optionally list EtherCAT terminals.
8. Return structured status and status code.

#### Backoff behavior
When route check is run as a background recovery loop:

1 sec
2 sec
4 sec
8 sec
16 sec
32 sec
64 sec
64 sec
64 sec
...
The maximum backoff is configurable.

#### Example output
IP: 172.21.148.178

[OK] ICMP ping succeeded
[OK] ADS TCP port 48898 reachable
[OK] AMS Net ID discovery succeeded: 172.21.148.178.1.1
[OK] System service port 10000 state: RUN
[OK] PLC runtime port 851 state: RUN

Result: ROUTE_FUNCTIONAL

[OK] NC axes found: 3
     Axis 1: X
     Axis 2: Y
     Axis 3: Z

OR

[OK] No NC configurations found

[OK] EtherCAT terminals found: 4
     Port 1001  Name XY Axis EL7062  Type EL7062 Revision 0x00100000
     Port 1002  Name XY Axis EL5042  Type EL5042 Revision 0x00100000
     Port 1003  Name Lim EL1008  Type EL1008 Revision 0x00100000
     Port 1004  Name Z Axis EL7062  Type EL7062 Revision 0x00100000

OR

[OK] No EtherCAT master configured

OR 

[WARN] EtherCAT master configured but communication failed

#### Route creation support

##### Shell command
Example usage:
```bash
shellAdsAddRoute [username]
Password:
route created
```
OR
```bash
shellAdsAddRoute [username]
Password:
route creation failed, incorrect password provided
```

After successful route creation, automatically run checkRoute function.

### Phase 2: IOC connection state machine and heartbeat
Implement a formalized connection state machine that controls when PLC reads/write are allowed with the goal of preventing specific actions when our diagnostics show they will fail and fill up the logs with useless information.

- New connection state machine
- Dedicated heartbeat looping thread
- Reconnect/backoff conditions
- Error-spam suppression
- PV alarm propagation to properly show when PVs are bad
- Dynamic handle and notification recreation after recovery without restarting entire IOC process

#### States
```
UNINITIALIZED
DISCOVERING_NETID
ROUTE_CHECKING
NO_NETWORK
NO_ADS_TCP
NO_AMS_DISCOVERY
NO_ROUTE
TARGET_NOT_RUN
PLC_CODE_NOT_RUN
CONNECTED
DEGRADED
RECONNECTING
SHUTTING_DOWN
```

#### State behavior
| State | PLC Reads/Writes | Heartbeat | Route Check | PV Alarm Behavior |
|---|---|---|---|---|
| `UNINITIALIZED` | No | No | No | Invalid/disconnected |
| `DISCOVERING_NETID` | No | No | Yes | Invalid/disconnected |
| `ROUTE_CHECKING` | No | Limited | Yes | Invalid/disconnected |
| `NO_NETWORK` | No | No | Periodic backoff | COMM/INVALID |
| `NO_ADS_TCP` | No | No | Periodic backoff | COMM/INVALID |
| `NO_AMS_DISCOVERY` | No | No | Periodic backoff | COMM/INVALID |
| `NO_ROUTE` | No | No or limited | Periodic backoff | COMM/INVALID |
| `TARGET_NOT_RUN` | No | Yes | Periodic | COMM/INVALID |
| `PLC_CODE_NOT_RUN` | No | Yes | Periodic | COMM/INVALID |
| `CONNECTED` | Yes | Yes | No | Normal |
| `DEGRADED` | Partial | Yes | Optional | Per-PV alarm |
| `RECONNECTING` | No | Yes or limited | Yes | COMM/INVALID |
| `SHUTTING_DOWN` | No | No | No | No new activity |

#### State transition rules
TODO: FLESH THIS OUT MORE
- Failed heartbeat reads beyond threshold transition to ROUTE_CHECKING or RECONNECTING.
- Successful full route check transitions to CONNECTED.
- System service port not in RUN transitions to TARGET_NOT_RUN.
- Required PLC runtime port not in RUN transitions to PLC_NOT_RUN.
- Partial symbol/PV failures while core communication is healthy transition to DEGRADED.
- IOC shutdown transitions to SHUTTING_DOWN.

#### Heartbeat
Run every heartbeatPeriodSec.
Heartbeat checks
Each heartbeat cycle should:

Read ADS state from system service port, default 10000.
If system read fails:
Increment failure count.
If failure count reaches heartbeatFailureThreshold, transition to ROUTE_CHECKING or RECONNECTING.
If system service port is not RUN:
Transition to TARGET_NOT_RUN.
Stop normal PLC reads/writes.
Alarm affected PVs.
Read ADS state from each configured PLC runtime port.
If a runtime port is not RUN:
Transition to PLC_NOT_RUN for PVs associated with that runtime.
Stop reads/writes for that runtime.
Alarm affected PVs.
If all required checks pass:
Transition to CONNECTED.
If recovering, recreate handles and notifications.
Recovery behavior
When transitioning from disconnected/stopped state back to CONNECTED:

Re-read or refresh symbol table if enabled.
Re-request all dynamic handles.
Recreate ADS notifications.
Recreate event logger subscription if enabled.
Resume bulk reads/writes.
Clear PV alarms after successful read/write update.


### Phase 3: Bulk read/write resilience
Improve read/write stability under communication interruptions and load spikes.

- Bulk read failure flags on a per symbol basis
    - full bulk read failure flags all the symbols in that bulk read.
    - failure of single sub command of bulk read flags just that symbol.
- Recover dynamic handles
- Index group/index offset fallback when handles fail repeatedly
- Slow recovery queue for failed PVs
- Group individual write commands within a certain window of time into bulk write commands

Evidence and profiling results for this phase: `ads-network-resilience-profile.md` (IOC exits via `exit(-1)` and SIGPIPE during PLC reactivation; stuck bulk loop not yet reproduced, network-unplug runs pending).

### Phase 4 — NC, EtherCAT, and CoE support
Add shell commands and EPICS PV mapping support for selected NC, EtherCAT, and CoE operations.

- NC axis listing
- NC parameter read/write
- NC symbol read/write
- EtherCAT terminal discovery
- EtherCAT state read/request
- CoE parameter read/write

### Phase 5 — Event logger subscription support
Add TwinCAT event logger subscription support.

- Event logger subscription thread
- Reconnect-safe subscription lifecycle
- Raw event preservation
- Best-effort event parsing
- Rate-limited event reporting (suppress event spams from bogging down IOC server)

### Optional Phase 6: Symbol parser integration
Port the AdsSymbolParser from ads-actions to c++.

- Symbol table discovery
- Symbol validation
- Type/size validation
- Better startup diagnostics for missing/mismatched symbols

## Features to port to twincat-ads
### Stability and troubleshooting assistance related features
1. When communication issues arise, auto diagnose them by a checkRoute function added to twincat-ads (Allow these to be done by shell command as well):
    1. Attempt to ping target, if ping fails report that the target cannot be pinged and that the user should check the network connection between the IOC and PLC target. Go into a state where we issue a new ping in 1 second, then 2, then 4, then 8, then 16, then 32, then 64 and cap at every 64 seconds.
    2. If ping succeeds, report ping success and then make an AMS net ID discovery call on the target. If this fails, report that the target TwinCAT system is in a bad state and that the user should check the TwinCAT target for issues.
    3. If the AMS net ID discovery call works,  report the AMS net ID and that discovery was a success and then attempt to read the ADS state of port 10000. If this fails, report that there is an issue with the AMS route to the PLC target. It might be malformed or not ever created. In this case, offer to the user to create the route but only wait for 60 seconds before checking route again.
    4. If the ADS state of port 10000 shows that the target overall ADS system state is not in RUN mode, then report that to the user. Give them the option to request the target to go into RUN mode but wait only 60 seconds before running the route check again.
    5. If the ADS state of port 10000 shows RUN mode, then report that to the user and perform an ADS state read of port 851. If this fails, report that the plc runtime execution is in a bad state and needs to be checked. If the port 851 state is STOPPED, then report that the plc runtime execution is stopped and needs to be started. Give the option to request the target plc runtime execution to go into RUN mode but wait only 60 seconds before running the route check again.
    6. If the ADS state of port 851 shows RUN mode, then report that the PLC target route is functional.
    7. Check if an NC is configured by trying to list the NC axes and their axis ids and if one is not configured, report that no NC is configured (but this is not necessarily an error). If one is configured, report the list of configured NC axes and their ids.
    8. Check if an EtherCAT master is configured and if one is not configured, report that no EtherCAT master is configured (but this is not necessarily an error). If one is configured, report the list of all configured EtherCAT terminals with their AMS port number, configured name, terminal type, and ESI revision number.
2. When individual bulk read requests fail once (but the ADS state read heartbeat is still healthy in RUN state), flag the failed individual requests and add all the failed parameters over 0.1 second to a queue to request new dynamic handles for them.
    - Every 0.1 seconds, attempt to bulk request new dynamic handles for all parameters in the failed queue.
    - If a given bulk read request fails again even after the new dynamic handles have been requested, try again once more by adding it back to the failed to read queue.
    - If the bulk read fails again, attempt to read that parameter by requesting an index group and offset.
    - If that fails, try once more with the index group and offset.
    - If a parameter fails again, throw an error that the parameter cannot be read and remove it from the bulk read requests. Mark the PV is a major error or disconnected.
3. Maintain a heartbeat of the system port 10000 ADS state that runs every 5 seconds. 
    - If this reads that the PLC is not in RUN mode, stop attempting requests to read and write to PLC parameters and flag them all as disconnected or major error.
    - Move into a state where we keep checking the state until it is in run mode again  
    - When the PLC comes back into run mode, re-request all dynamics handles and dismiss and set up all notifications again.
    - If the heartbeat fails to be read properly at least twice in a row, move into a checkRoute loop to periodically try to diagnose what is wrong with the connection. Do not spam hundreds of ads read state lock errors in a row.
4. Add a bulk write request handler that groups individual write operations that occur witihn 0.1 seconds of each other into bulk write requests.
    - This prevents stability issues caused by too many write requests at once causing IOC server overload.
5. Add event logger subscription support.
    - Run on a separate thread.
    - Request to open up a subscription with the logger port 100.
    - Here is an example of how to open up such a subscription using pyads.
    - We would want to delete and reopen this subscription when communication issues arise and they are resolved.
    - On certain types of errors, such as dynamic handle errors and such we might want to auto fallback to index/offset bulk reads instead of dynamic handle reads.
```python
import ctypes
import datetime
import struct
import sys
import time

import pyads
from pyads.structs import SAdsNotificationHeader


# Change for specific target
REMOTE_NETID = "172.21.148.178.1.1"
REMOTE_IP    = "172.21.148.178"

LOGGER_PORT = 100

LOGGER_IDX_GROUP  = 0x00000001
LOGGER_IDX_OFFSET = 0x0000FFFF
LENGTH            = 0x00000400
TRANS_MODE        = 3
MAX_DELAY         = 0
CYCLE_TIME        = 0

MIN_EVENT_SIZE = 40

def filetime_to_dt(ft):
    if not ft:
        return None
    try:
        return datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ft / 10)
    except (OverflowError, OSError):
        return None


def parse_event_sample(sample: bytes) -> dict:
    """Layout confirmed against live 0x4260 events:
         [0:8] FILETIME, [8:12] kind, [12:16] src AMS port,
         [16:..] NUL-terminated source name, then message text.
       Message = longest printable run. NOTE: this method is proven only for
       the 0x4260 message so far; different event types are still untested."""
    out = {"raw_len": len(sample), "raw_hex": sample[:16].hex()}
    try:
        out["event_time"] = filetime_to_dt(struct.unpack_from("<Q", sample, 0)[0])
        out["kind_08"]    = struct.unpack_from("<I", sample, 8)[0]
        out["src_port"]   = struct.unpack_from("<I", sample, 12)[0]
        name_end = sample.index(b"\x00", 16)
        out["src_name"]   = sample[16:name_end].decode("latin1", "replace")
    except Exception as e:
        out["parse_error"] = f"header: {e!r}"
    run, best = bytearray(), bytearray()
    for b in sample:
        if 32 <= b < 127:
            run.append(b)
        else:
            best = run if len(run) > len(best) else best
            run = bytearray()
    best = run if len(run) > len(best) else best
    out["message"] = best.decode("latin1", "replace")
    return out


class LoggerNotificationClient:
    def __init__(self):
        self.plc = pyads.Connection(REMOTE_NETID, LOGGER_PORT, REMOTE_IP)
        self.where = f"REMOTE {REMOTE_NETID}:{LOGGER_PORT}"
        self._handle = None
        self._user_handle = None
        self._cb_ref = None

    def open(self):
        self.plc.open()

    def close(self):
        if self._handle is not None:
            try:
                self.plc.del_device_notification(self._handle, self._user_handle)
            except Exception as e:
                print(f"[warn] del_device_notification: {e!r}")
        try:
            self.plc.close()
        except Exception:
            pass

    def subscribe(self, on_event):
        attr = pyads.NotificationAttrib(
            length=LENGTH,
            trans_mode=TRANS_MODE,
            max_delay=MAX_DELAY,
            cycle_time=CYCLE_TIME,
        )

        def _raw_cb(notification, data):
            try:
                hdr = notification.contents
                base = ctypes.addressof(hdr)
            except AttributeError:
                base = int(notification)
                hdr = SAdsNotificationHeader.from_address(base)
            try:
                size = int(hdr.cbSampleSize)
                raw = ctypes.string_at(base + SAdsNotificationHeader.data.offset, size)
            except Exception as e:
                print(f"[cb] raw read failed: {e!r}")
                return
            print(f"[cb] fired: size={size} first16={raw[:16].hex(' ')}")
            if size < MIN_EVENT_SIZE:
                print("     (below event-record size; scrolling-buffer noise)")
                return
            ev = parse_event_sample(raw)
            ev["stamp_time"] = filetime_to_dt(int(getattr(hdr, "nTimeStamp", 0)))
            on_event(ev)

        self._cb_ref = _raw_cb
        self._handle, self._user_handle = self.plc.add_device_notification(
            (LOGGER_IDX_GROUP, LOGGER_IDX_OFFSET), attr, _raw_cb
        )
        print(f"[ok] subscribed on {self.where} "
              f"group=0x{LOGGER_IDX_GROUP:X} offset=0x{LOGGER_IDX_OFFSET:X} "
              f"len=0x{LENGTH:X} mode={TRANS_MODE} handle={self._handle}")


def manual_test():
    def on_event(ev):
        print("\n=== EVENT ===")
        for k in ("event_time", "stamp_time", "src_name", "src_port",
                  "kind_08", "message", "parse_error", "raw_hex"):
            if k in ev:
                print(f"  {k:12}: {ev[k]}")

    c = LoggerNotificationClient()
    try:
        c.open(); print(f"[ok] connection open ({c.where})")
        print(f"[ok] state = {c.plc.read_state()}")
        c.subscribe(on_event)
        print("[..] waiting. Trigger an error in TwinCAT. Ctrl-C to quit.")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[..] unsubscribing")
    except pyads.ADSError as e:
        print(f"[ERR] ADSError: {e}")
    finally:
        c.close(); print("[ok] closed")


if __name__ == "__main__":
    manual_test()
```
6. Implement the AdsSymbolParser from ads-actions and use it to identify when certain symbols do not exist on the target. Or use it to prevent the need
for hardcoding certain symbol addresses into the db file.
7. Chunk the massive db file into multiple smaller db files, separated by type of parameter. E.g., one for subscriptions, one for normal bulk read/writes, one for coe reads/writes, one for nc reads/writes, etc... so that troubleshooting db loading issues is easier to do.

### Configuration related features
1. Make an explicit AMS net ID optional for the IOC configuration.
    - By default, IOC should use ADS discovery to determine the AMS net ID of the PLC target. No need to force user to ensure PLC AMS net ID is the same as PLC IP except with .1.1 at the end.
2. Add the ability for the IOC to add a route on the target to the IOC server via IOC shell.
    - E.g., if the checkRoute function detects that the reason we cannot communicate with the PLC is that we have no route, report that to the user and ask them to enter in the username and then the password to create the route with.
    - If this succeeds, then attempt checkRoute again to make sure the route is working now.
    - If the route is still failing, then report this to the user and enter a preiodic checkRoute loop.

### NC related features
1. Read individual NC parameters (make mappable to a PV) on AMS port 500 (defaults to 500, but can be customized to a different AMS port). Allow these to be done by shell command as well.
2. Write individual NC parameters (make mappable to a PV) on AMS port 500 (defaults to 500, but can be customized to a different AMS port) Allow these to be done by shell command as well.
3. List NC axes and their IDs on the PLC target using NC Ring 0 configuration. By shell command and automatically during startup.
4. Read individual NC symbols (make mappable to a PV) on AMS port 501 (defaults to 501, but can be customized to a different AMS port) Allow these to be done by shell command as well.
5. Write individual NC symbols (make mappable to a PV) on AMS port 501 (defaults to 501, but can be customized to a different AMS port) Allow these to be done by shell command as well.

### EtherCAT related features
1. Shell command to list the EtherCAT terminals configured on a target (Port, Name, Terminal Type, ESI revision)
    - See discover-coe in ads-actions

#### Diagnostics
1. Read the EtherCAT state machine state for individual terminals. Allow these to be done by shell command as well.
    - See read-ecat-state in ads-actions
2. Request an EtherCAt state machine state for individual terminals. Allow these to be done by shell command as well.
    - See request-ecat-state in ads-actions

#### CoE parameters
1. Read individual CoE parameters (make mappable to a PV). Allow these to be done by shell command as well.
2. Write individual CoE parameters (make mappable to a PV). Allow these to be done by shell command as well.