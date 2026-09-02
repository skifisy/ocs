# ns-3-UB 交换机热点分析

`scripts/analyze_switch_hotspots.py` 从 ns-3-UB 的原始 `PortTrace` 中统计
交换机到交换机的实际承载流量。它不会把 `traffic.csv` 的应用字节直接当成链路流量，
而是使用仿真过程中真实发送的报文大小和时间戳，因此会自然包含协议头、ACK、控制报文、
路由选择和排队造成的时序变化。

## 1. 输入

脚本接收一个已经完成仿真的 case 目录：

```text
<case-dir>/
├── node.csv
├── topology.csv
├── traffic.csv
└── runlog/
    └── PortTrace_node_<node>_port_<port>.tr
```

`PortTrace` 必须包含 ns-3-UB 当前格式的发送记录：

```text
[123.45us] Port Tx, port ID: 67 PacketSize: 4096
```

脚本只统计 `Port Tx`。同一传输在对端产生的 `Port Rx` 不会再次累加，两个方向也会
分别统计。

## 2. 在 10k case 上运行

在 `ocs` 仓库根目录执行：

```bash
python3 scripts/analyze_switch_hotspots.py \
  /mnt/liujiaxin/scratch/mooncake_pd_example_10k \
  --windows-us 100,1000,10000,100000 \
  --threshold-gbps 1600 \
  --plot-window-us 1000 \
  --top-k 10
```

默认输出目录：

```text
/mnt/liujiaxin/scratch/mooncake_pd_example_10k/output/switch_hotspots
```

如果服务器没有 matplotlib，或者只需要 CSV：

```bash
python3 scripts/analyze_switch_hotspots.py \
  /mnt/liujiaxin/scratch/mooncake_pd_example_10k \
  --windows-us 100,1000,10000,100000 \
  --threshold-gbps 1600 \
  --no-plots
```

### 仿真分析完成后单独补图（无需重扫 PortTrace）

如果第一次运行时出现：

```text
[WARN] matplotlib is not installed; CSV outputs are complete, plots skipped.
```

说明 CSV/JSON 结果已经完整，不需要重新运行 ns-3，也不需要重新执行流量分析。
如果服务器的 Matplotlib/Pillow 无法导入，可以直接使用纯 SVG 后端，不安装任何包：

```bash
CASE=/mnt/yuhaoze/scratch/mooncake_pd_example_10k
python3 "$CASE/plot_switch_hotspots.py" \
  "$CASE" \
  --backend svg \
  --plot-window-us 1000 \
  --top-k 10
```

该后端只使用 Python 标准库，输出独立 SVG 图和 `hotspot_plots.html` 汇总页面；浏览器
可以直接打开，SVG也可以直接插入论文或PPT。`numpy`、`pandas`、Matplotlib和Pillow
均不是必需依赖。

如果仍希望生成 PNG，可以把 Matplotlib 安装在 case 内的私有目录（不需要 `sudo`）：

```bash
CASE=/mnt/yuhaoze/scratch/mooncake_pd_example_10k
mkdir -p "$CASE/.python_packages"
python3 -m pip install --target "$CASE/.python_packages" matplotlib
```

然后在 `ocs` 仓库根目录直接读取已有结果生成图：

```bash
python3 scripts/plot_switch_hotspots.py \
  "$CASE" \
  --matplotlib-path "$CASE/.python_packages" \
  --plot-window-us 1000 \
  --top-k 10
```

如果脚本已经复制到了 case 根目录，则使用：

```bash
python3 "$CASE/plot_switch_hotspots.py" \
  "$CASE" \
  --matplotlib-path "$CASE/.python_packages" \
  --plot-window-us 1000 \
  --top-k 10
```

也可以把第一个位置参数直接写成：
`$CASE/output/switch_hotspots`。默认图仍写到该分析目录，不会修改 CSV。

需要为全部已分析窗口分别绘图时，使用 `--all-windows`。该模式会多次读取较大的
`switch_bundle_timeseries.csv`，因此通常先画推荐的 1ms 主窗口。需要论文或报告中的
矢量图时，可加 `--formats png,pdf,svg`。

如果 trace 使用绝对时间，而只想分析其中一段：

```bash
python3 scripts/analyze_switch_hotspots.py \
  /mnt/liujiaxin/scratch/mooncake_pd_example_10k \
  --start-us 1000000 \
  --end-us 2000000
```

`--start-us` 为闭区间起点，`--end-us` 为开区间终点。

## 3. 输出文件

### `switch_hotspot_summary.csv`

每一行表示一个方向的交换机对在一个时间窗口下的汇总：

| 字段 | 含义 |
|---|---|
| `src_switch/dst_switch` | 有方向的交换机对 |
| `parallel_links` | 两台交换机之间的物理并行链路数 |
| `capacity_gbps` | 从 `topology.csv` 汇总的真实方向容量 |
| `threshold_gbps` | 比较阈值，默认 1600Gbps，即 4×400G |
| `average/p95/p99/max_gbps` | 实际承载吞吐率 |
| `p99/max_utilization` | 相对真实 bundle capacity 的利用率 |
| `hot_fraction` | 吞吐超过比较阈值的窗口比例 |
| `longest_hot_duration_us` | 连续超过比较阈值的最长时间 |
| `excess_bytes_above_threshold` | 按窗口计算的阈值以上承载字节 |
| `saturated_fraction` | 达到真实容量95%的窗口比例，可用参数修改 |
| `p99_max_lane_utilization` | bundle 内最热单条链路的99分位利用率 |
| `mean/min_jain_fairness_active` | 并行链路负载均衡程度，越接近1越均匀 |

最常用的查看命令：

```bash
head -n 21 output/switch_hotspots/switch_hotspot_summary.csv
```

文件已经按 `window_us`、`p99_gbps` 降序排列。

### `switch_bundle_timeseries.csv`

交换机对的稀疏时间序列。只输出存在 `Port Tx` 的窗口，未输出的窗口表示0流量。
除 bundle 总吞吐外，还包含：

- 当前窗口启用的物理 lane 数；
- 最热 lane 的吞吐和利用率；
- `lane_skew=max_lane/mean_lane`；
- Jain fairness。

这个文件用于画时间序列和进一步分析微突发。

### `physical_link_summary.csv`

每条有方向的物理400G链路的总流量、平均吞吐、最大小窗口吞吐和活跃窗口数。
如果对应 `PortTrace` 文件不存在，脚本将它视为零流量链路，并标记
`trace_file_exists=0`。

### `traffic_endpoint_summary.csv`

按 `sourceNode,destNode,opType` 汇总 `traffic.csv` 的应用负载。它用于解释端点需求，
不能替代 `PortTrace` 的链路统计。

### `analysis_summary.json`

保存本次分析参数、时间范围、日志异常数，以及每个时间窗口下 P99 最大的交换机对。
自动化流程应优先读取这个文件判断分析是否完整。

### PNG 图

- `hotspot_topk_<window>us.png`：Top-K P99/最大吞吐、1.6Tbps阈值和真实容量；
- `hotspot_timeseries_<window>us.png`：Top-K交换机对时间序列；
- `leaf_to_spine_p99_heatmap_<window>us.png`：Leaf→Spine P99矩阵；
- `spine_to_leaf_p99_heatmap_<window>us.png`：Spine→Leaf P99矩阵。
- `hotspot_window_comparison_<window>us_top.png`：主窗口 Top-K 在所有时间尺度下的
  P99对比，用于判断热点是微突发还是持续热点。
- `plot_manifest.json`：独立绘图脚本记录的参数和图文件列表。
- `hotspot_plots.html`：纯SVG后端生成的全部图表汇总页面。

图表只使用测量结果，不会填充或生成估计数据。

## 4. 指标解释

### 超过4×400G，不等于超过当前物理容量

默认比较阈值为：

```text
4 × 400Gbps = 1600Gbps
```

当前拓扑的一个 Leaf–Spine 对通常有6或7条400G链路，因此真实容量可能是
2400或2800Gbps。比如 P99=1800Gbps 表示该交换机对需要超过四条400G链路，
但未必已经让当前电交换 bundle 过载。是否过载要看 `p99_utilization`、
`saturated_fraction` 和队列。

### 短窗口可能略微超过理论容量

`Port Tx` 在报文开始发送时记录整个报文大小。报文可能跨越窗口边界，因此在非常短的
窗口中可能出现轻微的 `utilization>1`。判断持续过载时应同时查看1ms、10ms以上窗口，
不要只使用一个100us最大值。

### carried load 不是 offered load

本脚本分析的是已经从端口发出的 carried load。若真实容量只有1600Gbps，实际发送速率
会被限制在1600Gbps附近，超额需求会进入队列，而不会继续出现在 Port Tx 吞吐中。

因此容量不足的完整证据是：

1. carried load 长时间接近真实容量；
2. egress queue 持续增长；
3. task/phase 完成时间恶化。

当前已经完成的仿真如果没有记录队列，仍可先用本脚本定位热点交换机对；下一轮仿真再对
Top-K端口增加 `UbQueueManager` 的 Push/Pop/queue-bytes trace。

## 5. 物理端口饱和与同时活跃的任务流

交换机对聚合结果用于判断总需求，端口结果用于判断单条400G lane是否打满，以及并行
lane是否失衡。端口分析与画图是两个独立步骤；画图不会重新扫描数亿条 `PortTrace`。

把以下三个脚本放在同一目录，因为端口分析器会复用交换机分析器的拓扑和 trace 解析：

```text
analyze_switch_hotspots.py
analyze_port_hotspots.py
plot_port_hotspots.py
```

对已经完成的10k仿真直接运行：

```bash
CASE=/mnt/yuhaoze/scratch/mooncake_pd_example_10k

python3 "$CASE/analyze_port_hotspots.py" "$CASE" \
  --windows-us 100,1000,10000,100000 \
  --timeseries-window-us 1000 \
  --saturation-ratio 0.95 \
  --spare-ratio 0.50
```

该步骤默认读取已有的 `output/switch_hotspots/analysis_summary.json` 作为全局时间边界，
因此不需要先额外扫描一次所有端口。默认输出到 `output/port_hotspots`：

| 文件 | 含义 |
|---|---|
| `physical_port_hotspot_summary.csv` | 每个有向物理端口在各窗口下的 P50/P95/P99/max、饱和占比和最长连续饱和时长 |
| `physical_port_timeseries.csv` | `--timeseries-window-us` 指定窗口下的稀疏端口序列，缺行表示0 |
| `bundle_lane_balance_summary.csv` | 同一交换机对的并行lane失衡持续性 |
| `bundle_lane_balance_timeseries.csv` | 饱和lane数、空闲lane数、Jain fairness和最热端口 |
| `port_analysis_summary.json` | 配置、trace覆盖率、异常数和各窗口最热端口 |

随后只读CSV生成纯SVG图，不依赖 Matplotlib、Pillow、NumPy或pandas：

```bash
python3 "$CASE/plot_port_hotspots.py" \
  "$CASE/output/port_hotspots" \
  --plot-window-us 1000 \
  --top-k 20 \
  --timeseries-top-k 8
```

图输出到 `output/port_hotspots/plots`，其中：

- `port_topk_1000us.svg`：端口 P99、最大速率、95%阈值和真实容量；
- `port_p99_heatmap_1000us.svg`：交换机×输出端口的 P99 Gbps 热力图；
- `port_saturation_heatmap_1000us.svg`：端口饱和窗口比例；
- `port_timeseries_1000us.svg`：最热端口时序；
- `bundle_lane_imbalance_1000us.svg`：一条lane饱和、同bundle另一条lane仍有余量的持续比例；
- `port_hotspot_plots.html`：全部图的浏览器汇总页。

### 当前已完成仿真的流计数为什么为空

旧格式 `PortTrace` 只有时间、端口、方向和包长；当前 case 又设置
`UB_RECORD_PKT_TRACE=false`，因此不能从包数量反推同时活跃的流数量。分析器会把
`active_task_flow_count_*` 留空并令 `flow_metrics_available=0`，不会生成伪造数字。

这里还要区分两个概念：

- `active_task_flow_count`：一个窗口内至少有一个带标签数据包从该端口发出的 distinct
  `TaskId` 数量；
- `active_hash_key_count`：ECMP真正使用的 route/hash key 数量。

二者不等价。尤其该 case 的 LDST 设置了 `UsePacketSpray=true`，一个 `TaskId` 在同一
窗口内可能出现在多个并行端口；而 URMA 的 `UsePacketSpray=false` 会把 sport/dport
清零后按 `(sip,dip,priority)` 哈希。当前 trace 没有完整 routing key，所以
`active_hash_key_count` 必须继续为空。

### 下一轮仿真记录轻量 TaskId（不打开全路径 trace）

仓库内置的 `ns-3-ub/` 已在现有 `Port Tx` 行尾追加 `TaskId: <id>`；无
`UbFlowTag` 的控制包写为 `TaskId: NA`。这比 `UB_RECORD_PKT_TRACE=true` 的逐包全路径
日志轻得多，不需要应用补丁。`patches/` 仅保留给独立上游工作树做历史参考。

```bash
cd /path/to/ocs/ns-3-ub
./ns3 configure -d release --disable-examples --disable-tests
./ns3 build
```

下一轮仍可保留：

```text
global UB_RECORD_PKT_TRACE "false"
```

重跑后，同一个端口分析命令会自动识别新后缀并输出：

- 每窗口 `active_task_flow_count`；
- 每端口 P50/P95/P99/max 活跃任务流数；
- 每bundle的 distinct 活跃任务流数；
- `port_load_vs_task_flows_<window>us.svg`，用于区分“少数大流打满端口”和“许多并发小流聚合打满端口”。

“同时活跃”在这里采用可复现的窗口定义：该 `TaskId` 在窗口内至少发送一个包。它不是
任务生命周期定义，也不是ECMP hash-key计数。

## 6. 有向 L1→L1 流量矩阵与同时活跃任务流

当前拓扑中的端点不是静态连接到唯一 L1：计算端点连接同 Group 的4台 L1，存储端点
也连接4台存储 L1。因此不能只根据 `traffic.csv` 的 `sourceNode/destNode` 推导实际
`src_l1/dst_l1`。ECMP或packet spray选择不同端口时，静态映射会产生错误结果。

内置 `ns-3-ub/` 的 L1-pair trace 使用以下方法：

1. 报文第一次由端点进入 L1 时，写入4字节 `UbL1TraceTag(srcL1)`；
2. 报文从实际出口 L1 发向目的端点时，写一条 `L1PairTrace`；
3. 中间的 `L1→L2` 和 `L2→L1` 不计入逻辑 L1 对，避免同一报文重复计数；
4. LDST ACK继承原始 `UbFlowTag`，使反向报文也可关联 `taskId`。

Trace格式为：

```text
[123.45us] L1 Pair Tx, SrcL1: 37 DstL1: 53 PacketUid: 1001 PacketSize: 4096 TaskId: 7
```

无任务标签的控制包保留字节统计，并写为 `TaskId: NA`。

### 6.1 使用内置 ns-3-UB 重新仿真

内置源码已经同时包含端口 `TaskId` 后缀、L1 对 Trace，以及真实 `MEM_LOAD`/
`MEM_STORE` 所需的 LD/ST 初始化与 priority 传播修复。不要再重复应用 `patches/`：

```bash
./scripts/run_mooncake_l1_pipeline.sh \
  --trace /path/to/mooncake_trace.jsonl \
  --case-dir /mnt/yuhaoze/scratch/mooncake_pd_l1trace_10k \
  --max-requests 10000
```

仍可保持：

```text
global UB_RECORD_PKT_TRACE "false"
```

`L1PairTrace` 受已有 `UB_TRACE_ENABLE` 控制，不要求开启巨量 `AllPacketTrace`。

### 6.2 统计命令

将下面三个脚本放在同一目录：

```text
analyze_switch_hotspots.py
analyze_l1_pair_hotspots.py
plot_l1_pair_hotspots.py
```

执行：

```bash
CASE=/mnt/yuhaoze/scratch/mooncake_pd_example_10k

python3 analyze_l1_pair_hotspots.py "$CASE" \
  --windows-us 100,1000,10000,100000 \
  --timeseries-window-us 1000 \
  --timeseries-top-k 20
```

默认输出到 `output/l1_pair_hotspots`：

| 文件 | 含义 |
|---|---|
| `l1_pair_summary.csv` | 全部有向 L1 对的总字节、平均/P50/P95/P99/max Gbps、全程 distinct task 数、窗口并发 task 数和流量占比 |
| `l1_pair_timeseries.csv` | 主窗口下 P99 最热 Top-K L1 对的稀疏时序，默认Top-20 |
| `l1_pair_analysis.json` | L1节点/Group、Trace覆盖率、异常记录和各窗口最热L1对 |

关键字段：

| 字段 | 定义 |
|---|---|
| `total_tx_bytes` | 该有向 L1 对实际交付的总线上字节，每个端到端报文只计一次 |
| `p99_gbps/max_gbps` | 时间窗口内该 L1 对的P99/最大carried throughput |
| `distinct_task_flow_count` | 全观察区间在该方向出现过的不同taskId总数 |
| `active_task_flow_count_p99/max` | 单窗口同时至少发送一个包的distinct taskId数量 |
| `share_of_src_l1_traffic` | 该窗口中该L1对占源L1全部跨L1流量的比例 |
| `share_of_dst_l1_traffic` | 该窗口中该L1对占目的L1全部入向流量的比例 |
| `unattributed_bytes` | 有L1对但没有taskId的控制报文字节 |

这里的“同时活跃流”严格指窗口内 distinct `taskId`，不等于ECMP hash-key。LDST开启
packet spray时，一个task的不同报文可能经过不同Spine，但在同一个 `src_l1→dst_l1`
窗口中只计一个任务流。

### 6.3 纯SVG图

```bash
python3 plot_l1_pair_hotspots.py \
  "$CASE/output/l1_pair_hotspots" \
  --plot-window-us 1000 \
  --top-k 20 \
  --timeseries-top-k 8
```

生成：

- `l1_pair_p99_traffic_heatmap_1000us.svg`：横轴目的L1、纵轴源L1、颜色P99 Gbps；
- `l1_pair_p99_task_flows_heatmap_1000us.svg`：同一矩阵上的P99并发task数；
- `l1_pair_topk_1000us.svg`：最热L1对的P99、最大吞吐和并发task数；
- `l1_pair_traffic_vs_flows_1000us.svg`：吞吐与并发流数联合散点图；
- `l1_pair_timeseries_1000us.svg`：Top L1对吞吐/并发task双面板时序；
- `l1_pair_hotspot_plots.html`：浏览器汇总页。

最能说明问题的组合不是“流很多”本身，而是：某些固定 L1 对同时具有较高P99/持续
吞吐、较多并发大任务，并且占源或目的 L1 总流量比例明显偏高。随后仍需结合400G物理
端口饱和、队列和持续时间，区分逻辑通信热点、ECMP lane失衡与网络总容量不足。

## 7. 性能和空间

脚本不会一次性加载所有 `runlog`。它按照有方向的交换机对处理，每次只保存该 bundle
的6或7条端口时间桶，然后立即写出结果。

最小时间窗口决定内存和输出规模：

- 100us：适合微突发，输出最大；
- 1ms：推荐作为主结果；
- 10ms/100ms：判断热点是否持续、是否适合 OCS 静态/慢速重配。

如果100us输出过大，可先运行：

```bash
--windows-us 1000,10000,100000
```

确认 Top-K 后，再使用 `--start-us/--end-us` 对热点区间进行100us细化分析。
