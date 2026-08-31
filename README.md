# Mooncake Trace → DAG → ns-3-UB Traffic

本仓库提供一条两阶段转换流水线，把 Mooncake JSONL 请求轨迹转换成
`ns-3-UB` 可读取的 `traffic.csv`：

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

> 配置中的 `kv_bytes_per_token`、Prefill/Decode 时间和 Storage 延迟是可运行的
> 示例参数，不是从 trace 自动推导的实测值。正式实验前应按实际模型、精度和硬件校准。

