# 从 Mooncake trace 一键重跑

## 1. 最短命令

```bash
git clone https://github.com/skifisy/ocs.git
cd ocs

./scripts/run_mooncake_l1_pipeline.sh \
  --trace /path/to/mooncake_trace.jsonl \
  --case-dir /mnt/yuhaoze/scratch/mooncake_pd_l1trace_10k \
  --max-requests 10000 \
  --jobs 16
```

`--case-dir` 必须指向不存在或为空的目录。ns-3-UB 每次仿真都会重建
`runlog/`，所以脚本拒绝复用非空目录，避免误删以前的日志。

## 2. 实际执行链

1. `mooncake_trace_to_dag.py`：JSONL 请求轨迹转 task DAG；
2. `generate_topology.py`：生成 37 个端点、20 个 L1、10 个 L2 的 400G Clos；
3. `generate_routing_table.py`：生成最短路径 ECMP 路由；
4. `task_dag_to_ub_traffic.py`：生成 ns-3-UB `traffic.csv`；
5. 复制轻量 trace 配置，保持 `UB_RECORD_PKT_TRACE=false`；
6. 编译仓库内置的 `ns-3-ub/` 并运行 `scratch/ub-quick-example`；
7. 从 `L1PairTrace.tr` 统计有向 L1 对流量和窗口内 distinct TaskId；
8. 生成 CSV、JSON、SVG 和 HTML 报告。

读取型任务使用 ns-3-UB 的真实 LD/ST 语义建模：P 或 D 是 `MEM_LOAD` 的
source/requester，Storage 是 destination/target。网络中先出现 P/D→Storage 的小型
Read 请求，再出现 Storage→P/D 的 KV 数据响应。写入则使用 `MEM_STORE`，由 P/D
携带 KV 数据发往 Storage，Storage 返回 ACK。

## 3. 关键输出

```text
<case>/
├── dag/task_dag.csv
├── traffic.csv
├── node.csv
├── topology.csv
├── routing_table.csv
├── runlog/L1PairTrace.tr
└── output/l1_pair_hotspots/
    ├── l1_pair_summary.csv
    ├── l1_pair_timeseries.csv
    ├── l1_pair_analysis.json
    └── plots/l1_pair_hotspot_plots.html
```

`l1_pair_summary.csv` 同时包含：

- L1 对的总字节、平均/P50/P95/P99/最大 Gbps；
- 整轮仿真的 distinct TaskId 数；
- 每个统计窗口的 P50/P95/P99/最大活跃 TaskId 数。

这里的“流数量”是窗口内 distinct `taskId`，不是五元组或 routing hash-key
数量。一个 task 即使逐包经过不同 Spine，也只在相应 L1 对/窗口内计一次。

## 4. 常用选项

```bash
# 只生成 case，不编译、不仿真；适合先检查输入
./scripts/run_mooncake_l1_pipeline.sh \
  --trace examples/sample_trace.jsonl \
  --case-dir /tmp/mooncake_prepare \
  --prepare-only

# 同时扫描 PortTrace，补充交换机链路束和单400G端口报告
./scripts/run_mooncake_l1_pipeline.sh \
  --trace /path/to/mooncake_trace.jsonl \
  --case-dir /mnt/yuhaoze/scratch/mooncake_pd_all_metrics \
  --max-requests 10000 \
  --all-analyses
```

完整参数：

```bash
./scripts/run_mooncake_l1_pipeline.sh --help
```

## 5. ns-3-UB 版本

内置源码位于 `ns-3-ub/`，基于上游 commit
`d25d6504f8e9a13a418125b82f84ef775eada39c`。L1-pair 和 TaskId trace 已经合入，
不要再次应用 `patches/ns3-ub-l1-pair-trace.patch`。具体修改与许可证说明见
`ns-3-ub/OCS_INTEGRATION.md` 和 `ns-3-ub/LICENSE`。
