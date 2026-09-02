#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
NS3_DIR="$ROOT_DIR/ns-3-ub"
DEFAULT_MODEL_CONFIG="$ROOT_DIR/configs/mooncake_pd_store_config_v6_layer_pipeline.json"
NS3_CASE_CONFIG_DIR="$ROOT_DIR/configs/ns3_mooncake_l1_trace"

TRACE_PATH=""
CASE_DIR=""
MODEL_CONFIG="$DEFAULT_MODEL_CONFIG"
MAX_REQUESTS=""
WARMUP_REQUESTS=0
WINDOWS_US="100,1000,10000,100000"
PLOT_WINDOW_US=1000
TOP_K=20
JOBS=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
PREPARE_ONLY=0
ALL_ANALYSES=0

usage() {
    cat <<'EOF'
Run the complete Mooncake -> ns-3-UB -> L1-pair analysis pipeline.

Usage:
  scripts/run_mooncake_l1_pipeline.sh --trace TRACE.jsonl [options]

Required:
  --trace PATH              Mooncake JSONL input trace.

Options:
  --case-dir PATH           New/empty ns-3-UB case directory.
                            Default: runs/<trace-name>_l1_case
  --config PATH             Mooncake model config JSON.
  --max-requests N          Process at most N trace requests.
  --warmup-requests N       Warm-up requests (default: 0).
  --windows-us LIST         Analysis windows (default: 100,1000,10000,100000).
  --plot-window-us N        Window used in plots (default: 1000).
  --top-k N                 L1 pairs retained in time series (default: 20).
  --jobs N                  Parallel compiler jobs.
  --all-analyses            Also run switch-bundle and physical-port analyses.
  --prepare-only            Stop after generating the complete ns-3 case.
  -h, --help                Show this help.

The case directory is never deleted or overwritten. Choose a new directory for
each run. The lightweight L1PairTrace is enabled; the very large AllPacketTrace
remains disabled.
EOF
}

die() {
    echo "[ERROR] $*" >&2
    exit 1
}

need_value() {
    [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --trace)
            need_value "$@"
            TRACE_PATH=$2
            shift 2
            ;;
        --case-dir)
            need_value "$@"
            CASE_DIR=$2
            shift 2
            ;;
        --config)
            need_value "$@"
            MODEL_CONFIG=$2
            shift 2
            ;;
        --max-requests)
            need_value "$@"
            MAX_REQUESTS=$2
            shift 2
            ;;
        --warmup-requests)
            need_value "$@"
            WARMUP_REQUESTS=$2
            shift 2
            ;;
        --windows-us)
            need_value "$@"
            WINDOWS_US=$2
            shift 2
            ;;
        --plot-window-us)
            need_value "$@"
            PLOT_WINDOW_US=$2
            shift 2
            ;;
        --top-k)
            need_value "$@"
            TOP_K=$2
            shift 2
            ;;
        --jobs)
            need_value "$@"
            JOBS=$2
            shift 2
            ;;
        --all-analyses)
            ALL_ANALYSES=1
            shift
            ;;
        --prepare-only)
            PREPARE_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1 (use --help)"
            ;;
    esac
done

[[ -n "$TRACE_PATH" ]] || die "--trace is required"
[[ -f "$TRACE_PATH" ]] || die "trace not found: $TRACE_PATH"
[[ -f "$MODEL_CONFIG" ]] || die "model config not found: $MODEL_CONFIG"
[[ -x "$NS3_DIR/ns3" ]] || die "bundled ns-3-UB runner not found: $NS3_DIR/ns3"
[[ "$WARMUP_REQUESTS" =~ ^[0-9]+$ ]] || die "--warmup-requests must be a non-negative integer"
[[ "$PLOT_WINDOW_US" =~ ^[0-9]+$ && "$PLOT_WINDOW_US" -gt 0 ]] || die "--plot-window-us must be positive"
[[ "$TOP_K" =~ ^[0-9]+$ && "$TOP_K" -gt 0 ]] || die "--top-k must be positive"
[[ "$JOBS" =~ ^[0-9]+$ && "$JOBS" -gt 0 ]] || die "--jobs must be positive"
if [[ -n "$MAX_REQUESTS" ]]; then
    [[ "$MAX_REQUESTS" =~ ^[0-9]+$ && "$MAX_REQUESTS" -gt 0 ]] || \
        die "--max-requests must be positive"
fi

TRACE_PATH=$(readlink -f "$TRACE_PATH")
MODEL_CONFIG=$(readlink -f "$MODEL_CONFIG")
if [[ -z "$CASE_DIR" ]]; then
    trace_name=$(basename -- "$TRACE_PATH")
    trace_stem=${trace_name%.*}
    CASE_DIR="$ROOT_DIR/runs/${trace_stem}_l1_case"
fi
mkdir -p -- "$CASE_DIR"
CASE_DIR=$(readlink -f "$CASE_DIR")
if [[ -n "$(find "$CASE_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    die "case directory is not empty: $CASE_DIR; choose a new directory"
fi

command -v python3 >/dev/null || die "python3 is required"

echo "[1/8] Mooncake trace -> task DAG"
dag_cmd=(
    python3 "$ROOT_DIR/scripts/mooncake_trace_to_dag.py"
    "$TRACE_PATH"
    --config "$MODEL_CONFIG"
    --warmup-requests "$WARMUP_REQUESTS"
    --out-dir "$CASE_DIR/dag"
)
if [[ -n "$MAX_REQUESTS" ]]; then
    dag_cmd+=(--max-requests "$MAX_REQUESTS")
fi
"${dag_cmd[@]}"

echo "[2/8] Generate the Mooncake Clos topology"
python3 "$ROOT_DIR/scripts/generate_topology.py" --case-dir "$CASE_DIR"

echo "[3/8] Generate shortest-path ECMP routing"
python3 "$ROOT_DIR/scripts/generate_routing_table.py" \
    --case-dir "$CASE_DIR" \
    --debug-plan "$CASE_DIR/routing_debug.csv"

echo "[4/8] DAG -> ns-3-UB traffic and case configuration"
python3 "$ROOT_DIR/scripts/task_dag_to_ub_traffic.py" \
    "$CASE_DIR/dag/task_dag.csv" \
    --output "$CASE_DIR/traffic.csv" \
    --debug-output "$CASE_DIR/traffic_phase_debug.csv"
cp -- "$NS3_CASE_CONFIG_DIR/network_attribute.txt" "$CASE_DIR/network_attribute.txt"
cp -- "$NS3_CASE_CONFIG_DIR/transport_channel.csv" "$CASE_DIR/transport_channel.csv"

{
    printf 'trace=%s\n' "$TRACE_PATH"
    printf 'model_config=%s\n' "$MODEL_CONFIG"
    printf 'case_dir=%s\n' "$CASE_DIR"
    printf 'max_requests=%s\n' "${MAX_REQUESTS:-all}"
    printf 'warmup_requests=%s\n' "$WARMUP_REQUESTS"
} >"$CASE_DIR/pipeline_inputs.txt"

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
    echo "[DONE] Case prepared without building or simulation: $CASE_DIR"
    exit 0
fi

command -v cmake >/dev/null || die "cmake is required to build ns-3-UB"
command -v c++ >/dev/null || die "a C++ compiler is required to build ns-3-UB"

echo "[5/8] Configure and build bundled ns-3-UB"
(
    cd "$NS3_DIR"
    ./ns3 configure -d release --disable-examples --disable-tests
    ./ns3 build -j "$JOBS"
) 2>&1 | tee "$CASE_DIR/ns3_build.log"

echo "[6/8] Run ns-3-UB simulation"
(
    cd "$NS3_DIR"
    ./ns3 run --no-build scratch/ub-quick-example -- "$CASE_DIR"
) 2>&1 | tee "$CASE_DIR/ns3_run.log"

L1_TRACE="$CASE_DIR/runlog/L1PairTrace.tr"
[[ -s "$L1_TRACE" ]] || die "simulation did not produce a non-empty $L1_TRACE"

echo "[7/8] Analyze directed L1-pair traffic and active task flows"
python3 "$ROOT_DIR/scripts/analyze_l1_pair_hotspots.py" "$CASE_DIR" \
    --windows-us "$WINDOWS_US" \
    --timeseries-window-us "$PLOT_WINDOW_US" \
    --timeseries-top-k "$TOP_K"

echo "[8/8] Render SVG/HTML reports"
python3 "$ROOT_DIR/scripts/plot_l1_pair_hotspots.py" \
    "$CASE_DIR/output/l1_pair_hotspots" \
    --plot-window-us "$PLOT_WINDOW_US" \
    --top-k "$TOP_K" \
    --timeseries-top-k "$TOP_K"

if [[ "$ALL_ANALYSES" -eq 1 ]]; then
    echo "[extra] Analyze switch bundles"
    python3 "$ROOT_DIR/scripts/analyze_switch_hotspots.py" "$CASE_DIR" \
        --windows-us "$WINDOWS_US" \
        --threshold-gbps 1600
    python3 "$ROOT_DIR/scripts/plot_switch_hotspots.py" "$CASE_DIR" \
        --backend svg \
        --plot-window-us "$PLOT_WINDOW_US" \
        --top-k "$TOP_K"

    echo "[extra] Analyze physical ports"
    python3 "$ROOT_DIR/scripts/analyze_port_hotspots.py" "$CASE_DIR" \
        --windows-us "$WINDOWS_US" \
        --timeseries-window-us "$PLOT_WINDOW_US"
    python3 "$ROOT_DIR/scripts/plot_port_hotspots.py" \
        "$CASE_DIR/output/port_hotspots" \
        --plot-window-us "$PLOT_WINDOW_US" \
        --top-k "$TOP_K"
fi

echo
echo "[DONE] End-to-end pipeline completed"
echo "Case:       $CASE_DIR"
echo "CSV report: $CASE_DIR/output/l1_pair_hotspots/l1_pair_summary.csv"
echo "HTML plots: $CASE_DIR/output/l1_pair_hotspots/plots/l1_pair_hotspot_plots.html"
