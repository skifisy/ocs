# Mooncake Trace → DAG → ns-3-UB Traffic

本仓库提供一条两阶段转换流水线，把 Mooncake JSONL 请求轨迹转换成
`ns-3-UB` 可读取的 `traffic.csv`：

## 一键完整重跑（推荐）

仓库现在已经内置带 L1-pair 轻量 trace 的完整 `ns-3-ub/` 源码，不需要再应用
patch，也不需要另行下载 ns-3-UB。从 Mooncake JSONL 开始执行：

```bash
git clone https://github.com/skifisy/ocs.git
cd ocs

./scripts/run_mooncake_l1_pipeline.sh \
  --trace /path/to/mooncake_trace.jsonl \
  --case-dir /mnt/yuhaoze/scratch/mooncake_pd_l1trace_10k \
  --max-requests 10000 \
  --jobs 16
```

该命令依次完成：trace→DAG、DAG→traffic、400G Clos 拓扑与 ECMP 路由生成、
ns-3-UB 编译与仿真、`源L1→目的L1` 流量/活跃 TaskId 统计，以及 SVG/HTML 出图。
目标 case 必须是新目录或空目录，脚本不会删除、覆盖已有仿真结果。

首次正式运行前可用小样本验证完整数据准备：

```bash
./scripts/run_mooncake_l1_pipeline.sh \
  --trace examples/sample_trace.jsonl \
  --case-dir /tmp/mooncake_l1_smoke \
  --max-requests 2 \
  --prepare-only
```

系统依赖为 Python 3、CMake 和 C++ 编译器。绘图为纯 SVG/HTML，不依赖
Matplotlib 或 Pillow。详见 [`docs/end-to-end-run.md`](docs/end-to-end-run.md)。

```mermaid
flowchart LR
    A["Mooncake trace.jsonl"] --> B["trace → task DAG"]
    B --> C["task_dag.csv"]
    C --> D["DAG → UB phases"]
    D --> E["traffic.csv"]
```

当前版本建模的是：**Storage 中介式 P/D 分离 + Prefix Cache + Prefill
逐层计算/逐层写回**。P 和 D 没有直接流量。

## 文件

- `scripts/mooncake_trace_to_dag.py`：生成包含计算、网络、存储等待和依赖的应用 DAG。
- `scripts/task_dag_to_ub_traffic.py`：把 DAG 中的网络阶段降级为 ns-3-UB phase。
- `configs/mooncake_pd_store_config_v6_layer_pipeline.json`：可运行的示例配置。
- `docs/pipeline.md`：完整模型、字段映射、phase 语义和局限说明。
- [`docs/clos-network-basics.md`](docs/clos-network-basics.md)：Clos、ToR、Aggregation/Spine Block、项目拓扑映射及与 Jupiter OCS 的关系。
- [`docs/hotspot-analysis.md`](docs/hotspot-analysis.md)：从 ns-3-UB `PortTrace` 统计交换机链路束热点、4×400G超限和并行链路偏斜。
- `examples/sample_trace.jsonl`：最小输入示例。
- `tests/test_pipeline.py`：关键依赖、流水延迟和 uint32 拆分测试。

## 快速运行

在仓库根目录执行：

```bash
python3 scripts/mooncake_trace_to_dag.py \
  examples/sample_trace.jsonl \
  --config configs/mooncake_pd_store_config_v6_layer_pipeline.json \
  --out-dir mooncake_pd_store_dag_out

python3 scripts/task_dag_to_ub_traffic.py \
  mooncake_pd_store_dag_out/task_dag.csv \
  --output traffic.csv \
  --debug-output traffic_phase_debug.csv
```

第一步输出：

- `task_dag.csv`：应用级任务和依赖；
- `requests_debug.csv`：逐请求 HIT/MISS、P/D 放置和任务数量；
- `summary.json`：任务、流量和节点分布汇总。

第二步输出：

- `traffic.csv`：ns-3-UB 原生网络任务；
- `traffic_phase_debug.csv`：逐请求的 phase ID 和折叠延迟。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

完成 ns-3-UB 仿真后，可直接分析交换机热点：

```bash
python3 scripts/analyze_switch_hotspots.py \
  /mnt/liujiaxin/scratch/mooncake_pd_example_10k \
  --windows-us 100,1000,10000,100000 \
  --threshold-gbps 1600
```

如果分析 CSV 已经生成、只需补图，可使用独立脚本（不会重新读取 `runlog`）：

```bash
python3 scripts/plot_switch_hotspots.py \
  /path/to/ns3-case \
  --backend svg \
  --plot-window-us 1000 \
  --top-k 10
```

进一步检查每条物理400G输出端口是否饱和、并行lane是否失衡：

```bash
python3 scripts/analyze_port_hotspots.py \
  /mnt/yuhaoze/scratch/mooncake_pd_example_10k \
  --windows-us 100,1000,10000,100000 \
  --timeseries-window-us 1000

python3 scripts/plot_port_hotspots.py \
  /mnt/yuhaoze/scratch/mooncake_pd_example_10k/output/port_hotspots \
  --plot-window-us 1000
```

该绘图脚本只使用Python标准库。旧 trace 的任务流数字段会明确留空；仓库内置的
`ns-3-ub/` 已经会在新仿真的端口 trace 中记录 `TaskId`。详细定义见
[`docs/hotspot-analysis.md`](docs/hotspot-analysis.md)。

如果要统计报文实际经过的有向 `源L1 → 目的L1` 流量矩阵及同时活跃的任务流数量，
直接使用一键脚本重新仿真即可；内置 ns-3-UB 已经包含 L1-pair trace：

```bash
./scripts/run_mooncake_l1_pipeline.sh \
  --trace /path/to/mooncake_trace.jsonl \
  --case-dir /mnt/yuhaoze/scratch/mooncake_pd_l1trace_10k \
  --max-requests 10000
```

该方案在实际入口 L1 给报文添加轻量标签，并只在实际出口 L1 记录一次，因此支持当前
端点多归属拓扑，不会把 `L1→L2` 与 `L2→L1` 重复相加。流数量定义为窗口内 distinct
`taskId`，不是 routing hash-key 数量。

读写采用真实 LD/ST 发起关系：P/D 作为 `MEM_LOAD`/`MEM_STORE` 的发起方，Storage
作为目标。LOAD 在网络中包含 P/D→Storage 的小请求和 Storage→P/D 的数据响应；
STORE 包含 P/D→Storage 的数据和反向 ACK。

> 配置中的 `kv_bytes_per_token`、Prefill/Decode 时间和 Storage 延迟是可运行的
> 示例参数，不是从 trace 自动推导的实测值。正式实验前应按实际模型、精度和硬件校准。
