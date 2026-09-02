#!/usr/bin/env python3
"""Analyze actual directed L1-to-L1 traffic from ns-3-UB L1PairTrace.

The trace is emitted once, when a packet leaves its actual egress L1 toward
the destination device.  A compact packet tag carries the actual ingress L1,
so multi-homed endpoints, ECMP and packet spray are measured without guessing
an endpoint-to-leaf mapping and without counting the L1->L2 and L2->L1 hops
twice.

``active_task_flow_count`` means distinct UbFlowTag/taskId values observed in
one time window.  It is intentionally not called a routing-hash flow count.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from analyze_switch_hotspots import (
    PhysicalLink,
    classify_switch_roles,
    parse_windows,
    read_nodes,
    read_topology,
    roll_bins,
    sparse_percentile,
    throughput_gbps,
)


L1_PAIR_RE = re.compile(
    rb'^\[\s*([0-9eE+\-.]+)us\]\s+L1 Pair Tx,\s*'
    rb'SrcL1:\s*(\d+)\s+DstL1:\s*(\d+)\s+'
    rb'PacketUid:\s*(\d+)\s+PacketSize:\s*(\d+)\s+'
    rb'TaskId:\s*([0-9]+|NA)'
)

TIMESERIES_FIELDS = [
    'window_us', 'window_index', 'window_start_us', 'window_end_us',
    'src_l1', 'dst_l1', 'src_l1_group', 'dst_l1_group',
    'tx_bytes', 'packet_count', 'throughput_gbps',
    'active_task_flow_count', 'task_tagged_packets',
    'unattributed_packets', 'unattributed_bytes',
    'src_l1_total_gbps', 'dst_l1_total_gbps', 'network_total_gbps',
    'share_of_src_l1_traffic', 'share_of_dst_l1_traffic',
    'share_of_network_traffic',
]

SUMMARY_FIELDS = [
    'window_us', 'src_l1', 'dst_l1', 'src_l1_group', 'dst_l1_group',
    'total_tx_bytes', 'total_packets', 'task_tagged_packets',
    'unattributed_packets', 'unattributed_bytes', 'task_trace_coverage',
    'distinct_task_flow_count', 'average_gbps', 'p50_gbps', 'p95_gbps',
    'p99_gbps', 'max_gbps', 'active_windows', 'active_fraction',
    'active_task_flow_count_p50', 'active_task_flow_count_p95',
    'active_task_flow_count_p99', 'active_task_flow_count_max',
    'mean_bytes_per_task_flow', 'p99_share_of_src_l1_traffic',
    'max_share_of_src_l1_traffic', 'p99_share_of_dst_l1_traffic',
    'max_share_of_dst_l1_traffic', 'total_network_traffic_share',
    'peak_window_index', 'peak_window_start_us',
    'active_task_flows_at_peak', 'observed_bins',
]


@dataclass
class PairBins:
    tx_bytes: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    packets: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    tagged_packets: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    unattributed_bytes: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    task_ids: Dict[int, Set[int]] = field(default_factory=lambda: defaultdict(set))
    all_task_ids: Set[int] = field(default_factory=set)


def roll_count_bins(bins: Mapping[int, int], factor: int) -> Dict[int, int]:
    return roll_bins(bins, factor)


def roll_task_bins(
    bins: Mapping[int, Set[int]], factor: int,
) -> Dict[int, Set[int]]:
    if factor == 1:
        return {index: set(values) for index, values in bins.items()}
    result: Dict[int, Set[int]] = defaultdict(set)
    for bin_index, values in bins.items():
        result[bin_index // factor].update(values)
    return dict(result)


def build_l1_groups(
    links: Sequence[PhysicalLink], roles: Mapping[int, str],
) -> Dict[int, int]:
    """Group L1 switches that share at least one directly attached device."""
    leaves = sorted(node for node, role in roles.items() if role == 'LEAF')
    device_to_leaves: Dict[int, Set[int]] = defaultdict(set)
    for link in links:
        if link.node_a in leaves and link.node_b not in roles:
            device_to_leaves[link.node_b].add(link.node_a)
        if link.node_b in leaves and link.node_a not in roles:
            device_to_leaves[link.node_a].add(link.node_b)

    adjacency: Dict[int, Set[int]] = {leaf: set() for leaf in leaves}
    for members in device_to_leaves.values():
        for leaf in members:
            adjacency[leaf].update(members - {leaf})

    components: List[List[int]] = []
    unseen = set(leaves)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: Set[int] = set()
        while stack:
            leaf = stack.pop()
            if leaf in component:
                continue
            component.add(leaf)
            stack.extend(sorted(adjacency[leaf] - component, reverse=True))
        unseen -= component
        components.append(sorted(component))
    components.sort(key=lambda values: values[0])
    return {
        leaf: group_number
        for group_number, component in enumerate(components, 1)
        for leaf in component
    }


def observed_bin_range(
    window_us: int, first_event_us: float, last_event_us: float,
    requested_start_us: Optional[float], requested_end_us: Optional[float],
) -> Tuple[int, int]:
    start = requested_start_us if requested_start_us is not None else first_event_us
    end = requested_end_us if requested_end_us is not None else last_event_us
    first_bin = int(math.floor(start / window_us + 1e-12))
    if requested_end_us is not None:
        last_bin = int(math.ceil(end / window_us - 1e-12)) - 1
    else:
        last_bin = int(math.floor(end / window_us + 1e-12))
    return first_bin, max(first_bin, last_bin)


def float_value(value: object) -> object:
    return '{:.9f}'.format(value) if isinstance(value, float) else value


def write_rows(path: Path, fields: Sequence[str], rows: Sequence[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: float_value(row.get(key, '')) for key in fields})


def parse_l1_pair_trace(
    path: Path,
    base_window_us: int,
    valid_l1: Set[int],
    include_local: bool,
    start_us: Optional[float],
    end_us: Optional[float],
) -> Tuple[Dict[Tuple[int, int], PairBins], dict]:
    pairs: Dict[Tuple[int, int], PairBins] = {}
    stats = {
        'trace_lines': 0,
        'matched_events': 0,
        'malformed_lines': 0,
        'invalid_l1_events': 0,
        'local_l1_events_excluded': 0,
        'local_l1_bytes_excluded': 0,
        'tagged_events': 0,
        'unattributed_events': 0,
        'total_bytes': 0,
        'unattributed_bytes': 0,
        'first_event_us': None,
        'last_event_us': None,
    }
    with path.open('rb', buffering=1024 * 1024) as stream:
        for line in stream:
            stats['trace_lines'] += 1
            if b'L1 Pair Tx' not in line:
                continue
            match = L1_PAIR_RE.match(line)
            if not match:
                stats['malformed_lines'] += 1
                continue
            time_us = float(match.group(1))
            src_l1 = int(match.group(2))
            dst_l1 = int(match.group(3))
            packet_size = int(match.group(5))
            task_value = match.group(6)
            if start_us is not None and time_us < start_us:
                continue
            if end_us is not None and time_us >= end_us:
                continue
            if src_l1 not in valid_l1 or dst_l1 not in valid_l1:
                stats['invalid_l1_events'] += 1
                continue
            if src_l1 == dst_l1 and not include_local:
                stats['local_l1_events_excluded'] += 1
                stats['local_l1_bytes_excluded'] += packet_size
                continue

            pair = src_l1, dst_l1
            aggregate = pairs.setdefault(pair, PairBins())
            bin_index = int(math.floor(time_us / base_window_us + 1e-12))
            aggregate.tx_bytes[bin_index] += packet_size
            aggregate.packets[bin_index] += 1
            if task_value == b'NA':
                aggregate.unattributed_bytes[bin_index] += packet_size
                stats['unattributed_events'] += 1
                stats['unattributed_bytes'] += packet_size
            else:
                task_id = int(task_value)
                aggregate.tagged_packets[bin_index] += 1
                aggregate.task_ids[bin_index].add(task_id)
                aggregate.all_task_ids.add(task_id)
                stats['tagged_events'] += 1

            stats['matched_events'] += 1
            stats['total_bytes'] += packet_size
            first = stats['first_event_us']
            last = stats['last_event_us']
            stats['first_event_us'] = time_us if first is None else min(first, time_us)
            stats['last_event_us'] = time_us if last is None else max(last, time_us)
    return pairs, stats


def summarize_pair(
    pair: Tuple[int, int],
    aggregate: PairBins,
    byte_bins: Mapping[int, int],
    packet_bins: Mapping[int, int],
    tagged_bins: Mapping[int, int],
    unattributed_bins: Mapping[int, int],
    task_bins: Mapping[int, Set[int]],
    src_totals: Mapping[Tuple[int, int], int],
    dst_totals: Mapping[Tuple[int, int], int],
    network_totals: Mapping[int, int],
    window_us: int,
    first_bin: int,
    last_bin: int,
    l1_groups: Mapping[int, int],
) -> dict:
    src_l1, dst_l1 = pair
    total_bins = last_bin - first_bin + 1
    active_indices = sorted(byte_bins)
    rates = [throughput_gbps(byte_bins[index], window_us) for index in active_indices]
    task_counts = [float(len(task_bins.get(index, set()))) for index in active_indices]
    src_shares = [
        byte_bins[index] / src_totals[(src_l1, index)]
        for index in active_indices if src_totals.get((src_l1, index), 0)
    ]
    dst_shares = [
        byte_bins[index] / dst_totals[(dst_l1, index)]
        for index in active_indices if dst_totals.get((dst_l1, index), 0)
    ]
    total_bytes = sum(byte_bins.values())
    total_packets = sum(packet_bins.values())
    total_tagged = sum(tagged_bins.values())
    total_unattributed_bytes = sum(unattributed_bins.values())
    total_unattributed_packets = total_packets - total_tagged
    duration_us = total_bins * window_us
    peak_index = max(
        active_indices,
        key=lambda index: (byte_bins[index], -index),
        default=first_bin,
    )
    network_bytes = sum(network_totals.values())
    distinct_tasks = len(aggregate.all_task_ids)
    return {
        'window_us': window_us,
        'src_l1': src_l1,
        'dst_l1': dst_l1,
        'src_l1_group': l1_groups.get(src_l1, ''),
        'dst_l1_group': l1_groups.get(dst_l1, ''),
        'total_tx_bytes': total_bytes,
        'total_packets': total_packets,
        'task_tagged_packets': total_tagged,
        'unattributed_packets': total_unattributed_packets,
        'unattributed_bytes': total_unattributed_bytes,
        'task_trace_coverage': total_tagged / total_packets if total_packets else 0.0,
        'distinct_task_flow_count': distinct_tasks,
        'average_gbps': throughput_gbps(total_bytes, duration_us),
        'p50_gbps': sparse_percentile(rates, total_bins, 0.50),
        'p95_gbps': sparse_percentile(rates, total_bins, 0.95),
        'p99_gbps': sparse_percentile(rates, total_bins, 0.99),
        'max_gbps': max(rates, default=0.0),
        'active_windows': len(active_indices),
        'active_fraction': len(active_indices) / total_bins,
        'active_task_flow_count_p50': sparse_percentile(task_counts, total_bins, 0.50),
        'active_task_flow_count_p95': sparse_percentile(task_counts, total_bins, 0.95),
        'active_task_flow_count_p99': sparse_percentile(task_counts, total_bins, 0.99),
        'active_task_flow_count_max': max(task_counts, default=0.0),
        'mean_bytes_per_task_flow': total_bytes / distinct_tasks if distinct_tasks else 0.0,
        'p99_share_of_src_l1_traffic': sparse_percentile(src_shares, total_bins, 0.99),
        'max_share_of_src_l1_traffic': max(src_shares, default=0.0),
        'p99_share_of_dst_l1_traffic': sparse_percentile(dst_shares, total_bins, 0.99),
        'max_share_of_dst_l1_traffic': max(dst_shares, default=0.0),
        'total_network_traffic_share': total_bytes / network_bytes if network_bytes else 0.0,
        'peak_window_index': peak_index,
        'peak_window_start_us': peak_index * window_us,
        'active_task_flows_at_peak': len(task_bins.get(peak_index, set())),
        'observed_bins': total_bins,
    }


def analyze_l1_pairs(
    case_dir: Path,
    output_dir: Path,
    windows_us: Sequence[int],
    timeseries_window_us: Optional[int] = 1000,
    timeseries_top_k: int = 20,
    include_local: bool = False,
    start_us: Optional[float] = None,
    end_us: Optional[float] = None,
) -> dict:
    case_dir = case_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    windows = sorted(set(int(value) for value in windows_us))
    if not windows or windows[0] <= 0:
        raise ValueError('windows_us must contain positive integers')
    if any(window % windows[0] for window in windows):
        raise ValueError('every window must be a multiple of the smallest window')
    if timeseries_window_us is not None and timeseries_window_us not in windows:
        raise ValueError('timeseries_window_us must be one of {}'.format(windows))
    if timeseries_top_k <= 0:
        raise ValueError('timeseries_top_k must be positive')
    if start_us is not None and end_us is not None and end_us <= start_us:
        raise ValueError('end_us must be greater than start_us')

    node_path = case_dir / 'node.csv'
    topology_path = case_dir / 'topology.csv'
    trace_path = case_dir / 'runlog' / 'L1PairTrace.tr'
    for required in (node_path, topology_path, trace_path):
        if not required.is_file():
            if required == trace_path:
                raise FileNotFoundError(
                    '{}; rebuild ns-3-UB with the L1-pair trace patch and rerun the case'.format(
                        required))
            raise FileNotFoundError(required)

    nodes = read_nodes(node_path)
    links = read_topology(topology_path, nodes)
    roles = classify_switch_roles(nodes, links)
    l1_nodes = {node for node, role in roles.items() if role == 'LEAF'}
    l1_groups = build_l1_groups(links, roles)
    if not l1_nodes:
        raise ValueError('topology contains no endpoint-facing L1 switches')

    pairs, trace_stats = parse_l1_pair_trace(
        trace_path, windows[0], l1_nodes, include_local, start_us, end_us)
    if not pairs or trace_stats['first_event_us'] is None:
        raise ValueError('selected interval contains no cross-L1 trace events')

    first_event_us = float(trace_stats['first_event_us'])
    last_event_us = float(trace_stats['last_event_us'])
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: List[dict] = []
    selected_roll = None

    for window_us in windows:
        factor = window_us // windows[0]
        first_bin, last_bin = observed_bin_range(
            window_us, first_event_us, last_event_us, start_us, end_us)
        rolled = {}
        src_totals: Dict[Tuple[int, int], int] = defaultdict(int)
        dst_totals: Dict[Tuple[int, int], int] = defaultdict(int)
        network_totals: Dict[int, int] = defaultdict(int)

        for pair, aggregate in pairs.items():
            byte_bins = roll_count_bins(aggregate.tx_bytes, factor)
            packet_bins = roll_count_bins(aggregate.packets, factor)
            tagged_bins = roll_count_bins(aggregate.tagged_packets, factor)
            unattributed_bins = roll_count_bins(aggregate.unattributed_bytes, factor)
            task_bins = roll_task_bins(aggregate.task_ids, factor)
            rolled[pair] = (
                byte_bins, packet_bins, tagged_bins, unattributed_bins, task_bins)
            src_l1, dst_l1 = pair
            for bin_index, num_bytes in byte_bins.items():
                src_totals[(src_l1, bin_index)] += num_bytes
                dst_totals[(dst_l1, bin_index)] += num_bytes
                network_totals[bin_index] += num_bytes

        for pair, aggregate in sorted(pairs.items()):
            byte_bins, packet_bins, tagged_bins, unattributed_bins, task_bins = rolled[pair]
            summaries.append(summarize_pair(
                pair, aggregate, byte_bins, packet_bins, tagged_bins,
                unattributed_bins, task_bins, src_totals, dst_totals,
                network_totals, window_us, first_bin, last_bin, l1_groups))
        if timeseries_window_us == window_us:
            selected_roll = rolled, src_totals, dst_totals, network_totals

    summaries.sort(key=lambda row: (
        row['window_us'], -row['p99_gbps'], -row['max_gbps'],
        row['src_l1'], row['dst_l1']))
    write_rows(output_dir / 'l1_pair_summary.csv', SUMMARY_FIELDS, summaries)

    timeseries_pairs: List[Tuple[int, int]] = []
    timeseries_path = output_dir / 'l1_pair_timeseries.csv'
    with timeseries_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=TIMESERIES_FIELDS)
        writer.writeheader()
        if timeseries_window_us is not None and selected_roll is not None:
            candidates = [
                row for row in summaries if row['window_us'] == timeseries_window_us]
            candidates.sort(key=lambda row: (
                -row['p99_gbps'], -row['max_gbps'],
                row['src_l1'], row['dst_l1']))
            timeseries_pairs = [
                (row['src_l1'], row['dst_l1'])
                for row in candidates[:timeseries_top_k]
            ]
            rolled, src_totals, dst_totals, network_totals = selected_roll
            for pair in timeseries_pairs:
                src_l1, dst_l1 = pair
                byte_bins, packet_bins, tagged_bins, unattributed_bins, task_bins = rolled[pair]
                for bin_index in sorted(byte_bins):
                    num_bytes = byte_bins[bin_index]
                    src_bytes = src_totals[(src_l1, bin_index)]
                    dst_bytes = dst_totals[(dst_l1, bin_index)]
                    network_bytes = network_totals[bin_index]
                    tagged_packets = tagged_bins.get(bin_index, 0)
                    packet_count = packet_bins.get(bin_index, 0)
                    row = {
                        'window_us': timeseries_window_us,
                        'window_index': bin_index,
                        'window_start_us': bin_index * timeseries_window_us,
                        'window_end_us': (bin_index + 1) * timeseries_window_us,
                        'src_l1': src_l1,
                        'dst_l1': dst_l1,
                        'src_l1_group': l1_groups.get(src_l1, ''),
                        'dst_l1_group': l1_groups.get(dst_l1, ''),
                        'tx_bytes': num_bytes,
                        'packet_count': packet_count,
                        'throughput_gbps': throughput_gbps(
                            num_bytes, timeseries_window_us),
                        'active_task_flow_count': len(
                            task_bins.get(bin_index, set())),
                        'task_tagged_packets': tagged_packets,
                        'unattributed_packets': packet_count - tagged_packets,
                        'unattributed_bytes': unattributed_bins.get(bin_index, 0),
                        'src_l1_total_gbps': throughput_gbps(
                            src_bytes, timeseries_window_us),
                        'dst_l1_total_gbps': throughput_gbps(
                            dst_bytes, timeseries_window_us),
                        'network_total_gbps': throughput_gbps(
                            network_bytes, timeseries_window_us),
                        'share_of_src_l1_traffic': (
                            num_bytes / src_bytes if src_bytes else 0.0),
                        'share_of_dst_l1_traffic': (
                            num_bytes / dst_bytes if dst_bytes else 0.0),
                        'share_of_network_traffic': (
                            num_bytes / network_bytes if network_bytes else 0.0),
                    }
                    writer.writerow({
                        key: float_value(row.get(key, '')) for key in TIMESERIES_FIELDS})

    hottest_by_window = {}
    for window_us in windows:
        candidates = [row for row in summaries if row['window_us'] == window_us]
        if not candidates:
            continue
        hottest = max(candidates, key=lambda row: (row['p99_gbps'], row['max_gbps']))
        most_flows = max(candidates, key=lambda row: (
            row['active_task_flow_count_p99'], row['active_task_flow_count_max']))
        hottest_by_window[str(window_us)] = {
            'hottest_traffic_pair': {
                'src_l1': hottest['src_l1'],
                'dst_l1': hottest['dst_l1'],
                'p99_gbps': hottest['p99_gbps'],
                'max_gbps': hottest['max_gbps'],
                'p99_active_task_flows': hottest['active_task_flow_count_p99'],
            },
            'most_concurrent_flows_pair': {
                'src_l1': most_flows['src_l1'],
                'dst_l1': most_flows['dst_l1'],
                'p99_active_task_flows': most_flows['active_task_flow_count_p99'],
                'max_active_task_flows': most_flows['active_task_flow_count_max'],
                'p99_gbps': most_flows['p99_gbps'],
            },
        }

    report = {
        'case_dir': str(case_dir),
        'output_dir': str(output_dir),
        'trace_file': str(trace_path),
        'windows_us': windows,
        'timeseries_window_us': timeseries_window_us,
        'timeseries_top_k': timeseries_top_k,
        'timeseries_pairs': [list(pair) for pair in timeseries_pairs],
        'include_local_l1_traffic': include_local,
        'first_l1_pair_tx_us': first_event_us,
        'last_l1_pair_tx_us': last_event_us,
        'l1_nodes': sorted(l1_nodes),
        'l1_groups': {
            str(group): sorted(node for node, value in l1_groups.items() if value == group)
            for group in sorted(set(l1_groups.values()))
        },
        'observed_directed_l1_pairs': len(pairs),
        'matched_packet_events': trace_stats['matched_events'],
        'total_l1_pair_tx_bytes': trace_stats['total_bytes'],
        'task_tagged_packet_events': trace_stats['tagged_events'],
        'unattributed_packet_events': trace_stats['unattributed_events'],
        'unattributed_tx_bytes': trace_stats['unattributed_bytes'],
        'task_trace_coverage': (
            trace_stats['tagged_events'] / trace_stats['matched_events']
            if trace_stats['matched_events'] else 0.0),
        'malformed_trace_lines': trace_stats['malformed_lines'],
        'invalid_l1_events': trace_stats['invalid_l1_events'],
        'local_l1_events_excluded': trace_stats['local_l1_events_excluded'],
        'local_l1_bytes_excluded': trace_stats['local_l1_bytes_excluded'],
        'flow_definition': (
            'A task flow is one distinct UbFlowTag/taskId with at least one packet '
            'observed for a directed L1 pair in the time window.'),
        'hash_flow_metrics_available': False,
        'hottest_by_window': hottest_by_window,
        'counting_method': (
            'One trace event is emitted at the actual egress L1 downlink. The packet '
            'carries its actual ingress L1 in UbL1TraceTag, so each end-to-end packet '
            'is counted once and multi-homed endpoints require no static mapping.'),
    }
    (output_dir / 'l1_pair_analysis.json').write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Aggregate exact directed L1-to-L1 carried traffic and concurrent '
            'task-flow counts from runlog/L1PairTrace.tr.'))
    parser.add_argument('case_dir', type=Path)
    parser.add_argument(
        '--output-dir', type=Path,
        help='default: CASE/output/l1_pair_hotspots')
    parser.add_argument(
        '--windows-us', default='100,1000,10000,100000',
        help='comma-separated windows; every value must be a multiple of the smallest')
    parser.add_argument(
        '--timeseries-window-us', type=int, default=1000,
        help='one selected window written to l1_pair_timeseries.csv; use 0 to disable')
    parser.add_argument(
        '--timeseries-top-k', type=int, default=20,
        help='write time series only for the top K pairs by P99 traffic')
    parser.add_argument('--include-local', action='store_true')
    parser.add_argument('--start-us', type=float)
    parser.add_argument('--end-us', type=float)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        windows = parse_windows(args.windows_us)
        timeseries_window = args.timeseries_window_us or None
        output_dir = args.output_dir or args.case_dir / 'output' / 'l1_pair_hotspots'
        report = analyze_l1_pairs(
            args.case_dir, output_dir, windows, timeseries_window,
            args.timeseries_top_k, args.include_local, args.start_us, args.end_us)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print('[ERROR] {}'.format(exc), file=sys.stderr)
        return 2

    print('[OK] L1 pair analysis written to {}'.format(report['output_dir']))
    print('[OK] L1 nodes: {}'.format(report['l1_nodes']))
    print('[OK] observed directed pairs: {}'.format(
        report['observed_directed_l1_pairs']))
    print('[OK] packet events: {} (task-tag coverage {:.2%})'.format(
        report['matched_packet_events'], report['task_trace_coverage']))
    for window, values in sorted(
        report['hottest_by_window'].items(), key=lambda item: int(item[0])
    ):
        hot = values['hottest_traffic_pair']
        print('[HOT {}us] L1 {} -> {}: p99={:.3f} Gbps, max={:.3f} Gbps, '
              'p99 task flows={:.1f}'.format(
                  window, hot['src_l1'], hot['dst_l1'], hot['p99_gbps'],
                  hot['max_gbps'], hot['p99_active_task_flows']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
