# 快速开始

> 本项目基于 ns-3.44 构建，已在 Linux 与 Windows WSL 系统下验证。详细的平台支持、安装步骤、系统要求及编译选项，请参阅 [ns-3.44 文档](https://www.nsnam.org/releases/ns-3-44/documentation/)、[安装指南](https://www.nsnam.org/docs/release/3.44/installation/singlehtml/) 及 [ns-3.44 源码](https://gitlab.com/nsnam/ns-3-dev/-/tree/ns-3.44?ref_type=tags)。

## 环境要求

核心构建依赖如下工具。代码下载可通过 Git，或通过浏览器 / wget / curl 下载源码压缩包（tar + bunzip2 解压）。

| 目的       | 工具                          | 最低版本             |
| ---------- | ----------------------------- | -------------------- |
| 下载       | git（Git 下载）<br/>或：tar 与 bunzip2（Web 下载）               | 无最低版本要求       |
| 编译器     | g++<br/>或：clang++           | >= 10<br/>>= 11      |
| 配置       | python3                       | >= 3.8               |
| 构建系统   | cmake<br/>以及 make / ninja / Xcode 其一 | cmake >= 3.13<br/>无最低版本要求 |

如使用 Conda/virtualenv，请确保后续运行的 `python3` 与安装依赖的解释器一致。

### 快速检查版本

可在命令行中按下列方式检查版本：

| 工具    | 版本检查命令        |
| ------- | ------------------- |
| g++     | `g++ --version`     |
| clang++ | `clang++ --version` |
| python3 | `python3 -V`        |
| cmake   | `cmake --version`   |

## 获取代码

```bash
# 克隆项目
git clone https://gitcode.com/open-usim/ns-3-ub.git
cd ns-3-ub

# 初始化并更新子模块（包含 Python 分析工具）
git submodule update --init --recursive

# 如果上述命令失败，可以手动克隆：
# git clone https://gitcode.com/open-usim/ns-3-ub-tools.git scratch/ns-3-ub-tools

# 验证子模块状态
git submodule status
```

如需在仿真结束后自动触发 trace 分析，请在对应用例的 `network_attribute.txt` 中配置工具路径，例如：

```
global UB_PYTHON_SCRIPT_PATH "scratch/ns-3-ub-tools/trace_analysis/parse_trace.py"
```

## Python 工具与依赖

项目的 Python 工具集位于 `scratch/ns-3-ub-tools/`：

- 拓扑/可视化：`net_sim_builder.py`、`topo_plot.py`、`user_topo_*.py`
- 流量生成：`traffic_maker/*`
- Trace 分析：`trace_analysis/parse_trace.py`

依赖安装推荐使用项目内的 `requirements.txt`：

```bash
python3 -m pip install --user -r scratch/ns-3-ub-tools/requirements.txt
# 可能会遇到`externally-managed-environment`限制，此时请尝试使用虚拟环境
# 使用国内镜像源加速下载
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r scratch/ns-3-ub-tools/requirements.txt
# 或使用 conda：
conda install pandas matplotlib seaborn
# 或者手动安装以上依赖
```

说明： 请在运行 `trace_analysis/parse_trace.py` 前通过 `requirements.txt` 预先安装所需第三方包。

## 配置与编译

```bash
# 配置构建环境
./ns3 configure

# 编译项目
./ns3 build
```

## 运行简单示例

```bash
# 如使用 Conda，请确保其 bin 在 PATH 前（或先激活环境）
export PATH=/home/ytxing/miniconda3/bin:$PATH

# 安装依赖
python3 -m pip install --user -r scratch/ns-3-ub-tools/requirements.txt

# 运行小示例并触发 trace 分析
./ns3 run 'scratch/ub-quick-example scratch/2nodes_single-tp'

# 验证输出
ls scratch/2nodes_single-tp/output/
# 预期包含：task_statistics.csv  throughput.csv
```

如遇到 `ModuleNotFoundError: No module named 'pandas'`，说明运行时的 `python3` 与安装依赖所用解释器不一致；请检查 PATH，或使用 `python3 -m pip install --user ...` 在当前解释器中安装依赖。

## scratch 目录下的示例

以下为当前仓库中已提供的可用用例目录及对应运行命令：

- 2 节点（单 TP）：
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2nodes_single-tp'
  ```

- 2 节点（多 TP）：
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2nodes_multiple-tp'
  ```

- 2 节点（包喷洒）：
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2nodes_packet-spray'
  ```

- 2D FullMesh 4x4（多路径 All-to-All）：
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2dfm4x4-multipath_a2a'
  ```

- 2D FullMesh 4x4（分层广播）：
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/2dfm4x4-hierarchical_broadcast'
  ```

- Clos（32 hosts / 4 leafs / 8 spines, pod2pod）：
  ```bash
  ./ns3 run 'scratch/ub-quick-example scratch/clos_32hosts-4leafs-8spines_pod2pod'
  ```

说明：部分大型用例运行时间较长，请按需选择运行。

## 完整工作流程示例

```bash
# 运行完整示例，包含 Python 后处理
./ns3 run 'scratch/ub-quick-example scratch/2dfm4x4-multipath_a2a'

# 预期输出：
[01:23:37]:Run case: scratch/2dfm4x4-multipath_a2a
[01:23:37]:Set component attributes
[01:23:37]:Create node.
[01:23:37]:Start Client.
[01:23:37]:Simulator finished!
[01:23:37]:Start Parse Trace File.
所有依赖已满足，开始执行脚本...
处理完成，结果已保存到 scratch/2dfm4_4-multipath_a2a/output/task_statistics.csv
处理完成，结果已保存到 scratch/2dfm4_4-multipath_a2a/output/throughput.csv
[01:23:37]:Program finished.

# 查看生成的结果文件
ls scratch/2dfm4x4-multipath_a2a/output/
# task_statistics.csv  throughput.csv
```

## 配置文件说明

每个用例目录通常包含如下文件（格式可参照现有样例）：

- `network_attribute.txt` - 网络全局参数（可配置 `UB_PYTHON_SCRIPT_PATH` 用于自动后处理）
- `node.csv` - 节点定义
- `topology.csv` - 拓扑连接
- `routing_table.csv` - 路由表
- `transport_channel.csv` - 传输通道
- `traffic.csv` - 流量定义

更多配置细节与场景文件格式说明，请参见：[scratch/README.md](scratch/README.md)。
