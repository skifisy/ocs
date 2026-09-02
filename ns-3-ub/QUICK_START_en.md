# Quick Start

> This project is built on ns-3.44 and verified on Linux and Windows WSL. For detailed platform support, installation steps, system requirements, and build options, see [ns-3.44 Documentation](https://www.nsnam.org/releases/ns-3-44/documentation/), [Installation Guide](https://www.nsnam.org/docs/release/3.44/installation/singlehtml/), and [ns-3.44 Source](https://gitlab.com/nsnam/ns-3-dev/-/tree/ns-3.44?ref_type=tags).

## Prerequisites

The core build depends on the following tools. Download can be via either Git or source archive (via a web browser, wget, or curl).

| Purpose       | Tool                         | Minimum version        |
| ------------- | ---------------------------- | ---------------------- |
| Download      | git (for Git download) <br/>or: tar and bunzip2 (Web)      | No minimum version     |
| Compiler      | g++<br/>or: clang++          | >= 10<br/>>= 11        |
| Configuration | python3                      | >= 3.8                 |
| Build system  | cmake<br/>plus one of make / ninja / Xcode | cmake >= 3.13<br/>No minimum version |

If using Conda/virtualenv, please ensure that the `python3` used later matches the interpreter used to install dependencies.

### Check versions quickly

From the command line, you can check the versions as follows:

| Tool    | Version check command |
| ------- | --------------------- |
| g++     | `g++ --version`       |
| clang++ | `clang++ --version`   |
| python3 | `python3 -V`          |
| cmake   | `cmake --version`     |

## Get the Code

```bash
# Clone the project
git clone https://gitcode.com/open-usim/ns-3-ub.git
cd ns-3-ub

# Initialize and update submodules (includes Python analysis tools)
git submodule update --init --recursive

# If the above command fails, you can clone manually:
# git clone https://gitcode.com/open-usim/ns-3-ub-tools.git scratch/ns-3-ub-tools

# Verify submodule status
git submodule status
```

To automatically trigger trace analysis after simulation completion, please configure the tool path in the corresponding use case's `network_attribute.txt`, for example:

```
global UB_PYTHON_SCRIPT_PATH "scratch/ns-3-ub-tools/trace_analysis/parse_trace.py"
```

## Python Tools & Dependencies

The project's Python toolset is located in `scratch/ns-3-ub-tools/`:

- Topology/Visualization: `net_sim_builder.py`, `topo_plot.py`, `user_topo_*.py`
- Traffic Generation: `traffic_maker/*`
- Trace Analysis: `trace_analysis/parse_trace.py`

It is recommended to install dependencies using the `requirements.txt` in the project:

```bash
python3 -m pip install --user -r scratch/ns-3-ub-tools/requirements.txt
# You may encounter `externally-managed-environment` restrictions, in which case please try using a virtual environment
# Use domestic mirror sources to accelerate downloads
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r scratch/ns-3-ub-tools/requirements.txt
# Or use conda:
conda install pandas matplotlib seaborn
# Or manually install the above dependencies
```

Note: Please install the required third-party packages via `requirements.txt` before running `trace_analysis/parse_trace.py`.

## Build

```bash
# Configure build environment
./ns3 configure

# Compile project
./ns3 build
```

## Run a Minimal Example

```bash
# If using Conda, ensure its bin is in PATH first (or activate environment first)
export PATH=/home/ytxing/miniconda3/bin:$PATH

# Install dependencies
python3 -m pip install --user -r scratch/ns-3-ub-tools/requirements.txt

# Run small example and trigger trace analysis
./ns3 run 'scratch/ub-quick-example scratch/2nodes_single-tp'

# Verify output
ls scratch/2nodes_single-tp/output/
# Expected to contain: task_statistics.csv  throughput.csv
```

If you encounter `ModuleNotFoundError: No module named 'pandas'`, it means the runtime `python3` is inconsistent with the interpreter used to install dependencies; please check PATH, or use `python3 -m pip install --user ...` to install dependencies in the current interpreter.

## Examples under scratch (Available Use Cases List)

The following are the available use case directories and corresponding run commands currently provided in the repository:

- 2 nodes (single TP):
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2nodes_single-tp'
  ```

- 2 nodes (multiple TP):
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2nodes_multiple-tp'
  ```

- 2 nodes (packet spray):
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2nodes_packet-spray'
  ```

- 2D FullMesh 4x4 (multipath All-to-All):
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2dfm4x4-multipath_a2a'
  ```

- 2D FullMesh 4x4 (hierarchical broadcast):
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2dfm4x4-hierarchical_broadcast'
  ```

- Clos (32 hosts / 4 leafs / 8 spines, pod2pod):
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/clos_32hosts-4leafs-8spines_pod2pod'
  ```

Note: Some large-scale use cases take a long time to run, please select and run as needed.

## Full Workflow Example (Complete Workflow Verification)

```bash
# Run complete example, including Python post-processing
./ns3 run 'scratch/ub-quick-example scratch/2dfm4x4-multipath_a2a'

# Expected output:
[01:23:37]:Run case: scratch/2dfm4x4-multipath_a2a
[01:23:37]:Set component attributes
[01:23:37]:Create node.
[01:23:37]:Start Client.
[01:23:37]:Simulator finished!
[01:23:37]:Start Parse Trace File.
All dependencies are satisfied, starting script execution...
Processing complete, results saved to scratch/2dfm4_4-multipath_a2a/output/task_statistics.csv
Processing complete, results saved to scratch/2dfm4_4-multipath_a2a/output/throughput.csv
[01:23:37]:Program finished.

# View generated result files
ls scratch/2dfm4x4-multipath_a2a/output/
# task_statistics.csv  throughput.csv
```

## Config Files (Configuration File Description)

Each use case directory typically contains the following files (format can refer to existing examples):

- `network_attribute.txt` - Network global parameters (can configure `UB_PYTHON_SCRIPT_PATH` for automatic post-processing)
- `node.csv` - Node definitions
- `topology.csv` - Topology connections
- `routing_table.csv` - Routing table
- `transport_channel.csv` - Transport channels
- `traffic.csv` - Traffic definitions

For detailed scenario configuration and file formats, see: [scratch/README.md](scratch/README.md).