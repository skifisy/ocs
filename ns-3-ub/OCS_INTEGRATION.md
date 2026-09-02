# OCS integration notes

This directory vendors the complete buildable ns-3-UB source tree used by the
Mooncake-to-L1-pair pipeline. It is based on:

- upstream: `https://github.com/halfmanli/ns-3-ub.git`
- upstream commit: `d25d6504f8e9a13a418125b82f84ef775eada39c`

To keep the OCS repository practical to clone, the snapshot omits only
upstream documentation images/diagram binaries and the three large LTE fading
trace datasets under `src/lte/model/fading-traces/`. They are not compiled and
are unrelated to Unified Bus, LD/ST, Mooncake, or the supplied build command
(`--disable-examples --disable-tests`). All source code, headers, build files,
runtime models, and OCS case inputs required by this pipeline are included.

The OCS integration adds lightweight tracing for the actual directed source-L1
to destination-L1 pair and the originating task ID. The relevant changes are in:

- `src/unified-bus/model/ub-tag.h`
- `src/unified-bus/model/ub-port.h`
- `src/unified-bus/model/ub-port.cc`
- `src/unified-bus/model/ub-utils.h`
- `src/unified-bus/model/ub-utils.cc`
- `src/unified-bus/model/protocol/ub-ldst-api.cc`

It also fixes the LD/ST initialization and priority propagation needed by the
Mooncake pipeline. Mooncake reads use `MEM_LOAD` with P or D as the requester
and Storage as the target; writes use `MEM_STORE` with P or D as the sender.
LOAD traffic therefore contains a small request in the requester-to-Storage
direction and a data response in the Storage-to-requester direction.

The generated `runlog/L1PairTrace.tr` records each carried packet once at its
actual destination L1. This supports multi-homed endpoints without adding the
L1-to-L2 and L2-to-L1 physical hops together. `UbL1TraceTag` is ns-3 metadata;
it is not serialized into the simulated wire packet and consumes no link
bandwidth.

The former patch is retained under `../patches/` for reference or for applying
to a separate clean upstream checkout. Do not apply it to this vendored source:
the changes are already present.

The optional upstream `scratch/ns-3-ub-tools` submodule is not required by the
integrated pipeline. The supplied case config disables upstream trace parsing;
OCS analysis scripts process `L1PairTrace.tr` and `PortTrace` directly.
