#!/usr/bin/env python3
"""Analyze switch-to-switch hotspots from ns-3-UB PortTrace logs.

The analyzer treats ``Port Tx`` events as carried wire traffic.  It maps every
``(node, port)`` back to its peer through ``topology.csv``, keeps only links
whose two endpoints are SWITCH nodes, and aggregates parallel physical links
into a directional switch-pair bundle.

The implementation is deliberately streaming at the raw-log level: one
switch-pair bundle (normally six or seven PortTrace files) is kept in memory at
a time.  This makes it practical to process multi-gigabyte ``runlog`` trees.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple


TX_LINE_RE = re.compile(
    rb'^\[\s*([0-9eE+\-.]+)us\]\s+Port Tx,\s*port ID:\s*(\d+)\s+'
    rb'PacketSize:\s*(\d+)'
)
RATE_RE = re.compile(r'^\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmMgGtT]?)bps\s*$')


@dataclass(frozen=True)
class Node:
    node_id: int
    node_type: str
    port_num: int


@dataclass(frozen=True)
class PhysicalLink:
    node_a: int
    port_a: int
    node_b: int
    port_b: int
    rate_gbps: float


@dataclass(frozen=True)
class DirectedLane:
    src_node: int
    src_port: int
    dst_node: int
    dst_port: int
    rate_gbps: float


@dataclass
class TraceBins:
    bins: Dict[int, int]
    total_bytes: int = 0
    tx_events: int = 0
    first_time_us: Optional[float] = None
    last_time_us: Optional[float] = None
    malformed_tx_lines: int = 0
    wrong_port_lines: int = 0
    file_exists: bool = False


def parse_node_range(text: str) -> List[int]:
    text = text.strip()
    if '..' not in text:
        return [int(text)]
    left, right = text.split('..', 1)
    start = int(left)
    end = int(right)
    if end < start:
        raise ValueError(f'invalid node range: {text}')
    return list(range(start, end + 1))


def read_nodes(path: Path) -> Dict[int, Node]:
    nodes: Dict[int, Node] = {}
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'nodeId', 'nodeType', 'portNum'}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path} must contain columns {sorted(required)}')
        for row in reader:
            node_type = row['nodeType'].strip().upper()
            port_num = int(row['portNum'])
            for node_id in parse_node_range(row['nodeId']):
                if node_id in nodes:
                    raise ValueError(f'duplicate node ID {node_id} in {path}')
                nodes[node_id] = Node(node_id, node_type, port_num)
    if not nodes:
        raise ValueError(f'{path} contains no nodes')
    return nodes


def parse_rate_gbps(text: str) -> float:
    match = RATE_RE.match(text)
    if not match:
        raise ValueError(f'unsupported data rate: {text!r}')
    value = float(match.group(1))
    prefix = match.group(2).upper()
    scale = {'': 1e-9, 'K': 1e-6, 'M': 1e-3, 'G': 1.0, 'T': 1e3}[prefix]
    return value * scale


def read_topology(path: Path, nodes: Mapping[int, Node]) -> List[PhysicalLink]:
    links: List[PhysicalLink] = []
    used_ports = set()
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'nodeId1', 'portId1', 'nodeId2', 'portId2', 'bandwidth'}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path} must contain columns {sorted(required)}')
        for line_number, row in enumerate(reader, 2):
            node_a = int(row['nodeId1'])
            port_a = int(row['portId1'])
            node_b = int(row['nodeId2'])
            port_b = int(row['portId2'])
            if node_a not in nodes or node_b not in nodes:
                raise ValueError(f'{path}:{line_number}: unknown node on {node_a}<->{node_b}')
            for node_id, port_id in ((node_a, port_a), (node_b, port_b)):
                if not 0 <= port_id < nodes[node_id].port_num:
                    raise ValueError(
                        f'{path}:{line_number}: node {node_id} port {port_id} out of range')
                if (node_id, port_id) in used_ports:
                    raise ValueError(
                        f'{path}:{line_number}: node {node_id} port {port_id} reused')
                used_ports.add((node_id, port_id))
            links.append(PhysicalLink(
                node_a, port_a, node_b, port_b,
                parse_rate_gbps(row['bandwidth'])))
    if not links:
        raise ValueError(f'{path} contains no links')
    return links


def classify_switch_roles(
    nodes: Mapping[int, Node], links: Sequence[PhysicalLink]
) -> Dict[int, str]:
    """Classify endpoint-facing switches as LEAF and switch-only nodes as SPINE."""
    has_device_neighbor = defaultdict(bool)
    for link in links:
        if nodes[link.node_a].node_type == 'SWITCH' and nodes[link.node_b].node_type == 'DEVICE':
            has_device_neighbor[link.node_a] = True
        if nodes[link.node_b].node_type == 'SWITCH' and nodes[link.node_a].node_type == 'DEVICE':
            has_device_neighbor[link.node_b] = True
    return {
        node_id: ('LEAF' if has_device_neighbor[node_id] else 'SPINE')
        for node_id, node in nodes.items() if node.node_type == 'SWITCH'
    }


def build_directed_bundles(
    nodes: Mapping[int, Node], links: Sequence[PhysicalLink]
) -> Dict[Tuple[int, int], List[DirectedLane]]:
    bundles: Dict[Tuple[int, int], List[DirectedLane]] = defaultdict(list)
    for link in links:
        if not (
            nodes[link.node_a].node_type == 'SWITCH'
            and nodes[link.node_b].node_type == 'SWITCH'
        ):
            continue
        bundles[(link.node_a, link.node_b)].append(DirectedLane(
            link.node_a, link.port_a, link.node_b, link.port_b, link.rate_gbps))
        bundles[(link.node_b, link.node_a)].append(DirectedLane(
            link.node_b, link.port_b, link.node_a, link.port_a, link.rate_gbps))
    for lanes in bundles.values():
        lanes.sort(key=lambda lane: (lane.src_port, lane.dst_port))
    if not bundles:
        raise ValueError('topology contains no SWITCH-to-SWITCH links')
    return dict(bundles)


def parse_windows(text: str) -> List[int]:
    windows = sorted({int(part.strip()) for part in text.split(',') if part.strip()})
    if not windows or windows[0] <= 0:
        raise ValueError('--windows-us must contain positive integer microsecond windows')
    base = windows[0]
    bad = [window for window in windows if window % base != 0]
    if bad:
        raise ValueError(
            f'every window must be an integer multiple of the smallest window {base}us; got {bad}')
    return windows


def throughput_gbps(num_bytes: int, window_us: int) -> float:
    return num_bytes * 8.0 / (window_us * 1000.0)


def trace_path(runlog_dir: Path, node_id: int, port_id: int) -> Path:
    return runlog_dir / f'PortTrace_node_{node_id}_port_{port_id}.tr'


def parse_port_tx_bins(
    path: Path,
    expected_port: int,
    base_window_us: int,
    start_us: Optional[float] = None,
    end_us: Optional[float] = None,
) -> TraceBins:
    result = TraceBins(bins={})
    if not path.exists():
        return result
    result.file_exists = True
    bins: Dict[int, int] = defaultdict(int)
    with path.open('rb', buffering=1024 * 1024) as f:
        for line in f:
            if b'Port Tx' not in line:
                continue
            match = TX_LINE_RE.match(line)
            if not match:
                result.malformed_tx_lines += 1
                continue
            time_us = float(match.group(1))
            port_id = int(match.group(2))
            packet_size = int(match.group(3))
            if port_id != expected_port:
                result.wrong_port_lines += 1
                continue
            if start_us is not None and time_us < start_us:
                continue
            if end_us is not None and time_us >= end_us:
                continue
            bin_index = int(math.floor(time_us / base_window_us + 1e-12))
            bins[bin_index] += packet_size
            result.total_bytes += packet_size
            result.tx_events += 1
            if result.first_time_us is None or time_us < result.first_time_us:
                result.first_time_us = time_us
            if result.last_time_us is None or time_us > result.last_time_us:
                result.last_time_us = time_us
    result.bins = dict(bins)
    return result


def roll_bins(bins: Mapping[int, int], factor: int) -> Dict[int, int]:
    if factor == 1:
        return dict(bins)
    rolled: Dict[int, int] = defaultdict(int)
    for bin_index, num_bytes in bins.items():
        rolled[bin_index // factor] += num_bytes
    return dict(rolled)


def jain_fairness(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    total = sum(values)
    squares = sum(value * value for value in values)
    if total == 0 or squares == 0:
        return 0.0
    return total * total / (len(values) * squares)


def sparse_percentile(nonzero_values: Sequence[float], total_count: int, quantile: float) -> float:
    """Linear percentile over ``nonzero_values`` plus implicit zero samples."""
    if total_count <= 0:
        return 0.0
    if not 0.0 <= quantile <= 1.0:
        raise ValueError('quantile must be in [0, 1]')
    ordered = sorted(value for value in nonzero_values if value > 0)
    zero_count = max(0, total_count - len(ordered))

    def value_at(index: int) -> float:
        if index < zero_count:
            return 0.0
        return ordered[index - zero_count]

    rank = (total_count - 1) * quantile
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return value_at(lower)
    weight = rank - lower
    return value_at(lower) * (1.0 - weight) + value_at(upper) * weight


TIMESERIES_FIELDS = [
    'window_us', 'window_index', 'window_start_us', 'window_end_us',
    'src_switch', 'dst_switch', 'src_role', 'dst_role', 'parallel_links',
    'capacity_gbps', 'threshold_gbps', 'tx_bytes', 'throughput_gbps',
    'utilization', 'above_threshold', 'active_lanes', 'max_lane_gbps',
    'max_lane_utilization', 'mean_lane_gbps', 'lane_skew', 'jain_fairness',
]


PHYSICAL_FIELDS = [
    'src_switch', 'src_port', 'dst_switch', 'dst_port', 'src_role', 'dst_role',
    'capacity_gbps', 'trace_file_exists', 'tx_events', 'total_tx_bytes',
    'average_gbps', 'max_base_window_gbps', 'active_base_windows',
    'first_tx_us', 'last_tx_us', 'malformed_tx_lines', 'wrong_port_lines',
]


SUMMARY_FIELDS = [
    'window_us', 'src_switch', 'dst_switch', 'src_role', 'dst_role',
    'parallel_links', 'capacity_gbps', 'threshold_gbps', 'total_tx_bytes',
    'average_gbps', 'p50_gbps', 'p95_gbps', 'p99_gbps', 'max_gbps',
    'p99_utilization', 'max_utilization', 'hot_windows', 'hot_fraction',
    'longest_hot_duration_us', 'excess_bytes_above_threshold',
    'saturated_windows', 'saturated_fraction', 'p99_max_lane_utilization',
    'max_lane_utilization', 'mean_jain_fairness_active',
    'min_jain_fairness_active', 'observed_bins',
]


def write_endpoint_traffic_summary(traffic_path: Path, output_path: Path) -> Dict[str, int]:
    if not traffic_path.exists():
        return {'traffic_rows': 0, 'traffic_payload_bytes': 0}
    grouped: Dict[Tuple[int, int, str], List[int]] = defaultdict(lambda: [0, 0])
    with traffic_path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return {'traffic_rows': 0, 'traffic_payload_bytes': 0}
        src_field = 'sourceNode' if 'sourceNode' in reader.fieldnames else 'sourceNodeId'
        dst_field = 'destNode' if 'destNode' in reader.fieldnames else 'destNodeId'
        required = {src_field, dst_field, 'dataSize(Byte)', 'opType'}
        if not required.issubset(reader.fieldnames):
            raise ValueError(f'{traffic_path} must contain columns {sorted(required)}')
        total_rows = 0
        total_bytes = 0
        for row in reader:
            size = int(row['dataSize(Byte)'])
            key = (int(row[src_field]), int(row[dst_field]), row['opType'].strip())
            grouped[key][0] += 1
            grouped[key][1] += size
            total_rows += 1
            total_bytes += size
    with output_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'source_node', 'dest_node', 'op_type', 'task_count',
            'application_payload_bytes', 'payload_share',
        ])
        for (src, dst, op_type), (count, num_bytes) in sorted(
            grouped.items(), key=lambda item: (-item[1][1], item[0])):
            share = num_bytes / total_bytes if total_bytes else 0.0
            writer.writerow([src, dst, op_type, count, num_bytes, f'{share:.9f}'])
    return {'traffic_rows': total_rows, 'traffic_payload_bytes': total_bytes}


def observed_bin_range(
    window_us: int,
    first_event_us: float,
    last_event_us: float,
    requested_start_us: Optional[float],
    requested_end_us: Optional[float],
) -> Tuple[int, int]:
    start = requested_start_us if requested_start_us is not None else first_event_us
    end = requested_end_us if requested_end_us is not None else last_event_us
    first_bin = int(math.floor(start / window_us + 1e-12))
    if requested_end_us is not None:
        last_bin = int(math.ceil(end / window_us - 1e-12)) - 1
    else:
        last_bin = int(math.floor(end / window_us + 1e-12))
    return first_bin, max(first_bin, last_bin)


def iter_grouped_timeseries(path: Path) -> Iterator[Tuple[Tuple[int, int, int], List[dict]]]:
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        def key(row: dict) -> Tuple[int, int, int]:
            return int(row['src_switch']), int(row['dst_switch']), int(row['window_us'])

        for group_key, rows in itertools.groupby(reader, key=key):
            yield group_key, list(rows)


def summarize_timeseries(
    timeseries_path: Path,
    summary_path: Path,
    first_event_us: float,
    last_event_us: float,
    requested_start_us: Optional[float],
    requested_end_us: Optional[float],
    saturation_ratio: float,
) -> List[dict]:
    summaries: List[dict] = []
    for (_, _, window_us), rows in iter_grouped_timeseries(timeseries_path):
        first_bin, last_bin = observed_bin_range(
            window_us, first_event_us, last_event_us,
            requested_start_us, requested_end_us)
        total_bins = last_bin - first_bin + 1
        throughputs = [float(row['throughput_gbps']) for row in rows]
        lane_utils = [float(row['max_lane_utilization']) for row in rows]
        jains = [float(row['jain_fairness']) for row in rows]
        threshold = float(rows[0]['threshold_gbps'])
        capacity = float(rows[0]['capacity_gbps'])
        threshold_bytes = threshold * window_us * 1000.0 / 8.0

        hot_indices = []
        saturated = 0
        excess_bytes = 0.0
        total_bytes = 0
        for row in rows:
            bin_index = int(row['window_index'])
            rate = float(row['throughput_gbps'])
            num_bytes = int(row['tx_bytes'])
            total_bytes += num_bytes
            if rate > threshold:
                hot_indices.append(bin_index)
                excess_bytes += max(0.0, num_bytes - threshold_bytes)
            if capacity > 0 and rate >= saturation_ratio * capacity:
                saturated += 1

        longest_run = 0
        current_run = 0
        previous = None
        for bin_index in hot_indices:
            if previous is not None and bin_index == previous + 1:
                current_run += 1
            else:
                current_run = 1
            longest_run = max(longest_run, current_run)
            previous = bin_index

        duration_us = total_bins * window_us
        average = throughput_gbps(total_bytes, duration_us)
        p50 = sparse_percentile(throughputs, total_bins, 0.50)
        p95 = sparse_percentile(throughputs, total_bins, 0.95)
        p99 = sparse_percentile(throughputs, total_bins, 0.99)
        maximum = max(throughputs, default=0.0)
        p99_lane = sparse_percentile(lane_utils, total_bins, 0.99)
        max_lane = max(lane_utils, default=0.0)

        first = rows[0]
        summaries.append({
            'window_us': window_us,
            'src_switch': int(first['src_switch']),
            'dst_switch': int(first['dst_switch']),
            'src_role': first['src_role'],
            'dst_role': first['dst_role'],
            'parallel_links': int(first['parallel_links']),
            'capacity_gbps': capacity,
            'threshold_gbps': threshold,
            'total_tx_bytes': total_bytes,
            'average_gbps': average,
            'p50_gbps': p50,
            'p95_gbps': p95,
            'p99_gbps': p99,
            'max_gbps': maximum,
            'p99_utilization': p99 / capacity if capacity else 0.0,
            'max_utilization': maximum / capacity if capacity else 0.0,
            'hot_windows': len(hot_indices),
            'hot_fraction': len(hot_indices) / total_bins,
            'longest_hot_duration_us': longest_run * window_us,
            'excess_bytes_above_threshold': int(round(excess_bytes)),
            'saturated_windows': saturated,
            'saturated_fraction': saturated / total_bins,
            'p99_max_lane_utilization': p99_lane,
            'max_lane_utilization': max_lane,
            'mean_jain_fairness_active': sum(jains) / len(jains) if jains else 0.0,
            'min_jain_fairness_active': min(jains, default=0.0),
            'observed_bins': total_bins,
        })

    summaries.sort(key=lambda row: (
        row['window_us'], -row['p99_gbps'], -row['max_gbps'],
        row['src_switch'], row['dst_switch']))
    with summary_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for row in summaries:
            writer.writerow({
                key: f'{value:.9f}' if isinstance(value, float) else value
                for key, value in row.items()
            })
    return summaries


def downsample_max(points: Sequence[Tuple[float, float]], max_points: int = 5000) -> List[Tuple[float, float]]:
    if len(points) <= max_points:
        return list(points)
    bucket_size = int(math.ceil(len(points) / max_points))
    result = []
    for offset in range(0, len(points), bucket_size):
        bucket = points[offset:offset + bucket_size]
        result.append(max(bucket, key=lambda point: point[1]))
    return result


def load_top_timeseries(
    path: Path,
    window_us: int,
    keys: Sequence[Tuple[int, int]],
    first_event_us: float,
) -> Dict[Tuple[int, int], List[Tuple[float, float]]]:
    selected = set(keys)
    series: Dict[Tuple[int, int], List[Tuple[float, float]]] = defaultdict(list)
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if int(row['window_us']) != window_us:
                continue
            key = int(row['src_switch']), int(row['dst_switch'])
            if key not in selected:
                continue
            elapsed_ms = (float(row['window_start_us']) - first_event_us) / 1000.0
            series[key].append((elapsed_ms, float(row['throughput_gbps'])))
    return series


def make_plots(
    output_dir: Path,
    timeseries_path: Path,
    summaries: Sequence[dict],
    plot_window_us: int,
    top_k: int,
    first_event_us: float,
) -> List[str]:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print(
            '[WARN] matplotlib could not be imported ({}); CSV outputs are '
            'complete, plots skipped.'.format(exc), file=sys.stderr)
        return []

    selected = [row for row in summaries if row['window_us'] == plot_window_us]
    selected.sort(key=lambda row: (row['p99_gbps'], row['max_gbps']), reverse=True)
    top = selected[:top_k]
    if not top:
        return []
    files = []

    labels = [f"{row['src_switch']}→{row['dst_switch']}" for row in reversed(top)]
    p99 = [row['p99_gbps'] for row in reversed(top)]
    maximum = [row['max_gbps'] for row in reversed(top)]
    capacities = [row['capacity_gbps'] for row in reversed(top)]
    threshold = float(top[0]['threshold_gbps'])
    y = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(11, max(5, 0.45 * len(labels) + 2)))
    ax.barh([value - 0.18 for value in y], p99, height=0.34, label='P99 carried load')
    ax.barh([value + 0.18 for value in y], maximum, height=0.34, label='Maximum carried load', alpha=0.55)
    ax.scatter(capacities, y, marker='|', s=180, color='black', label='Actual bundle capacity')
    ax.axvline(threshold, color='red', linestyle='--', linewidth=1.3,
               label=f'Target threshold {threshold:g} Gbps')
    ax.set_yticks(y, labels)
    ax.set_xlabel('Gbps')
    ax.set_title(f'Top switch-pair hotspots ({plot_window_us}us windows)')
    ax.grid(axis='x', alpha=0.25)
    ax.legend(loc='best')
    fig.tight_layout()
    path = output_dir / f'hotspot_topk_{plot_window_us}us.png'
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)

    top_keys = [(row['src_switch'], row['dst_switch']) for row in top]
    series = load_top_timeseries(
        timeseries_path, plot_window_us, top_keys, first_event_us)
    fig, ax = plt.subplots(figsize=(12, 6))
    for key in top_keys:
        points = downsample_max(series.get(key, []))
        if points:
            ax.plot([point[0] for point in points], [point[1] for point in points],
                    linewidth=1.0, label=f'{key[0]}→{key[1]}')
    ax.axhline(threshold, color='red', linestyle='--', linewidth=1.2,
               label=f'{threshold:g} Gbps threshold')
    ax.set_xlabel('Elapsed time from first observed TX (ms)')
    ax.set_ylabel('Carried load (Gbps)')
    ax.set_title(f'Top switch-pair carried-load timeline ({plot_window_us}us windows)')
    ax.grid(alpha=0.25)
    ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    path = output_dir / f'hotspot_timeseries_{plot_window_us}us.png'
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)

    for src_role, dst_role, name in (
        ('LEAF', 'SPINE', 'leaf_to_spine'),
        ('SPINE', 'LEAF', 'spine_to_leaf'),
    ):
        rows = [row for row in selected
                if row['src_role'] == src_role and row['dst_role'] == dst_role]
        if not rows:
            continue
        src_ids = sorted({row['src_switch'] for row in rows})
        dst_ids = sorted({row['dst_switch'] for row in rows})
        values = {(row['src_switch'], row['dst_switch']): row['p99_gbps'] for row in rows}
        matrix = [[values.get((src, dst), 0.0) for dst in dst_ids] for src in src_ids]
        fig, ax = plt.subplots(figsize=(max(8, len(dst_ids) * 0.65), max(6, len(src_ids) * 0.35)))
        image = ax.imshow(matrix, aspect='auto', interpolation='nearest')
        ax.set_xticks(range(len(dst_ids)), dst_ids)
        ax.set_yticks(range(len(src_ids)), src_ids)
        ax.set_xlabel(f'{dst_role.title()} switch')
        ax.set_ylabel(f'{src_role.title()} switch')
        ax.set_title(f'P99 carried load: {src_role}→{dst_role} ({plot_window_us}us, Gbps)')
        fig.colorbar(image, ax=ax, label='P99 Gbps')
        fig.tight_layout()
        path = output_dir / f'{name}_p99_heatmap_{plot_window_us}us.png'
        fig.savefig(path, dpi=180)
        plt.close(fig)
        files.append(path.name)
    return files


def analyze_case(
    case_dir: Path,
    output_dir: Path,
    windows_us: Sequence[int],
    threshold_gbps: float = 1600.0,
    saturation_ratio: float = 0.95,
    start_us: Optional[float] = None,
    end_us: Optional[float] = None,
    top_k: int = 10,
    plot_window_us: Optional[int] = None,
    make_plot_files: bool = True,
    progress_every: int = 25,
) -> dict:
    case_dir = case_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if threshold_gbps <= 0:
        raise ValueError('threshold_gbps must be > 0')
    if not 0 < saturation_ratio <= 1:
        raise ValueError('saturation_ratio must be in (0, 1]')
    if start_us is not None and end_us is not None and end_us <= start_us:
        raise ValueError('end_us must be greater than start_us')
    windows = sorted(set(int(window) for window in windows_us))
    if not windows or windows[0] <= 0:
        raise ValueError('windows_us must contain positive integers')
    base_window_us = windows[0]
    if any(window % base_window_us for window in windows):
        raise ValueError('every window must be a multiple of the smallest window')

    node_path = case_dir / 'node.csv'
    topology_path = case_dir / 'topology.csv'
    runlog_dir = case_dir / 'runlog'
    for required in (node_path, topology_path, runlog_dir):
        if not required.exists():
            raise FileNotFoundError(required)
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes = read_nodes(node_path)
    links = read_topology(topology_path, nodes)
    roles = classify_switch_roles(nodes, links)
    bundles = build_directed_bundles(nodes, links)
    timeseries_path = output_dir / 'switch_bundle_timeseries.csv'
    physical_path = output_dir / 'physical_link_summary.csv'
    summary_path = output_dir / 'switch_hotspot_summary.csv'

    first_event_us: Optional[float] = None
    last_event_us: Optional[float] = None
    missing_trace_files = 0
    malformed_tx_lines = 0
    wrong_port_lines = 0
    total_tx_events = 0
    total_switch_tx_bytes = 0
    physical_records = []

    with timeseries_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TIMESERIES_FIELDS)
        writer.writeheader()
        for bundle_number, ((src_switch, dst_switch), lanes) in enumerate(
            sorted(bundles.items()), 1):
            member_base_bins: List[Dict[int, int]] = []
            for lane in lanes:
                path = trace_path(runlog_dir, lane.src_node, lane.src_port)
                trace = parse_port_tx_bins(
                    path, lane.src_port, base_window_us, start_us, end_us)
                member_base_bins.append(trace.bins)
                if not trace.file_exists:
                    missing_trace_files += 1
                malformed_tx_lines += trace.malformed_tx_lines
                wrong_port_lines += trace.wrong_port_lines
                total_tx_events += trace.tx_events
                total_switch_tx_bytes += trace.total_bytes
                if trace.first_time_us is not None:
                    first_event_us = trace.first_time_us if first_event_us is None else min(
                        first_event_us, trace.first_time_us)
                    last_event_us = trace.last_time_us if last_event_us is None else max(
                        last_event_us, trace.last_time_us)
                maximum = max(
                    (throughput_gbps(value, base_window_us)
                     for value in trace.bins.values()), default=0.0)
                physical_records.append({
                    'src_switch': lane.src_node,
                    'src_port': lane.src_port,
                    'dst_switch': lane.dst_node,
                    'dst_port': lane.dst_port,
                    'src_role': roles.get(lane.src_node, 'SWITCH'),
                    'dst_role': roles.get(lane.dst_node, 'SWITCH'),
                    'capacity_gbps': lane.rate_gbps,
                    'trace_file_exists': int(trace.file_exists),
                    'tx_events': trace.tx_events,
                    'total_tx_bytes': trace.total_bytes,
                    'average_gbps': 0.0,
                    'max_base_window_gbps': maximum,
                    'active_base_windows': len(trace.bins),
                    'first_tx_us': '' if trace.first_time_us is None else trace.first_time_us,
                    'last_tx_us': '' if trace.last_time_us is None else trace.last_time_us,
                    'malformed_tx_lines': trace.malformed_tx_lines,
                    'wrong_port_lines': trace.wrong_port_lines,
                })

            capacity = sum(lane.rate_gbps for lane in lanes)
            for window_us in windows:
                factor = window_us // base_window_us
                member_bins = [roll_bins(bins, factor) for bins in member_base_bins]
                active_indices = sorted(set().union(*(bins.keys() for bins in member_bins)))
                for bin_index in active_indices:
                    lane_bytes = [bins.get(bin_index, 0) for bins in member_bins]
                    total_bytes = sum(lane_bytes)
                    if total_bytes == 0:
                        continue
                    lane_rates = [throughput_gbps(value, window_us) for value in lane_bytes]
                    bundle_rate = sum(lane_rates)
                    max_lane_rate = max(lane_rates, default=0.0)
                    mean_lane_rate = bundle_rate / len(lanes)
                    max_lane_utilization = max(
                        (rate / lane.rate_gbps
                         for rate, lane in zip(lane_rates, lanes)),
                        default=0.0)
                    writer.writerow({
                        'window_us': window_us,
                        'window_index': bin_index,
                        'window_start_us': bin_index * window_us,
                        'window_end_us': (bin_index + 1) * window_us,
                        'src_switch': src_switch,
                        'dst_switch': dst_switch,
                        'src_role': roles.get(src_switch, 'SWITCH'),
                        'dst_role': roles.get(dst_switch, 'SWITCH'),
                        'parallel_links': len(lanes),
                        'capacity_gbps': f'{capacity:.9f}',
                        'threshold_gbps': f'{threshold_gbps:.9f}',
                        'tx_bytes': total_bytes,
                        'throughput_gbps': f'{bundle_rate:.9f}',
                        'utilization': f'{bundle_rate / capacity if capacity else 0.0:.9f}',
                        'above_threshold': int(bundle_rate > threshold_gbps),
                        'active_lanes': sum(1 for value in lane_bytes if value > 0),
                        'max_lane_gbps': f'{max_lane_rate:.9f}',
                        'max_lane_utilization': f'{max_lane_utilization:.9f}',
                        'mean_lane_gbps': f'{mean_lane_rate:.9f}',
                        'lane_skew': f'{max_lane_rate / mean_lane_rate if mean_lane_rate else 0.0:.9f}',
                        'jain_fairness': f'{jain_fairness(lane_rates):.9f}',
                    })
            if progress_every > 0 and bundle_number % progress_every == 0:
                print(f'[INFO] processed {bundle_number}/{len(bundles)} directed switch bundles')

    if first_event_us is None or last_event_us is None:
        raise ValueError(
            'no switch-to-switch Port Tx events found; verify UB_TRACE_ENABLE and runlog path')

    observed_duration_us = max(
        base_window_us,
        (math.floor(last_event_us / base_window_us)
         - math.floor(first_event_us / base_window_us) + 1) * base_window_us)
    for record in physical_records:
        record['average_gbps'] = throughput_gbps(
            int(record['total_tx_bytes']), int(observed_duration_us))
    with physical_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=PHYSICAL_FIELDS)
        writer.writeheader()
        for record in sorted(physical_records, key=lambda row: (
            -float(row['max_base_window_gbps']), row['src_switch'], row['src_port'])):
            writer.writerow({
                key: f'{value:.9f}' if isinstance(value, float) else value
                for key, value in record.items()
            })

    summaries = summarize_timeseries(
        timeseries_path, summary_path, first_event_us, last_event_us,
        start_us, end_us, saturation_ratio)
    traffic_stats = write_endpoint_traffic_summary(
        case_dir / 'traffic.csv', output_dir / 'traffic_endpoint_summary.csv')

    chosen_plot_window = plot_window_us
    if chosen_plot_window is None:
        chosen_plot_window = 1000 if 1000 in windows else windows[0]
    if chosen_plot_window not in windows:
        raise ValueError(f'plot_window_us {chosen_plot_window} is not in windows_us {windows}')
    plot_files = []
    if make_plot_files:
        plot_files = make_plots(
            output_dir, timeseries_path, summaries, chosen_plot_window,
            top_k, first_event_us)

    hottest_by_window = {}
    for window_us in windows:
        candidates = [row for row in summaries if row['window_us'] == window_us]
        if candidates:
            top = max(candidates, key=lambda row: (row['p99_gbps'], row['max_gbps']))
            hottest_by_window[str(window_us)] = {
                'src_switch': top['src_switch'],
                'dst_switch': top['dst_switch'],
                'p99_gbps': top['p99_gbps'],
                'max_gbps': top['max_gbps'],
                'capacity_gbps': top['capacity_gbps'],
                'hot_fraction': top['hot_fraction'],
            }

    report = {
        'case_dir': str(case_dir),
        'output_dir': str(output_dir),
        'windows_us': windows,
        'base_window_us': base_window_us,
        'threshold_gbps': threshold_gbps,
        'saturation_ratio': saturation_ratio,
        'requested_start_us': start_us,
        'requested_end_us': end_us,
        'first_switch_tx_us': first_event_us,
        'last_switch_tx_us': last_event_us,
        'observed_duration_us': observed_duration_us,
        'directed_switch_bundles': len(bundles),
        'directed_physical_lanes': len(physical_records),
        'missing_zero_traffic_trace_files': missing_trace_files,
        'malformed_tx_lines': malformed_tx_lines,
        'wrong_port_lines': wrong_port_lines,
        'switch_tx_events': total_tx_events,
        'switch_tx_bytes': total_switch_tx_bytes,
        'traffic_rows': traffic_stats['traffic_rows'],
        'traffic_payload_bytes': traffic_stats['traffic_payload_bytes'],
        'hottest_by_window': hottest_by_window,
        'plot_files': plot_files,
        'notes': [
            'Port Tx bytes are carried wire bytes, including protocol overhead and control traffic.',
            'Directions are analyzed separately; Port Rx is intentionally not counted.',
            'A missing PortTrace file is treated as a zero-traffic physical lane.',
            'Throughput above configured capacity in a short bin can include packet-start boundary effects.',
            'This analyzer measures carried load. Offered load and queue growth require an egress queue trace.',
        ],
    }
    with (output_dir / 'analysis_summary.json').open('w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write('\n')
    return report


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Analyze ns-3-UB switch-to-switch hotspots from PortTrace logs.')
    parser.add_argument('case_dir', type=Path,
                        help='ns-3-UB case directory containing node.csv, topology.csv, traffic.csv, and runlog/')
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='output directory (default: <case-dir>/output/switch_hotspots)')
    parser.add_argument('--windows-us', default='100,1000,10000,100000',
                        help='comma-separated windows in microseconds; each must be a multiple of the smallest')
    parser.add_argument('--threshold-gbps', type=float, default=1600.0,
                        help='comparison threshold, default 1600 Gbps = 4x400G')
    parser.add_argument('--saturation-ratio', type=float, default=0.95,
                        help='fraction of actual bundle capacity considered saturated (default: 0.95)')
    parser.add_argument('--start-us', type=float, default=None,
                        help='optional inclusive simulation-time filter in microseconds')
    parser.add_argument('--end-us', type=float, default=None,
                        help='optional exclusive simulation-time filter in microseconds')
    parser.add_argument('--top-k', type=int, default=10,
                        help='number of switch pairs shown in ranking/timeline plots')
    parser.add_argument('--plot-window-us', type=int, default=None,
                        help='window used in plots; default 1000us if requested, otherwise smallest window')
    parser.add_argument('--no-plots', action='store_true',
                        help='write CSV/JSON only; do not import matplotlib')
    parser.add_argument('--progress-every', type=int, default=25,
                        help='print progress after this many directed bundles; 0 disables progress')
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    try:
        windows = parse_windows(args.windows_us)
        output_dir = args.output_dir or args.case_dir / 'output' / 'switch_hotspots'
        report = analyze_case(
            case_dir=args.case_dir,
            output_dir=output_dir,
            windows_us=windows,
            threshold_gbps=args.threshold_gbps,
            saturation_ratio=args.saturation_ratio,
            start_us=args.start_us,
            end_us=args.end_us,
            top_k=args.top_k,
            plot_window_us=args.plot_window_us,
            make_plot_files=not args.no_plots,
            progress_every=args.progress_every,
        )
        print('=' * 88)
        print('ns-3-UB switch hotspot analysis complete')
        print('=' * 88)
        print(f"Case                    : {report['case_dir']}")
        print(f"Output                  : {report['output_dir']}")
        print(f"Observed TX interval     : {report['first_switch_tx_us']:.3f}us .. "
              f"{report['last_switch_tx_us']:.3f}us")
        print(f"Directed switch bundles : {report['directed_switch_bundles']}")
        print(f"Directed physical lanes : {report['directed_physical_lanes']}")
        print(f"Switch TX events         : {report['switch_tx_events']:,}")
        print(f"Switch TX bytes          : {report['switch_tx_bytes']:,}")
        print(f"Missing zero-TX logs     : {report['missing_zero_traffic_trace_files']}")
        for window, hottest in report['hottest_by_window'].items():
            print(f"Hottest P99 @{window}us     : {hottest['src_switch']}→{hottest['dst_switch']} "
                  f"p99={hottest['p99_gbps']:.3f}Gbps max={hottest['max_gbps']:.3f}Gbps")
        print('=' * 88)
        return 0
    except Exception as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
