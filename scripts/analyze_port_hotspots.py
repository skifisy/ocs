#!/usr/bin/env python3
"""Analyze individual switch-to-switch output ports from ns-3-UB PortTrace.

The companion switch-pair analyzer aggregates parallel lanes.  This program
keeps each physical output port separate so a 400 Gbps lane can be tested for
sustained saturation and imbalance against the other lanes in its bundle.

Legacy PortTrace records do not contain task IDs or routing hash keys.  Task-flow
metrics are explicitly marked unavailable unless every selected Port Tx record
has the optional compact ``TaskId`` suffix; the program never infers a flow
count from packet count.  Routing hash-key counts remain a separate unavailable
metric even when TaskId instrumentation is present.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from analyze_switch_hotspots import (
    DirectedLane,
    TraceBins,
    build_directed_bundles,
    classify_switch_roles,
    jain_fairness,
    parse_port_tx_bins,
    parse_windows,
    read_nodes,
    read_topology,
    roll_bins,
    sparse_percentile,
    throughput_gbps,
    trace_path,
)


PORT_TIMESERIES_FIELDS = [
    'window_us', 'window_index', 'window_start_us', 'window_end_us',
    'src_switch', 'src_port', 'dst_switch', 'dst_port',
    'src_role', 'dst_role', 'capacity_gbps', 'saturation_threshold_gbps',
    'tx_bytes', 'throughput_gbps', 'utilization', 'saturated',
    'active_task_flow_count', 'active_hash_key_count', 'flow_metrics_available',
]

PORT_SUMMARY_FIELDS = [
    'window_us', 'src_switch', 'src_port', 'dst_switch', 'dst_port',
    'src_role', 'dst_role', 'capacity_gbps', 'saturation_threshold_gbps',
    'trace_file_exists', 'tx_events', 'total_tx_bytes', 'average_gbps',
    'p50_gbps', 'p95_gbps', 'p99_gbps', 'max_gbps',
    'p99_utilization', 'max_utilization', 'active_windows',
    'saturated_windows', 'saturated_fraction',
    'longest_saturated_duration_us', 'observed_bins',
    'active_task_flow_count_p50', 'active_task_flow_count_p95',
    'active_task_flow_count_p99', 'active_task_flow_count_max',
    'active_hash_key_count_p99',
    'flow_metrics_available',
]

BUNDLE_TIMESERIES_FIELDS = [
    'window_us', 'window_index', 'window_start_us', 'window_end_us',
    'src_switch', 'dst_switch', 'src_role', 'dst_role', 'parallel_links',
    'bundle_capacity_gbps', 'bundle_throughput_gbps', 'active_lanes',
    'saturated_lanes', 'spare_lanes', 'max_lane_gbps', 'max_lane_port',
    'min_lane_gbps', 'mean_lane_gbps', 'max_lane_utilization',
    'min_lane_utilization', 'lane_skew', 'jain_fairness',
    'potential_lane_imbalance', 'active_task_flow_count',
    'flow_metrics_available',
]

BUNDLE_SUMMARY_FIELDS = [
    'window_us', 'src_switch', 'dst_switch', 'src_role', 'dst_role',
    'parallel_links', 'bundle_capacity_gbps', 'average_bundle_gbps',
    'p99_bundle_gbps', 'max_bundle_gbps', 'hottest_p99_port',
    'hottest_port_p99_gbps', 'p99_max_lane_gbps', 'max_lane_gbps',
    'mean_jain_fairness_active', 'min_jain_fairness_active',
    'potential_imbalance_windows', 'potential_imbalance_fraction',
    'longest_potential_imbalance_us', 'max_saturated_lanes',
    'max_spare_lanes', 'observed_bins', 'flow_metrics_available',
    'active_task_flow_count_p99', 'active_task_flow_count_max',
]


CONFIG_RE = re.compile(
    r'^\s*(?:default|global)\s+([^\s]+)\s+"?([^"\s]+)"?', re.IGNORECASE)
PORT_FLOW_TX_RE = re.compile(
    rb'^\[\s*([0-9eE+\-.]+)us\]\s+Port Tx,\s*port ID:\s*(\d+)\s+'
    rb'PacketSize:\s*(\d+)(?:\s+TaskId:\s*([0-9]+|NA))?'
)


@dataclass
class FlowAwareTrace:
    trace: TraceBins
    task_ids_by_bin: Dict[int, Set[int]]
    instrumented_tx_events: int = 0
    task_tagged_tx_events: int = 0


def parse_port_tx_bins_with_tasks(
    path: Path,
    expected_port: int,
    base_window_us: int,
    start_us: Optional[float] = None,
    end_us: Optional[float] = None,
) -> FlowAwareTrace:
    """Parse bytes plus the optional compact ``TaskId`` PortTrace suffix."""
    trace = TraceBins(bins={})
    result = FlowAwareTrace(trace=trace, task_ids_by_bin={})
    if not path.exists():
        return result
    trace.file_exists = True
    bins: Dict[int, int] = defaultdict(int)
    task_ids: Dict[int, Set[int]] = defaultdict(set)
    with path.open('rb', buffering=1024 * 1024) as stream:
        for line in stream:
            if b'Port Tx' not in line:
                continue
            match = PORT_FLOW_TX_RE.match(line)
            if not match:
                trace.malformed_tx_lines += 1
                continue
            time_us = float(match.group(1))
            port_id = int(match.group(2))
            packet_size = int(match.group(3))
            task_value = match.group(4)
            if port_id != expected_port:
                trace.wrong_port_lines += 1
                continue
            if start_us is not None and time_us < start_us:
                continue
            if end_us is not None and time_us >= end_us:
                continue
            bin_index = int(math.floor(time_us / base_window_us + 1e-12))
            bins[bin_index] += packet_size
            trace.total_bytes += packet_size
            trace.tx_events += 1
            if task_value is not None:
                result.instrumented_tx_events += 1
                if task_value != b'NA':
                    task_ids[bin_index].add(int(task_value))
                    result.task_tagged_tx_events += 1
            if trace.first_time_us is None or time_us < trace.first_time_us:
                trace.first_time_us = time_us
            if trace.last_time_us is None or time_us > trace.last_time_us:
                trace.last_time_us = time_us
    trace.bins = dict(bins)
    result.task_ids_by_bin = dict(task_ids)
    return result


def roll_task_bins(
    bins: Mapping[int, Set[int]], factor: int,
) -> Dict[int, Set[int]]:
    if factor == 1:
        return {index: set(values) for index, values in bins.items()}
    rolled: Dict[int, Set[int]] = defaultdict(set)
    for bin_index, values in bins.items():
        rolled[bin_index // factor].update(values)
    return dict(rolled)


def read_relevant_config(path: Path) -> dict:
    result = {
        'ub_record_pkt_trace': None,
        'urma_use_packet_spray': None,
        'ldst_use_packet_spray': None,
        'routing_algorithm': None,
    }
    if not path.is_file():
        return result
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            match = CONFIG_RE.match(line)
            if not match:
                continue
            name = match.group(1).lower()
            value = match.group(2)
            if name == 'ub_record_pkt_trace':
                result['ub_record_pkt_trace'] = value.lower() == 'true'
            elif name == 'ns3::ubtransportchannel::usepacketspray':
                result['urma_use_packet_spray'] = value.lower() == 'true'
            elif name == 'ns3::ubldstapi::usepacketspray':
                result['ldst_use_packet_spray'] = value.lower() == 'true'
            elif name == 'ns3::ubroutingprocess::routingalgorithm':
                result['routing_algorithm'] = value
    return result


def existing_observed_bounds(case_dir: Path) -> Optional[Tuple[float, float]]:
    path = case_dir / 'output' / 'switch_hotspots' / 'analysis_summary.json'
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        first = float(report['first_switch_tx_us'])
        last = float(report['last_switch_tx_us'])
        if last >= first:
            return first, last
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


def scan_trace_bounds(
    runlog_dir: Path,
    lanes: Sequence[DirectedLane],
    base_window_us: int,
    start_us: Optional[float],
    end_us: Optional[float],
) -> Tuple[float, float]:
    """Fallback bound discovery when a switch-hotspot report is unavailable."""
    first: Optional[float] = None
    last: Optional[float] = None
    for number, lane in enumerate(lanes, 1):
        trace = parse_port_tx_bins(
            trace_path(runlog_dir, lane.src_node, lane.src_port),
            lane.src_port, base_window_us, start_us, end_us)
        if trace.first_time_us is not None:
            first = trace.first_time_us if first is None else min(first, trace.first_time_us)
            last = trace.last_time_us if last is None else max(last, trace.last_time_us)
        if number % 100 == 0:
            print('[INFO] bound scan: {}/{} physical lanes'.format(number, len(lanes)))
    if first is None or last is None:
        raise ValueError('no switch-to-switch Port Tx events found')
    return first, last


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


def longest_consecutive(indices: Sequence[int]) -> int:
    longest = 0
    current = 0
    previous: Optional[int] = None
    for index in sorted(indices):
        if previous is not None and index == previous + 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = index
    return longest


def float_or_value(value: object) -> object:
    return '{:.9f}'.format(value) if isinstance(value, float) else value


def write_dict_rows(path: Path, fields: Sequence[str], rows: Sequence[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: float_or_value(row.get(key, '')) for key in fields})


def summarize_port(
    lane: DirectedLane,
    flow_trace: FlowAwareTrace,
    bins: Mapping[int, int],
    task_bins: Mapping[int, Set[int]],
    window_us: int,
    first_bin: int,
    last_bin: int,
    saturation_ratio: float,
    roles: Mapping[int, str],
) -> dict:
    trace = flow_trace.trace
    total_bins = last_bin - first_bin + 1
    rates = [throughput_gbps(value, window_us) for value in bins.values()]
    capacity = lane.rate_gbps
    saturation_rate = capacity * saturation_ratio
    saturated_indices = [
        index for index, value in bins.items()
        if throughput_gbps(value, window_us) >= saturation_rate
    ]
    total_bytes = sum(bins.values())
    duration_us = total_bins * window_us
    p99 = sparse_percentile(rates, total_bins, 0.99)
    maximum = max(rates, default=0.0)
    task_counts = [float(len(values)) for values in task_bins.values()]
    flow_available = (
        trace.tx_events > 0
        and flow_trace.instrumented_tx_events == trace.tx_events)
    return {
        'window_us': window_us,
        'src_switch': lane.src_node,
        'src_port': lane.src_port,
        'dst_switch': lane.dst_node,
        'dst_port': lane.dst_port,
        'src_role': roles.get(lane.src_node, 'SWITCH'),
        'dst_role': roles.get(lane.dst_node, 'SWITCH'),
        'capacity_gbps': capacity,
        'saturation_threshold_gbps': saturation_rate,
        'trace_file_exists': int(trace.file_exists),
        'tx_events': trace.tx_events,
        'total_tx_bytes': total_bytes,
        'average_gbps': throughput_gbps(total_bytes, duration_us),
        'p50_gbps': sparse_percentile(rates, total_bins, 0.50),
        'p95_gbps': sparse_percentile(rates, total_bins, 0.95),
        'p99_gbps': p99,
        'max_gbps': maximum,
        'p99_utilization': p99 / capacity if capacity else 0.0,
        'max_utilization': maximum / capacity if capacity else 0.0,
        'active_windows': len(bins),
        'saturated_windows': len(saturated_indices),
        'saturated_fraction': len(saturated_indices) / total_bins,
        'longest_saturated_duration_us': (
            longest_consecutive(saturated_indices) * window_us),
        'observed_bins': total_bins,
        'active_task_flow_count_p50': (
            sparse_percentile(task_counts, total_bins, 0.50)
            if flow_available else ''),
        'active_task_flow_count_p95': (
            sparse_percentile(task_counts, total_bins, 0.95)
            if flow_available else ''),
        'active_task_flow_count_p99': (
            sparse_percentile(task_counts, total_bins, 0.99)
            if flow_available else ''),
        'active_task_flow_count_max': (
            max(task_counts, default=0.0) if flow_available else ''),
        'active_hash_key_count_p99': '',
        'flow_metrics_available': int(flow_available),
    }


def summarize_bundle(
    src_switch: int,
    dst_switch: int,
    lanes: Sequence[DirectedLane],
    member_bins: Sequence[Mapping[int, int]],
    member_task_bins: Sequence[Mapping[int, Set[int]]],
    window_us: int,
    first_bin: int,
    last_bin: int,
    saturation_ratio: float,
    spare_ratio: float,
    roles: Mapping[int, str],
    timeseries_writer: Optional[csv.DictWriter],
    flow_available: bool,
) -> dict:
    total_bins = last_bin - first_bin + 1
    active_indices = sorted(set().union(*(bins.keys() for bins in member_bins)))
    bundle_rates: List[float] = []
    max_lane_rates: List[float] = []
    active_jains: List[float] = []
    bundle_task_counts: List[float] = []
    imbalance_indices: List[int] = []
    max_saturated_lanes = 0
    max_spare_lanes = 0

    for bin_index in active_indices:
        lane_rates = [
            throughput_gbps(bins.get(bin_index, 0), window_us)
            for bins in member_bins
        ]
        bundle_rate = sum(lane_rates)
        max_index = max(range(len(lane_rates)), key=lambda index: lane_rates[index])
        max_rate = lane_rates[max_index]
        min_rate = min(lane_rates, default=0.0)
        mean_rate = bundle_rate / len(lanes)
        utilizations = [
            rate / lane.rate_gbps if lane.rate_gbps else 0.0
            for rate, lane in zip(lane_rates, lanes)
        ]
        max_utilization = max(utilizations, default=0.0)
        min_utilization = min(utilizations, default=0.0)
        saturated_lanes = sum(value >= saturation_ratio for value in utilizations)
        spare_lanes = sum(value <= spare_ratio for value in utilizations)
        imbalance = int(
            max_utilization >= saturation_ratio and spare_lanes > 0)
        if imbalance:
            imbalance_indices.append(bin_index)
        max_saturated_lanes = max(max_saturated_lanes, saturated_lanes)
        max_spare_lanes = max(max_spare_lanes, spare_lanes)
        bundle_rates.append(bundle_rate)
        max_lane_rates.append(max_rate)
        active_jains.append(jain_fairness(lane_rates))

        active_tasks: Set[int] = set()
        for task_bins in member_task_bins:
            active_tasks.update(task_bins.get(bin_index, set()))
        bundle_task_counts.append(float(len(active_tasks)))

        if timeseries_writer is not None:
            timeseries_writer.writerow({
                'window_us': window_us,
                'window_index': bin_index,
                'window_start_us': bin_index * window_us,
                'window_end_us': (bin_index + 1) * window_us,
                'src_switch': src_switch,
                'dst_switch': dst_switch,
                'src_role': roles.get(src_switch, 'SWITCH'),
                'dst_role': roles.get(dst_switch, 'SWITCH'),
                'parallel_links': len(lanes),
                'bundle_capacity_gbps': '{:.9f}'.format(
                    sum(lane.rate_gbps for lane in lanes)),
                'bundle_throughput_gbps': '{:.9f}'.format(bundle_rate),
                'active_lanes': sum(rate > 0 for rate in lane_rates),
                'saturated_lanes': saturated_lanes,
                'spare_lanes': spare_lanes,
                'max_lane_gbps': '{:.9f}'.format(max_rate),
                'max_lane_port': lanes[max_index].src_port,
                'min_lane_gbps': '{:.9f}'.format(min_rate),
                'mean_lane_gbps': '{:.9f}'.format(mean_rate),
                'max_lane_utilization': '{:.9f}'.format(max_utilization),
                'min_lane_utilization': '{:.9f}'.format(min_utilization),
                'lane_skew': '{:.9f}'.format(
                    max_rate / mean_rate if mean_rate else 0.0),
                'jain_fairness': '{:.9f}'.format(jain_fairness(lane_rates)),
                'potential_lane_imbalance': imbalance,
                'active_task_flow_count': (
                    len(active_tasks) if flow_available else ''),
                'flow_metrics_available': int(flow_available),
            })

    total_bundle_bytes = sum(sum(bins.values()) for bins in member_bins)
    duration_us = total_bins * window_us
    return {
        'window_us': window_us,
        'src_switch': src_switch,
        'dst_switch': dst_switch,
        'src_role': roles.get(src_switch, 'SWITCH'),
        'dst_role': roles.get(dst_switch, 'SWITCH'),
        'parallel_links': len(lanes),
        'bundle_capacity_gbps': sum(lane.rate_gbps for lane in lanes),
        'average_bundle_gbps': throughput_gbps(total_bundle_bytes, duration_us),
        'p99_bundle_gbps': sparse_percentile(bundle_rates, total_bins, 0.99),
        'max_bundle_gbps': max(bundle_rates, default=0.0),
        'hottest_p99_port': '',
        'hottest_port_p99_gbps': 0.0,
        'p99_max_lane_gbps': sparse_percentile(max_lane_rates, total_bins, 0.99),
        'max_lane_gbps': max(max_lane_rates, default=0.0),
        'mean_jain_fairness_active': (
            sum(active_jains) / len(active_jains) if active_jains else 0.0),
        'min_jain_fairness_active': min(active_jains, default=0.0),
        'potential_imbalance_windows': len(imbalance_indices),
        'potential_imbalance_fraction': len(imbalance_indices) / total_bins,
        'longest_potential_imbalance_us': (
            longest_consecutive(imbalance_indices) * window_us),
        'max_saturated_lanes': max_saturated_lanes,
        'max_spare_lanes': max_spare_lanes,
        'observed_bins': total_bins,
        'flow_metrics_available': int(flow_available),
        'active_task_flow_count_p99': (
            sparse_percentile(bundle_task_counts, total_bins, 0.99)
            if flow_available else ''),
        'active_task_flow_count_max': (
            max(bundle_task_counts, default=0.0) if flow_available else ''),
    }


def analyze_port_hotspots(
    case_dir: Path,
    output_dir: Path,
    windows_us: Sequence[int],
    saturation_ratio: float = 0.95,
    spare_ratio: float = 0.50,
    timeseries_window_us: Optional[int] = 1000,
    start_us: Optional[float] = None,
    end_us: Optional[float] = None,
    progress_every: int = 25,
) -> dict:
    case_dir = case_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    windows = sorted(set(int(value) for value in windows_us))
    if not windows or windows[0] <= 0:
        raise ValueError('windows_us must contain positive integers')
    if any(window % windows[0] for window in windows):
        raise ValueError('every window must be a multiple of the smallest window')
    if not 0 < saturation_ratio <= 1:
        raise ValueError('saturation_ratio must be in (0, 1]')
    if not 0 <= spare_ratio < saturation_ratio:
        raise ValueError('spare_ratio must be >= 0 and less than saturation_ratio')
    if timeseries_window_us is not None and timeseries_window_us not in windows:
        raise ValueError('timeseries_window_us must be one of {}'.format(windows))
    if start_us is not None and end_us is not None and end_us <= start_us:
        raise ValueError('end_us must be greater than start_us')

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
    all_lanes = [lane for lanes in bundles.values() for lane in lanes]
    config = read_relevant_config(case_dir / 'network_attribute.txt')

    bounds = existing_observed_bounds(case_dir)
    bounds_source = 'output/switch_hotspots/analysis_summary.json'
    if bounds is None or start_us is not None or end_us is not None:
        bounds = scan_trace_bounds(
            runlog_dir, all_lanes, windows[0], start_us, end_us)
        bounds_source = 'PortTrace bound scan'
    first_event_us, last_event_us = bounds
    if start_us is not None:
        first_event_us = max(first_event_us, start_us)
    if end_us is not None:
        last_event_us = min(last_event_us, end_us)
    if last_event_us < first_event_us:
        raise ValueError('selected interval contains no switch-to-switch Port Tx events')

    port_timeseries_path = output_dir / 'physical_port_timeseries.csv'
    bundle_timeseries_path = output_dir / 'bundle_lane_balance_timeseries.csv'
    port_stream = port_timeseries_path.open('w', newline='', encoding='utf-8')
    bundle_stream = bundle_timeseries_path.open('w', newline='', encoding='utf-8')
    port_writer = csv.DictWriter(port_stream, fieldnames=PORT_TIMESERIES_FIELDS)
    bundle_writer = csv.DictWriter(bundle_stream, fieldnames=BUNDLE_TIMESERIES_FIELDS)
    port_writer.writeheader()
    bundle_writer.writeheader()

    port_summaries: List[dict] = []
    bundle_summaries: List[dict] = []
    missing_trace_files = 0
    malformed_tx_lines = 0
    wrong_port_lines = 0
    total_tx_events = 0
    total_tx_bytes = 0
    instrumented_tx_events = 0
    task_tagged_tx_events = 0

    try:
        for bundle_number, ((src_switch, dst_switch), lanes) in enumerate(
            sorted(bundles.items()), 1
        ):
            flow_traces: List[FlowAwareTrace] = []
            base_bins: List[Dict[int, int]] = []
            base_task_bins: List[Dict[int, Set[int]]] = []
            for lane in lanes:
                flow_trace = parse_port_tx_bins_with_tasks(
                    trace_path(runlog_dir, lane.src_node, lane.src_port),
                    lane.src_port, windows[0], start_us, end_us)
                trace = flow_trace.trace
                flow_traces.append(flow_trace)
                base_bins.append(trace.bins)
                base_task_bins.append(flow_trace.task_ids_by_bin)
                if not trace.file_exists:
                    missing_trace_files += 1
                malformed_tx_lines += trace.malformed_tx_lines
                wrong_port_lines += trace.wrong_port_lines
                total_tx_events += trace.tx_events
                total_tx_bytes += trace.total_bytes
                instrumented_tx_events += flow_trace.instrumented_tx_events
                task_tagged_tx_events += flow_trace.task_tagged_tx_events

            for window_us in windows:
                factor = window_us // windows[0]
                member_bins = [roll_bins(bins, factor) for bins in base_bins]
                member_task_bins = [
                    roll_task_bins(bins, factor) for bins in base_task_bins]
                bundle_flow_available = all(
                    flow_trace.trace.tx_events == 0
                    or flow_trace.instrumented_tx_events == flow_trace.trace.tx_events
                    for flow_trace in flow_traces)
                first_bin, last_bin = observed_bin_range(
                    window_us, first_event_us, last_event_us,
                    start_us, end_us)
                for lane, flow_trace, bins, task_bins in zip(
                    lanes, flow_traces, member_bins, member_task_bins
                ):
                    port_summaries.append(summarize_port(
                        lane, flow_trace, bins, task_bins,
                        window_us, first_bin, last_bin,
                        saturation_ratio, roles))
                    if timeseries_window_us == window_us:
                        saturation_rate = lane.rate_gbps * saturation_ratio
                        for bin_index in sorted(bins):
                            num_bytes = bins[bin_index]
                            rate = throughput_gbps(num_bytes, window_us)
                            port_writer.writerow({
                                'window_us': window_us,
                                'window_index': bin_index,
                                'window_start_us': bin_index * window_us,
                                'window_end_us': (bin_index + 1) * window_us,
                                'src_switch': lane.src_node,
                                'src_port': lane.src_port,
                                'dst_switch': lane.dst_node,
                                'dst_port': lane.dst_port,
                                'src_role': roles.get(lane.src_node, 'SWITCH'),
                                'dst_role': roles.get(lane.dst_node, 'SWITCH'),
                                'capacity_gbps': '{:.9f}'.format(lane.rate_gbps),
                                'saturation_threshold_gbps': '{:.9f}'.format(
                                    saturation_rate),
                                'tx_bytes': num_bytes,
                                'throughput_gbps': '{:.9f}'.format(rate),
                                'utilization': '{:.9f}'.format(
                                    rate / lane.rate_gbps if lane.rate_gbps else 0.0),
                                'saturated': int(rate >= saturation_rate),
                                'active_task_flow_count': (
                                    len(task_bins.get(bin_index, set()))
                                    if flow_trace.instrumented_tx_events == flow_trace.trace.tx_events
                                    else ''),
                                'active_hash_key_count': '',
                                'flow_metrics_available': int(
                                    flow_trace.instrumented_tx_events
                                    == flow_trace.trace.tx_events),
                            })

                bundle_summaries.append(summarize_bundle(
                    src_switch, dst_switch, lanes, member_bins,
                    member_task_bins,
                    window_us, first_bin, last_bin,
                    saturation_ratio, spare_ratio, roles,
                    bundle_writer if timeseries_window_us == window_us else None,
                    bundle_flow_available))

            if progress_every > 0 and bundle_number % progress_every == 0:
                print('[INFO] processed {}/{} directed switch bundles'.format(
                    bundle_number, len(bundles)))
    finally:
        port_stream.close()
        bundle_stream.close()

    flow_metrics_available = (
        total_tx_events > 0 and instrumented_tx_events == total_tx_events)
    task_summary_fields = [
        'active_task_flow_count_p50', 'active_task_flow_count_p95',
        'active_task_flow_count_p99', 'active_task_flow_count_max',
    ]
    if flow_metrics_available:
        for row in port_summaries:
            if not row['flow_metrics_available']:
                for field in task_summary_fields:
                    row[field] = 0.0
                row['flow_metrics_available'] = 1
    else:
        for row in port_summaries:
            for field in task_summary_fields:
                row[field] = ''
            row['flow_metrics_available'] = 0
        for row in bundle_summaries:
            row['active_task_flow_count_p99'] = ''
            row['active_task_flow_count_max'] = ''
            row['flow_metrics_available'] = 0

    port_lookup = {
        (row['window_us'], row['src_switch'], row['dst_switch'], row['src_port']): row
        for row in port_summaries
    }
    for row in bundle_summaries:
        candidates = [
            port_lookup[(row['window_us'], row['src_switch'], row['dst_switch'], lane.src_port)]
            for lane in bundles[(row['src_switch'], row['dst_switch'])]
        ]
        hottest = max(candidates, key=lambda item: (
            item['p99_gbps'], item['max_gbps'], -item['src_port']))
        row['hottest_p99_port'] = hottest['src_port']
        row['hottest_port_p99_gbps'] = hottest['p99_gbps']

    port_summaries.sort(key=lambda row: (
        row['window_us'], -row['p99_gbps'], -row['max_gbps'],
        row['src_switch'], row['src_port']))
    bundle_summaries.sort(key=lambda row: (
        row['window_us'], -row['potential_imbalance_fraction'],
        -row['p99_max_lane_gbps'], row['src_switch'], row['dst_switch']))
    write_dict_rows(
        output_dir / 'physical_port_hotspot_summary.csv',
        PORT_SUMMARY_FIELDS, port_summaries)
    write_dict_rows(
        output_dir / 'bundle_lane_balance_summary.csv',
        BUNDLE_SUMMARY_FIELDS, bundle_summaries)

    hottest_by_window = {}
    for window_us in windows:
        candidates = [row for row in port_summaries if row['window_us'] == window_us]
        if candidates:
            top = max(candidates, key=lambda row: (
                row['p99_gbps'], row['max_gbps']))
            hottest_by_window[str(window_us)] = {
                'src_switch': top['src_switch'],
                'src_port': top['src_port'],
                'dst_switch': top['dst_switch'],
                'dst_port': top['dst_port'],
                'capacity_gbps': top['capacity_gbps'],
                'p99_gbps': top['p99_gbps'],
                'max_gbps': top['max_gbps'],
                'saturated_fraction': top['saturated_fraction'],
                'longest_saturated_duration_us': top['longest_saturated_duration_us'],
            }

    report = {
        'case_dir': str(case_dir),
        'output_dir': str(output_dir),
        'windows_us': windows,
        'timeseries_window_us': timeseries_window_us,
        'saturation_ratio': saturation_ratio,
        'spare_ratio': spare_ratio,
        'first_switch_tx_us': first_event_us,
        'last_switch_tx_us': last_event_us,
        'bounds_source': bounds_source,
        'directed_switch_bundles': len(bundles),
        'directed_physical_ports': len(all_lanes),
        'missing_zero_traffic_trace_files': missing_trace_files,
        'malformed_tx_lines': malformed_tx_lines,
        'wrong_port_lines': wrong_port_lines,
        'switch_port_tx_events': total_tx_events,
        'switch_port_tx_bytes': total_tx_bytes,
        'instrumented_port_tx_events': instrumented_tx_events,
        'task_tagged_port_tx_events': task_tagged_tx_events,
        'task_trace_coverage': (
            instrumented_tx_events / total_tx_events if total_tx_events else 0.0),
        'network_config': config,
        'flow_metrics_available': flow_metrics_available,
        'task_flow_metrics_available': flow_metrics_available,
        'hash_key_metrics_available': False,
        'flow_metrics_reason': (
            'Compact TaskId suffix is present on every selected Port Tx event; '
            'active task-flow counts are exact per window. Routing hash-key '
            'counts remain unavailable because the routing key is not logged.'
            if flow_metrics_available else
            'PortTrace lacks the compact TaskId suffix on one or more selected '
            'Port Tx events. This completed run can provide exact port load but '
            'cannot reconstruct simultaneous task/hash-flow counts.'),
        'hottest_port_by_window': hottest_by_window,
        'notes': [
            'Port Tx is carried load. A capacity-limited port cannot reveal excess offered demand without queue tracing.',
            'Saturation means carried rate >= saturation_ratio * topology capacity.',
            'Potential lane imbalance means one lane is saturated while another lane in the same directed bundle is at or below spare_ratio.',
            'Potential lane imbalance is not labeled an ECMP collision because the routing hash key is unavailable.',
            'Active task-flow count means distinct TaskId values with at least one tagged packet transmitted in the window.',
            'With packet spray, one TaskId may appear on multiple parallel ports in the same window.',
            'TaskId is a workload-flow identity, not the ECMP routing hash key.',
            'Short-window rates can slightly exceed capacity because a whole packet is recorded at transmission start.',
        ],
    }
    with (output_dir / 'port_analysis_summary.json').open(
        'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    return report


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Analyze individual switch-to-switch output-port saturation and '
            'parallel-lane imbalance from ns-3-UB PortTrace.'))
    parser.add_argument('case_dir', type=Path)
    parser.add_argument(
        '--output-dir', type=Path, default=None,
        help='default: <case-dir>/output/port_hotspots')
    parser.add_argument(
        '--windows-us', default='100,1000,10000,100000',
        help='summary windows; every value must be a multiple of the smallest')
    parser.add_argument(
        '--timeseries-window-us', type=int, default=1000,
        help='window written to timeseries CSVs (default: 1000)')
    parser.add_argument(
        '--no-timeseries', action='store_true',
        help='write summaries only; skip sparse timeseries rows')
    parser.add_argument(
        '--saturation-ratio', type=float, default=0.95,
        help='port saturation threshold relative to capacity (default: 0.95)')
    parser.add_argument(
        '--spare-ratio', type=float, default=0.50,
        help='lane considered spare below this utilization (default: 0.50)')
    parser.add_argument('--start-us', type=float, default=None)
    parser.add_argument('--end-us', type=float, default=None)
    parser.add_argument('--progress-every', type=int, default=25)
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    try:
        windows = parse_windows(args.windows_us)
        output_dir = args.output_dir or args.case_dir / 'output' / 'port_hotspots'
        report = analyze_port_hotspots(
            case_dir=args.case_dir,
            output_dir=output_dir,
            windows_us=windows,
            saturation_ratio=args.saturation_ratio,
            spare_ratio=args.spare_ratio,
            timeseries_window_us=(
                None if args.no_timeseries else args.timeseries_window_us),
            start_us=args.start_us,
            end_us=args.end_us,
            progress_every=args.progress_every,
        )
        print('=' * 88)
        print('ns-3-UB physical port hotspot analysis complete')
        print('=' * 88)
        print('Case                    : {}'.format(report['case_dir']))
        print('Output                  : {}'.format(report['output_dir']))
        print('Observed TX interval     : {:.3f}us .. {:.3f}us'.format(
            report['first_switch_tx_us'], report['last_switch_tx_us']))
        print('Directed physical ports : {}'.format(report['directed_physical_ports']))
        print('Flow metrics available  : {}'.format(report['flow_metrics_available']))
        for window, hottest in report['hottest_port_by_window'].items():
            print(
                'Hottest P99 @{}us       : node {} port {} -> node {} port {} '
                'p99={:.3f}Gbps max={:.3f}Gbps sat={:.3%}'.format(
                    window, hottest['src_switch'], hottest['src_port'],
                    hottest['dst_switch'], hottest['dst_port'],
                    hottest['p99_gbps'], hottest['max_gbps'],
                    hottest['saturated_fraction']))
        print('=' * 88)
        return 0
    except Exception as exc:
        print('[ERROR] {}'.format(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
