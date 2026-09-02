# USAGE — Scenario configuration under `scratch/`

This document explains how to author, validate, and reason about all scenario configuration files under each case directory in `scratch/`. It ties together:
- The CSV/TXT formats consumed by the runner (`scratch/ub-quick-example.cc`),
- How the Python toolchain in the submodule `scratch/ns-3-ub-tools/` generates and analyzes them, and
- How the Unified-Bus (UB) model in `src/unified-bus/model/` interprets these values via ns-3’s Attribute System.

The goal is that you can confidently create new scenarios, know what each field means, which values are legal, and how to verify them.

---

## Directory layout of a typical case

Each case directory under `scratch/` usually contains:

- `network_attribute.txt` — Global defaults and feature toggles set via ns-3 Attributes and project-level globals.
- `node.csv` — Node inventory: devices and switches with port counts and (optional) forwarding delay.
- `topology.csv` — L2 links between ports, with bandwidth and propagation delay.
- `routing_table.csv` — Per-node forwarding rules for a given destination and destination-port.
- `transport_channel.csv` — Transport Path Numbers (TPNs) and priorities between endpoints and ports.
- `traffic.csv` — Application-level tasks (ops, size, priority, dependency, timing).
- `fault.csv` — Optional, only if faults are enabled (see `UB_FAULT_ENABLE`).

During a run, UB also emits:
- `runlog/` — Packet and task traces.
- `output/` (or `test/`) — Post-processed CSVs, e.g. `throughput.csv`, `task_statistics.csv`.

The runner loads them in this order (see `scratch/ub-quick-example.cc`):
1) `network_attribute.txt` → `UbUtils::SetComponentsAttribute`
2) `node.csv` → `UbUtils::CreateNode`
3) `topology.csv` → `UbUtils::CreateTopo`
4) `routing_table.csv` → `UbUtils::AddRoutingTable`
5) `transport_channel.csv` → `UbUtils::CreateTp`
6) `traffic.csv` → `UbUtils::ReadTrafficCSV` → schedule tasks

---

## How `ns-3-ub-tools` generates configurations

The submodule contains helpers to synthesize config files:

- Topology builders and visualization:
  - `user_topo_*.py` — declarative topology definitions (e.g., `user_topo_4x4_2DFM.py`, `user_topo_2layer_clos.py`).
  - `net_sim_builder.py` — expands node ranges, renders `node.csv`, `topology.csv`, `routing_table.csv`, and `transport_channel.csv` according to a chosen topology.
  - `topo_plot.py` — draw `network_topology.png` for quick visual checks.
- Traffic makers:
  - `traffic_maker/*.py` — generate `traffic.csv` for workloads (e.g., all-to-all, RDMA write/read patterns, collective-like flows).
- Trace analysis:
  - `trace_analysis/parse_trace.py` — orchestrates post-processing, runs:
    - `task_statistics.py` — merges Task/Packet traces back into `traffic.csv`.
    - `cal_throughput.py` — parses `PortTrace_node_*` to produce `throughput.csv`.

You can study how these scripts interpret/write each column to better understand legal values and intended semantics.

---

## Conventions and units

- Time strings follow ns-3 `Time` parsing (both `20ns` and `+20ns` are accepted), e.g. `20ns`, `5us`, `3ms`, `1s`.
- Data rates use ns-3 `DataRate` parsing, e.g. `400Gbps`, `1200Gbps`, `10Gbps`, `100Mbps`.
- Booleans are `true`/`false` (case-insensitive; tools normalize them).
- Integer lists are space-separated (e.g., `"1 2 3"`).
- Ranges use `a..b` inclusive (e.g., `0..3`) and are expanded by the tools.

---

## `network_attribute.txt`

Lines in two forms:

- Set ns-3 Attributes (defaults):
  - `default ns3::ClassName::AttributeName "value"`
  - Example: `default ns3::UbPort::UbDataRate "400Gbps"`
- Set project-level globals:
  - `global NAME "value"`
  - Example: `global UB_PYTHON_SCRIPT_PATH "scratch/ns-3-ub-tools/trace_analysis/parse_trace.py"`

Common UB attributes you’ll see (all names below come from `GetTypeId().AddAttribute(...)` in the code):

- Link/Port timing and rate:
  - `ns3::UbLink::Delay` (Time)
  - `ns3::UbPort::UbDataRate` (DataRate)
  - `ns3::UbPort::UbInterframeGap` (Time)
- Credit-based/PFC knobs:
  - `ns3::UbPort::CbfcFlitLenByte`, `CbfcFlitsPerCell`, `CbfcInitCreditCell`, `CbfcRetCellGrainDataPacket`, `CbfcRetCellGrainControlPacket`
  - `ns3::UbPort::PfcUpThld`, `PfcLowThld`
- Congestion control (CAQM) and buffers:
  - `ns3::UbCaqm::*`, `ns3::UbHostCaqm::*`, `ns3::UbSwitchCaqm::*`
  - `ns3::UbQueueManager::BufferSize`
- Transport behavior (`ns3::UbTransportChannel`):
  - `UsePacketSpray` (bool)
  - `UseShortestPaths` (bool)
  - `EnableRetrans`, `InitialRTO`, `MaxRetransAttempts`, `RetransExponentFactor`, `DefaultMaxWqeSegNum`, `DefaultMaxInflightPacketSize`, `TpOooThreshold`
- Allocator:
  - `ns3::UbSwitchAllocator::AllocationTime` (Time)
- App & API LD/ST knobs:
  - `ns3::UbApp::EnableMultiPath` (bool)
  - `ns3::UbApiLdst::*` (ThreadNum, LoadResponseSize, StoreRequestSize, QueuePriority)
  - `ns3::UbApiLdstThread::*` (StoreOutstanding, LoadOutstanding, LoadRequestSize, QueuePriority, UsePacketSpray, UseShortestPaths)

Project-level `global` keys (defined as `GlobalValue` in code and read by UB):

- `UB_FAULT_ENABLE` (bool) — If `true`, `fault.csv` must exist.
- `UB_PRIORITY_NUM`/`UB_VL_NUM` (int) — QoS/virtual lanes sizing.
- `UB_CC_ALGO` (string) — e.g., `CAQM`.
- `UB_CC_ENABLED` (bool) — enable/disable CC.
- Trace toggles: `UB_TRACE_ENABLE`, `UB_PARSE_TRACE_ENABLE`, `UB_RECORD_PKT_TRACE` (bool).
- `UB_PYTHON_SCRIPT_PATH` — Path to the Python post-processing entry (`parse_trace.py`).

Legal values and discovery:
- Names and types are defined in each class’s `GetTypeId().AddAttribute(...)`.
- To discover legal attributes quickly:
  - Search: `grep -R "GetTypeId\(|AddAttribute\(" src/unified-bus/model`
  - Check the Attribute value type (Time/DataRate/Boolean/Uinteger) to format literals correctly.
- If parsing fails, ns-3 prints an error; fix the literal (e.g., `20ns`, `400Gbps`, `true`).

---

## `node.csv`

Schema:
```
nodeId,nodeType,portNum[,forwardDelay]
```
- `nodeId` — integer or range `a..b`, inclusive.
- `nodeType` — `DEVICE` (end host) or `SWITCH`.
- `portNum` — number of ports on the node.
- `forwardDelay` — optional per-node forwarding delay (Time). If absent, defaults from attributes apply.

Examples:
```
0..1,DEVICE,1,1ns
2..3,SWITCH,4,1ns
```

Notes on `forwardDelay`:
- **Meaning**: the optional fourth column `forwardDelay` sets the **arbitration latency** (scheduling delay) for the node's internal switch allocator.
- **Code mapping**: when present, `UbUtils::CreateNode()` applies this value by calling `allocator->SetAttribute("AllocationTime", StringValue(forwardDelay));`.
- **Mechanism**: In `UbRoundRobinAllocator::TriggerAllocator`, this time is used to schedule the `AllocateNextPacket` event (`Simulator::Schedule(m_allocationTime, ...)`). This simulates the hardware processing time required for the arbiter to select which ingress queue's packet gets to transmit to an egress port.
- **Scope**: Applies to both `SWITCH` nodes and `DEVICE` nodes.
- **Format**: use ns-3 Time literals (e.g. `10ns`, `1us`, `1ms`).
- **Example**: `0,SWITCH,4,10ns` sets the switch allocator `AllocationTime` to `10ns` for node `0`.
- **Inspecting at runtime**: run your case with `--PrintAttributes=ns3::UbSwitchAllocator` to see the attribute and its current/default value.

---

## `topology.csv`

Schema:
```
nodeId1,portId1,nodeId2,portId2,bandwidth,delay
```
- `nodeId1/2` — node indices that are connected.
- `portId1/2` — local port indices at each endpoint.
- `bandwidth` — DataRate string, e.g. `400Gbps`, `1200Gbps`.
- `delay` — propagation Time, e.g. `20ns`.

Example:
```
0,0,2,0,400Gbps,20ns
1,0,3,0,1200Gbps,20ns
```

UB interprets these links via `UbLink` and attaches corresponding `UbPort`s with the specified rate and delay.

---

## `routing_table.csv`

Schema:
```
nodeId,dstNodeId,dstPortId,outPorts,metrics
```
- `nodeId` — the router (switch or device) where this rule applies.
- `dstNodeId` — destination end-host node.
- `dstPortId` — destination port at the end host (usually `0` unless multi-port hosts).
- `outPorts` — space-separated list of egress port IDs to use from `nodeId`.
- `metrics` — space-separated list of integer metrics (same length as `outPorts`), lower is better.

Examples:
```
0,1,0,0,4
2,1,0,1 2 3,3 3 3
```

UB stores outports per destination grouped by metric. The group with the smallest metric is installed as “shortest”; other groups are installed as “other”. If `UseShortestPaths` is `true` (see below), selection is made from the shortest group; otherwise selection may consider all outports defined for that destination.

Note — destination-port aware lookup with fallback:
- The switch `ns3::UbRoutingProcess::GetOutPort(...)` first tries to route by the exact pair `(dstNodeId, dstPortId)` encoded in the packet headers.
- If no entry exists for that exact pair, `ns3::UbRoutingProcess::GetOutPort(...)` masks the destination-port field in the network address and retries using only `dstNodeId` (i.e., route “to the node”, regardless of its local port).
- The simulator currently assumes all ports on a node are mutually reachable (equivalent) at the destination, so the fallback may deliver to another port on that node.
- Implementation references: `ns3::UbRoutingProcess::GetOutPort(rtKey, inPort)` performs this exact-then-fallback lookup; `UbSwitch::ForwardDataPacket` builds the routing key via `GetURMARoutingKey` / `GetLdstRoutingKey`. Address fields originate from `utils::NodeIdToIp(nodeId[,portId])` and CNA helpers `utils::Cna16ToNodeId/Cna16ToPortId`.

---

## `transport_channel.csv`

Schema:
```
nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric
```
- Defines Transport Path Numbers (TPNs) between a local `(nodeId1,portId1)` and a remote `(nodeId2,portId2)`.
- `tpn1/tpn2` — local TP numbers at each side; pairwise mapping.
- `priority` — traffic class / priority (0..15 by default; see `UB_PRIORITY_NUM`).
- `metric` — relative preference when multiple TPs exist (small is better).

Example (multi-TP between two hosts across three links):
```
0,0,0,1,0,0,7,2
0,1,1,1,1,1,7,2
0,2,2,1,2,2,7,2
```

UB reads these into `TpConnectionManager`. The `priority` field allows selecting TPNs by priority, and the `metric` field is used to prefer lower-metric TPNs when multiple candidates are present.

Constraints and tips:
- TPNs are looked up per port in controllers (TPN→`UbTransportChannel` map). Ensure uniqueness per (node, port); duplicates will collide in demux.
- `priority` should be within `UB_PRIORITY_NUM`.
- `metric` is an unsigned integer; lower is preferred when selecting among multiple TPNs.

### TPN in the code (what it does and how to set it)

- TPN is an integer identifier written into packet headers and used to index `UbTransportChannel` objects via per-port maps in controllers.
- You set TPNs in `transport_channel.csv` (`tpn1`/`tpn2`). The code does not de-duplicate TPNs at parse time; avoid assigning the same TPN twice on the same (node, port).
- Selection uses `priority` (exact match) and `metric` (prefer minimum) in `TpConnectionManager`.

TP channel and TP group in code:
- A TP channel is the UB transport-layer shared path that the transaction layer uses. In code it is `ns3::UbTransportChannel`, created by `UbController::CreateTp(...)` and stored in a per-port TPN map (`m_numToTp`; see `UbController::GetTp/GetTpnMap`). Endpoints are fixed at creation: source/destination node IDs and ports, priority, and the `(srcTpn,dstTpn)` pair are passed to `UbTransportChannel::SetUbTransport(...)` inside `CreateTp`.
- A jetty (function layer context) can be bound to multiple TP channels to form a TP group for multipath. In code (`UbApp::SendTraffic`), after `UbFunction::CreateJetty(...)`, the app collects candidate TPNs from `TpConnectionManager` and calls `UbFunction::jettyBindTp(src,dest,jettyNum,multiPath,tpns)`. When `multiPath` is true, `jettyBindTp` looks up each TP by TPN (`UbController::GetTp`) and calls `UbTransportChannel::CreateTpJettyRelationship(...)` for each; the vector is recorded in `UbFunction::m_jettyTpGroup[jettyNum]`.
- Control-plane establishment of TPs (negotiation/bring-up) is not modeled: the simulator instantiates TP channels directly from `transport_channel.csv` in `UbUtils::CreateTp(...)`. For administrative procedures, refer to the [UnifiedBus (UB) Base Specification](https://www.unifiedbus.com/zh); they are currently outside this simulator’s scope.

---

## `traffic.csv`

Schema:
```
taskId,sourceNode,destNode,dataSize(Byte),opType,priority,delay,phaseId,dependOnPhases
```
- `taskId` — integer ID (unique per file).
- `sourceNode` / `destNode` — end-host node IDs.
- `dataSize(Byte)` — payload size in bytes.
- `opType` — e.g., `URMA_WRITE`, `MEM_STORE`, … (supported by `UbApp`/API LDST layer).
- `priority` — 0..(UB_PRIORITY_NUM-1).
- `delay` — schedule offset relative to simulation start (Time).
- `phaseId` — integer phase tag; tasks with the same phase can run concurrently.
- `dependOnPhases` — optional list (space-separated) of phase IDs that must complete before this task’s phase starts.

Examples:
```
0,0,1,4000000,URMA_WRITE,7,10ns,0,
0,0,10,16384009,MEM_STORE,7,10ns,1,
1,0,13,16384000,MEM_STORE,7,10ns,1,
```

UB’s runner (`UbTrafficGen`) uses these to enqueue WQEs, connect to the proper `TpConnectionManager`, and drive sending/ACK tracking. The post-processing script `trace_analysis/task_statistics.py` merges Task and Packet traces back into this CSV, adding columns:
- `taskStartTime(us)`, `taskCompletesTime(us)` — task timeline
- `firstPacketSends(us)`, `lastPacketACKs(us)` — packet timeline
- `taskThroughput(Gbps)` — computed throughput per task

---

## Optional: `fault.csv`

Only required if `UB_FAULT_ENABLE` is `true` in `network_attribute.txt`. The exact schema depends on `UbFault` implementation (see `src/unified-bus/model/ub-fault*` and how `UbUtils::InitFaultMoudle` parses it). Typical fields include fault type, affected links/ports/nodes, start time, duration.

---

## Mapping to UB model code

- Attribute application happens in `UbUtils::SetComponentsAttribute` using ns-3 `Config::SetDefault` under the hood; attribute names map 1:1 with `GetTypeId().AddAttribute(...)` in classes like `UbPort`, `UbLink`, `UbSwitch`, `UbTransportChannel`, `UbApp`, etc.
- Node/port/link creation flows via `UbUtils::CreateNode` and `UbUtils::CreateTopo`, assembling `UbLink` between `UbPort`s. `topology.csv` bandwidth maps to `UbPort::UbDataRate`, delay to `UbLink::Delay`.
- Routing installs per-node forwarding tables from `routing_table.csv`.
- Transport channels (`transport_channel.csv`) build TPN mappings used by `UbApp` through `TpConnectionManager`.
- Tasks (`traffic.csv`) are scheduled by `UbTrafficGen`, and `UbApp` sends over the selected TPs, honoring `UsePacketSpray` vs. `EnableMultiPath`.

For advanced users, search in `src/unified-bus/model/`:
- `GetTypeId\(` and `AddAttribute\(` — discover attribute names/types and defaults.
- `UbUtils::Create*` — CSV parsing and object creation details.
- `UbTransportChannel`, `UbEgressQueue`, `UbSwitchAllocator` — behavior under multipath/spray and priority.

---

## Validating configuration values

- Attribute names/values:
  - If a run fails early, check console for attribute parse errors; fix the literal (e.g., `400Gbps`, `+10ns`, `true`).
  - To enumerate attributes of a component, inspect its `GetTypeId()` in the source.
- Structural consistency:
  - Ensure `node.csv` declares enough `portNum` to match `topology.csv` port IDs.
  - `routing_table.csv` `outPorts` must be valid egress ports on that `nodeId`.
  - `metrics` list length must equal `outPorts` length.
  - `transport_channel.csv` TPNs unique per (nodeId, portId).
  - `priority` values must be within `UB_PRIORITY_NUM`.
- Quick smoke test:
  - Run the case; UB will print clear errors for out-of-range indexes or bad formats.
  - Use `topo_plot.py` to visualize and catch wiring mistakes.

---

## Example: minimal 2-node single-TP

```
network_attribute.txt
  default ns3::UbPort::UbDataRate "400Gbps"
  default ns3::UbLink::Delay "+20ns"
  global UB_PYTHON_SCRIPT_PATH "scratch/ns-3-ub-tools/trace_analysis/parse_trace.py"

node.csv
  nodeId,nodeType,portNum,forwardDelay
  0..1,DEVICE,1,1ns
  2..3,SWITCH,4,1ns

topology.csv
  nodeId1,portId1,nodeId2,portId2,bandwidth,delay
  0,0,2,0,400Gbps,20ns
  1,0,3,0,400Gbps,20ns
  2,1,3,1,400Gbps,20ns

routing_table.csv
  nodeId,dstNodeId,dstPortId,outPorts,metrics
  0,1,0,0,1
  1,0,0,0,1
  2,0,0,0,1
  2,1,0,1,1
  3,0,0,1,1
  3,1,0,0,1

transport_channel.csv
  nodeId1,portId1,tpn1,nodeId2,portId2,tpn2,priority,metric
  0,0,0,1,0,0,7,1

traffic.csv
  taskId,sourceNode,destNode,dataSize(Byte),opType,priority,delay,phaseId,dependOnPhases
  0,0,1,4000000,URMA_WRITE,7,10ns,0,
```

In `scratch/ub-quick-example.cc`, `UbUtils::ParseTrace()` runs after the simulator. If `UB_PARSE_TRACE_ENABLE` is `true` and `UB_PYTHON_SCRIPT_PATH` points to `parse_trace.py`, the script processes `runlog/` and writes analysis CSVs (e.g., `task_statistics.csv`, `throughput.csv`) under the same case directory.

---

## Network modeling notes (from code)

- Link rate and packet size determine transmission time; `ns3::UbLink::Delay` adds propagation delay; switch arbitration is driven by `ns3::UbSwitchAllocator::AllocationTime`.
- IFG: `ns3::UbPort::UbInterframeGap` (set to `0ns` to disable spacing).
- Queue/buffer: `ns3::UbQueueManager::BufferSize` bounds ingress/egress accounting used by the switch.
- Path choice: `UseShortestPaths` influences which outport sets are considered; `UsePacketSpray` toggles per-packet load-balance usage in headers and routing.
- Congestion control: `UB_CC_ALGO` and `UB_CC_ENABLED` pick and enable the algorithm (CAQM classes are implemented).

---

## How to configure common behaviors (exact lines)

Place these in `network_attribute.txt` as needed (values shown are examples taken from shipped cases):

- Link/port basics
  - `default ns3::UbLink::Delay "20ns"`
  - `default ns3::UbPort::UbDataRate "400Gbps"`
  - `default ns3::UbPort::UbInterframeGap "0ns"`
- Switch allocator
  - `default ns3::UbSwitchAllocator::AllocationTime "10ns"`
- Transport toggles
  - `default ns3::UbTransportChannel::UsePacketSpray "false"`
  - `default ns3::UbTransportChannel::UseShortestPaths "true"`
  - Retransmission knobs (if used): `EnableRetrans`, `InitialRTO`, `MaxRetransAttempts`, ...
- Application multipath
  - `default ns3::UbApp::EnableMultiPath "false"`
- LD/ST API threads
  - `default ns3::UbApiLdst::ThreadNum "10"`
  - `default ns3::UbApiLdstThread::UsePacketSpray "true"`
  - `default ns3::UbApiLdstThread::UseShortestPaths "true"`
- Flow control and buffers (as needed)
  - `default ns3::UbPort::PfcUpThld "1677721"`
  - `default ns3::UbPort::PfcLowThld "1342176"`
  - `default ns3::UbQueueManager::BufferSize "12582912"`
- Congestion control
  - `global UB_CC_ALGO "CAQM"`
  - `global UB_CC_ENABLED "false"`
- Trace and parsing
  - `global UB_TRACE_ENABLE "true"`
  - `global UB_PARSE_TRACE_ENABLE "true"`
  - `global UB_RECORD_PKT_TRACE "true"`
  - `global UB_PYTHON_SCRIPT_PATH "scratch/ns-3-ub-tools/trace_analysis/parse_trace.py"`
- Priority/VL sizing
  - `global UB_PRIORITY_NUM "16"`
  - `global UB_VL_NUM "16"`

Then author the CSVs:
- `node.csv` (declare nodes/ports), `topology.csv` (wire ports with rate/delay),
- `routing_table.csv` (outPorts and metrics; smallest metric group becomes “shortest”),
- `transport_channel.csv` (TPNs and priorities; keep TPNs unique per (node, port)),
- `traffic.csv` (tasks).

---

## Discovering attributes at runtime (no doxygen)

ns-3’s CommandLine lets you introspect available TypeIds and Attributes directly from your program. Run your scenario with these flags:

Examples:

```bash
# List all registered TypeIds (you can pipe through grep Ub)
./ns3 run scratch/ub-quick-example -- --PrintTypeIds

# Show attributes for a specific component
./ns3 run scratch/ub-quick-example -- --PrintAttributes=ns3::UbPort
./ns3 run scratch/ub-quick-example -- --PrintAttributes=ns3::UbLink
./ns3 run scratch/ub-quick-example -- --PrintAttributes=ns3::UbTransportChannel
./ns3 run scratch/ub-quick-example -- --PrintAttributes=ns3::UbSwitchAllocator
./ns3 run scratch/ub-quick-example -- --PrintAttributes=ns3::UbApp

# Print global default paths (useful when writing network_attribute.txt)
./ns3 run scratch/ub-quick-example -- --PrintGlobals

# General help for supported flags
./ns3 run scratch/ub-quick-example -- --PrintHelp
```

Tip: You can run the same flags against any other scratch program name. This avoids relying on doxygen and guarantees you see exactly what your build exposes.

---

## FAQ

- “How do I get the latest tools?” — If you use submodules, remember that `git submodule update --init --recursive` checks out the submodule SHA recorded by the parent repo. To follow the tools’ latest `main`, either:
  - In the submodule: `git checkout main && git pull` (local only), or
  - Update the submodule pointer in the parent repo and `git commit` it, so others get it via `submodule update`.
  - Optionally set a tracking branch in `.gitmodules` and use `--remote` with submodule update.

- “How do I find legal values for an Attribute?” — Look for `GetTypeId().AddAttribute(...)` in the component source. The C++ type (Time/DataRate/Uinteger/Boolean/Enum) dictates the literal format.

---

Happy simulating — and if something’s unclear, open the corresponding tool script (`ns-3-ub-tools`) and the UB model class to see exactly how a field is parsed and applied.
