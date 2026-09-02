#!/usr/bin/env python3
"""Render dependency-free SVG charts from analyze_port_hotspots.py CSVs.

Only Python's standard library is used.  This avoids the Matplotlib/Pillow ABI
problem commonly seen when the ns-3 host mixes Python 3.10 system packages with
Python 3.11 site packages.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


BG = '#ffffff'
GRID = '#dbe4ee'
TEXT = '#172033'
MUTED = '#5f6b7a'
NAVY = '#075985'
BLUE = '#38bdf8'
PALE_BLUE = '#bae6fd'
ORANGE = '#ea580c'
RED = '#b91c1c'
GREEN = '#15803d'
MISSING = '#eef2f6'
SERIES_COLORS = [
    '#075985', '#b91c1c', '#15803d', '#7e22ce', '#c2410c',
    '#0f766e', '#be185d', '#4338ca', '#4d7c0f', '#a16207',
]


def load_csv(path: Path) -> List[dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def number(row: Mapping[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, ''))
    except (TypeError, ValueError):
        return default


def integer(row: Mapping[str, str], key: str, default: int = 0) -> int:
    try:
        return int(float(row.get(key, '')))
    except (TypeError, ValueError):
        return default


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text_element(
    x: float,
    y: float,
    value: object,
    size: int = 13,
    anchor: str = 'start',
    fill: str = TEXT,
    weight: str = 'normal',
    rotate: Optional[float] = None,
) -> str:
    transform = '' if rotate is None else ' transform="rotate({} {} {})"'.format(
        rotate, x, y)
    return (
        '<text x="{:.2f}" y="{:.2f}" font-size="{}" text-anchor="{}" '
        'fill="{}" font-weight="{}"{}>{}</text>'.format(
            x, y, size, anchor, fill, weight, transform, esc(value)))


def line_element(
    x1: float, y1: float, x2: float, y2: float, stroke: str = GRID,
    width: float = 1.0, dash: Optional[str] = None,
) -> str:
    dash_attr = '' if dash is None else ' stroke-dasharray="{}"'.format(dash)
    return (
        '<line x1="{:.2f}" y1="{:.2f}" x2="{:.2f}" y2="{:.2f}" '
        'stroke="{}" stroke-width="{:.2f}"{} />'.format(
            x1, y1, x2, y2, stroke, width, dash_attr))


def rect_element(
    x: float, y: float, width: float, height: float, fill: str,
    stroke: str = 'none', title: Optional[str] = None,
) -> str:
    tooltip = '' if title is None else '<title>{}</title>'.format(esc(title))
    return (
        '<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" '
        'fill="{}" stroke="{}">{}</rect>'.format(
            x, y, max(0.0, width), max(0.0, height), fill, stroke, tooltip))


def svg_document(width: int, height: int, title: str, body: Iterable[str]) -> str:
    return '\n'.join([
        '<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" '
        'viewBox="0 0 {} {}" role="img" aria-label="{}">'.format(
            width, height, width, height, esc(title)),
        '<rect width="100%" height="100%" fill="{}" />'.format(BG),
        '<style>text { font-family: Arial, Helvetica, sans-serif; } '
        'polyline { stroke-linejoin: round; stroke-linecap: round; }</style>',
        *body,
        '</svg>',
    ])


def write_svg(path: Path, width: int, height: int, title: str, body: List[str]) -> None:
    path.write_text(svg_document(width, height, title, body), encoding='utf-8')


def nice_ticks(maximum: float, count: int = 5) -> List[float]:
    if maximum <= 0:
        return [0.0, 1.0]
    raw = maximum / max(1, count)
    exponent = 10 ** math.floor(math.log10(raw))
    fraction = raw / exponent
    step_fraction = 1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10
    step = step_fraction * exponent
    upper = math.ceil(maximum / step) * step
    return [index * step for index in range(int(round(upper / step)) + 1)]


def mix_color(low: str, high: str, fraction: float) -> str:
    fraction = max(0.0, min(1.0, fraction))
    lo = tuple(int(low[index:index + 2], 16) for index in (1, 3, 5))
    hi = tuple(int(high[index:index + 2], 16) for index in (1, 3, 5))
    rgb = tuple(round(a + (b - a) * fraction) for a, b in zip(lo, hi))
    return '#{:02x}{:02x}{:02x}'.format(*rgb)


def port_label(row: Mapping[str, str]) -> str:
    return '{}:p{} → {}:p{}'.format(
        row['src_switch'], row['src_port'], row['dst_switch'], row['dst_port'])


def port_key(row: Mapping[str, str]) -> Tuple[int, int, int, int]:
    return (
        integer(row, 'src_switch'), integer(row, 'src_port'),
        integer(row, 'dst_switch'), integer(row, 'dst_port'))


def choose_windows(
    summaries: Sequence[dict], requested: Optional[int], all_windows: bool,
) -> List[int]:
    available = sorted({integer(row, 'window_us') for row in summaries})
    if not available:
        raise ValueError('no windows found in physical_port_hotspot_summary.csv')
    if all_windows:
        return available
    if requested is None:
        return [1000 if 1000 in available else available[0]]
    if requested not in available:
        raise ValueError(
            'plot window {}us is unavailable; choose one of {}'.format(
                requested, available))
    return [requested]


def plot_top_ports(rows: Sequence[dict], path: Path, top_k: int, window_us: int) -> None:
    ranked = sorted(rows, key=lambda row: (
        number(row, 'p99_utilization'), number(row, 'p99_gbps'),
        number(row, 'max_gbps')), reverse=True)[:top_k]
    if not ranked:
        raise ValueError('no port summary rows for {}us'.format(window_us))

    width = 1280
    left, right, top, bottom = 245, 90, 105, 80
    row_height = 38
    height = top + bottom + row_height * len(ranked)
    plot_width = width - left - right
    max_value = max(
        max(number(row, 'max_gbps'), number(row, 'capacity_gbps'))
        for row in ranked) * 1.04
    ticks = nice_ticks(max_value, 6)
    max_axis = max(ticks)
    body: List[str] = [
        text_element(36, 40, 'Top physical output ports by P99 utilization', 24, weight='bold'),
        text_element(36, 68, '{} µs windows · dark=P99 · light=maximum'.format(window_us), 14, fill=MUTED),
    ]
    y0 = top
    for tick in ticks:
        x = left + tick / max_axis * plot_width
        body.append(line_element(x, y0 - 10, x, height - bottom + 4))
        body.append(text_element(x, height - bottom + 27, '{:g}'.format(tick), 12, 'middle', MUTED))
    body.append(text_element(
        left + plot_width / 2, height - 20, 'Carried throughput (Gbps)', 14, 'middle', TEXT, 'bold'))

    for index, row in enumerate(ranked):
        y = y0 + index * row_height
        p99 = number(row, 'p99_gbps')
        maximum = number(row, 'max_gbps')
        capacity = number(row, 'capacity_gbps')
        threshold = number(row, 'saturation_threshold_gbps')
        saturation = number(row, 'saturated_fraction')
        label = port_label(row)
        tooltip = (
            '{} | P99 {:.3f} Gbps | max {:.3f} Gbps | capacity {:.3f} Gbps | '
            'saturated {:.2%}'.format(label, p99, maximum, capacity, saturation))
        body.append(text_element(left - 12, y + 20, label, 12, 'end'))
        body.append(rect_element(
            left, y + 6, maximum / max_axis * plot_width, 19,
            PALE_BLUE, title=tooltip))
        body.append(rect_element(
            left, y + 9, p99 / max_axis * plot_width, 13,
            NAVY, title=tooltip))
        threshold_x = left + threshold / max_axis * plot_width
        capacity_x = left + capacity / max_axis * plot_width
        body.append(line_element(threshold_x, y + 4, threshold_x, y + 27, ORANGE, 2))
        body.append(line_element(capacity_x, y + 3, capacity_x, y + 28, RED, 1.5, '3,2'))
        body.append(text_element(
            left + plot_width + 8, y + 20,
            'P99 {:.1f}G  sat {:.1%}'.format(p99, saturation), 11, fill=MUTED))
    body.append(line_element(36, 88, 54, 88, ORANGE, 3))
    body.append(text_element(60, 93, '95% saturation threshold', 12, fill=MUTED))
    body.append(line_element(230, 88, 248, 88, RED, 2, '3,2'))
    body.append(text_element(254, 93, 'port capacity', 12, fill=MUTED))
    write_svg(path, width, height, 'Top physical output ports', body)


def plot_port_heatmap(
    rows: Sequence[dict], path: Path, window_us: int, metric: str,
    title: str, subtitle: str, high_color: str, percent: bool,
) -> None:
    switches = sorted({integer(row, 'src_switch') for row in rows})
    ports = sorted({integer(row, 'src_port') for row in rows})
    values = {(integer(row, 'src_switch'), integer(row, 'src_port')): row for row in rows}
    if not switches or not ports:
        raise ValueError('no rows for heatmap')
    left, right, top, bottom = 110, 170, 120, 100
    cell_w = max(18, min(46, int(900 / len(switches))))
    cell_h = max(16, min(32, int(650 / len(ports))))
    width = left + right + cell_w * len(switches)
    height = top + bottom + cell_h * len(ports)
    body: List[str] = [
        text_element(30, 38, title, 23, weight='bold'),
        text_element(30, 66, subtitle, 13, fill=MUTED),
    ]
    max_capacity = max(number(row, 'capacity_gbps') for row in rows)
    scale_max = 1.0 if percent else max_capacity
    if scale_max <= 0:
        scale_max = 1.0

    for column, switch in enumerate(switches):
        x = left + column * cell_w + cell_w / 2
        body.append(text_element(x, top - 9, switch, 10, 'end', MUTED, rotate=-55))
    for row_index, port in enumerate(ports):
        y = top + row_index * cell_h
        body.append(text_element(left - 10, y + cell_h * 0.68, port, 10, 'end', MUTED))
        for column, switch in enumerate(switches):
            x = left + column * cell_w
            record = values.get((switch, port))
            if record is None:
                body.append(rect_element(x, y, cell_w - 1, cell_h - 1, MISSING))
                continue
            value = number(record, metric)
            color = mix_color('#f7fbff', high_color, value / scale_max)
            display = '{:.1%}'.format(value) if percent else '{:.2f} Gbps'.format(value)
            tooltip = '{} | {} | utilization {:.2%}'.format(
                port_label(record), display, number(record, 'p99_utilization'))
            body.append(rect_element(
                x, y, cell_w - 1, cell_h - 1, color, '#ffffff', tooltip))
            if cell_w >= 42 and cell_h >= 25:
                short = '{:.0%}'.format(value) if percent else '{:.0f}'.format(value)
                text_color = '#ffffff' if value / scale_max > 0.58 else TEXT
                body.append(text_element(
                    x + (cell_w - 1) / 2, y + cell_h * 0.67,
                    short, 9, 'middle', text_color))

    body.append(text_element(24, top + len(ports) * cell_h / 2, 'Output port ID', 13, 'middle', TEXT, 'bold', -90))
    body.append(text_element(left + len(switches) * cell_w / 2, height - 28, 'Source switch node ID', 13, 'middle', TEXT, 'bold'))
    legend_x = left + len(switches) * cell_w + 30
    legend_y = top
    legend_h = min(280, len(ports) * cell_h)
    steps = max(20, int(legend_h))
    for step in range(steps):
        fraction = 1 - step / max(1, steps - 1)
        body.append(rect_element(
            legend_x, legend_y + step * legend_h / steps,
            22, legend_h / steps + 1, mix_color('#f7fbff', high_color, fraction)))
    body.append(text_element(legend_x + 32, legend_y + 10,
                             '100%' if percent else '{:g}G'.format(scale_max), 11, fill=MUTED))
    body.append(text_element(legend_x + 32, legend_y + legend_h,
                             '0%', 11, fill=MUTED))
    write_svg(path, width, height, title, body)


def downsample_max(
    indices: Sequence[int], values: Sequence[float], max_points: int,
) -> Tuple[List[float], List[float]]:
    if len(indices) <= max_points:
        return [float(value) for value in indices], list(values)
    bucket = len(indices) / max_points
    result_x: List[float] = []
    result_y: List[float] = []
    for output_index in range(max_points):
        start = int(math.floor(output_index * bucket))
        end = max(start + 1, int(math.floor((output_index + 1) * bucket)))
        end = min(end, len(indices))
        chunk = values[start:end]
        peak_offset = max(range(len(chunk)), key=lambda offset: chunk[offset])
        result_x.append(float(indices[start + peak_offset]))
        result_y.append(chunk[peak_offset])
    return result_x, result_y


def plot_port_timeseries(
    summary_rows: Sequence[dict], timeseries_rows: Sequence[dict], path: Path,
    window_us: int, top_k: int, max_points: int,
) -> None:
    chosen = sorted(summary_rows, key=lambda row: (
        number(row, 'p99_utilization'), number(row, 'max_utilization')),
        reverse=True)[:min(top_k, len(SERIES_COLORS))]
    chosen_keys = {port_key(row) for row in chosen}
    relevant = [
        row for row in timeseries_rows
        if integer(row, 'window_us') == window_us and port_key(row) in chosen_keys
    ]
    if not relevant:
        raise ValueError(
            'physical_port_timeseries.csv has no rows for {}us; rerun the '
            'analyzer with --timeseries-window-us {}'.format(window_us, window_us))
    all_indices = [integer(row, 'window_index') for row in relevant]
    first_index, last_index = min(all_indices), max(all_indices)
    indices = list(range(first_index, last_index + 1))
    rate_by_port: Dict[Tuple[int, int, int, int], Dict[int, float]] = defaultdict(dict)
    for row in relevant:
        rate_by_port[port_key(row)][integer(row, 'window_index')] = number(row, 'throughput_gbps')

    width, height = 1380, 720
    left, right, top, bottom = 100, 310, 95, 95
    plot_width, plot_height = width - left - right, height - top - bottom
    max_capacity = max(number(row, 'capacity_gbps') for row in chosen)
    max_rate = max((number(row, 'throughput_gbps') for row in relevant), default=0.0)
    y_ticks = nice_ticks(max(max_capacity, max_rate) * 1.04, 6)
    y_max = max(y_ticks)
    body: List[str] = [
        text_element(34, 38, 'Hottest physical-port timelines', 24, weight='bold'),
        text_element(34, 66, '{} µs windows · missing sparse rows are zero · max-pool downsampling preserves peaks'.format(window_us), 13, fill=MUTED),
    ]
    for tick in y_ticks:
        y = top + plot_height - tick / y_max * plot_height
        body.append(line_element(left, y, left + plot_width, y))
        body.append(text_element(left - 10, y + 4, '{:g}'.format(tick), 11, 'end', MUTED))
    body.append(text_element(24, top + plot_height / 2, 'Carried throughput (Gbps)', 13, 'middle', TEXT, 'bold', -90))
    duration_ms = (last_index - first_index + 1) * window_us / 1000.0
    for part in range(6):
        fraction = part / 5
        x = left + fraction * plot_width
        body.append(text_element(x, top + plot_height + 25,
                                 '{:.1f}'.format(duration_ms * fraction), 11, 'middle', MUTED))
    body.append(text_element(left + plot_width / 2, height - 24, 'Time from plotted interval start (ms)', 13, 'middle', TEXT, 'bold'))

    capacities = {round(number(row, 'capacity_gbps'), 9) for row in chosen}
    if len(capacities) == 1:
        capacity = next(iter(capacities))
        for value, color, dash, label in [
            (capacity * 0.95, ORANGE, '6,4', '95% ({:.1f}G)'.format(capacity * 0.95)),
            (capacity, RED, '3,3', 'capacity ({:g}G)'.format(capacity)),
        ]:
            y = top + plot_height - value / y_max * plot_height
            body.append(line_element(left, y, left + plot_width, y, color, 1.5, dash))
            body.append(text_element(left + plot_width + 8, y + 4, label, 11, fill=color))

    span = max(1, last_index - first_index)
    for series_index, summary in enumerate(chosen):
        key = port_key(summary)
        values = [rate_by_port[key].get(index, 0.0) for index in indices]
        xs, ys = downsample_max(indices, values, max_points)
        points = ' '.join('{:.2f},{:.2f}'.format(
            left + (x - first_index) / span * plot_width,
            top + plot_height - y / y_max * plot_height)
            for x, y in zip(xs, ys))
        color = SERIES_COLORS[series_index % len(SERIES_COLORS)]
        body.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="1.7"><title>{}</title></polyline>'.format(
            points, color, esc(port_label(summary))))
        legend_y = top + 28 + series_index * 31
        body.append(line_element(left + plot_width + 26, legend_y, left + plot_width + 50, legend_y, color, 3))
        body.append(text_element(left + plot_width + 58, legend_y + 4, port_label(summary), 11))
        body.append(text_element(
            left + plot_width + 58, legend_y + 18,
            'P99 {:.1f}G · sat {:.1%}'.format(
                number(summary, 'p99_gbps'), number(summary, 'saturated_fraction')),
            10, fill=MUTED))
    write_svg(path, width, height, 'Hottest physical-port timelines', body)


def plot_bundle_imbalance(
    rows: Sequence[dict], path: Path, top_k: int, window_us: int,
) -> None:
    candidates = [row for row in rows if integer(row, 'parallel_links') > 1]
    ranked = sorted(candidates, key=lambda row: (
        number(row, 'potential_imbalance_fraction'),
        number(row, 'p99_max_lane_gbps')), reverse=True)[:top_k]
    if not ranked:
        return
    width = 1220
    left, right, top, bottom = 230, 180, 100, 80
    row_height = 38
    height = top + bottom + row_height * len(ranked)
    plot_width = width - left - right
    body: List[str] = [
        text_element(34, 38, 'Parallel-lane imbalance candidates', 24, weight='bold'),
        text_element(34, 66, '{} µs windows · saturated lane ≥95% while another lane ≤50%'.format(window_us), 13, fill=MUTED),
    ]
    for part in range(6):
        fraction = part / 5
        x = left + fraction * plot_width
        body.append(line_element(x, top - 8, x, height - bottom + 4))
        body.append(text_element(x, height - bottom + 26, '{:.0%}'.format(fraction), 11, 'middle', MUTED))
    for index, row in enumerate(ranked):
        y = top + index * row_height
        value = number(row, 'potential_imbalance_fraction')
        label = '{} → {} ({} lanes)'.format(
            row['src_switch'], row['dst_switch'], row['parallel_links'])
        body.append(text_element(left - 12, y + 20, label, 12, 'end'))
        body.append(rect_element(
            left, y + 6, value * plot_width, 20,
            mix_color('#fed7aa', RED, value), title=(
                '{} | imbalance {:.2%} | longest {} us | hottest p{} P99 {:.3f} Gbps'.format(
                    label, value, row['longest_potential_imbalance_us'],
                    row['hottest_p99_port'], number(row, 'hottest_port_p99_gbps')))))
        body.append(text_element(
            left + plot_width + 8, y + 20,
            '{:.1%} · longest {} µs'.format(
                value, row['longest_potential_imbalance_us']), 11, fill=MUTED))
    body.append(text_element(left + plot_width / 2, height - 22,
                             'Fraction of all observed windows', 13, 'middle', TEXT, 'bold'))
    write_svg(path, width, height, 'Parallel-lane imbalance candidates', body)


def plot_load_vs_task_flows(
    rows: Sequence[dict], path: Path, window_us: int, label_count: int = 12,
) -> bool:
    available = [
        row for row in rows
        if row.get('active_task_flow_count_p99', '') not in ('', None)
        and integer(row, 'flow_metrics_available') == 1
    ]
    if not available:
        return False
    width, height = 1160, 720
    left, right, top, bottom = 105, 260, 100, 90
    plot_width, plot_height = width - left - right, height - top - bottom
    max_tasks = max(number(row, 'active_task_flow_count_max') for row in available)
    x_ticks = nice_ticks(max(1.0, max_tasks), 6)
    x_max = max(x_ticks)
    max_utilization = max(1.05, max(number(row, 'p99_utilization') for row in available) * 1.05)
    y_ticks = [index * 0.2 for index in range(int(math.ceil(max_utilization / 0.2)) + 1)]
    y_max = max(y_ticks)
    body: List[str] = [
        text_element(34, 38, 'Port load versus simultaneous task flows', 24, weight='bold'),
        text_element(
            34, 66,
            '{} µs windows · each point is one directed physical output port'.format(window_us),
            13, fill=MUTED),
    ]
    for tick in x_ticks:
        x = left + tick / x_max * plot_width
        body.append(line_element(x, top, x, top + plot_height))
        body.append(text_element(x, top + plot_height + 25, '{:g}'.format(tick), 11, 'middle', MUTED))
    for tick in y_ticks:
        y = top + plot_height - tick / y_max * plot_height
        body.append(line_element(left, y, left + plot_width, y))
        body.append(text_element(left - 10, y + 4, '{:.0%}'.format(tick), 11, 'end', MUTED))
    saturation_y = top + plot_height - 0.95 / y_max * plot_height
    body.append(line_element(left, saturation_y, left + plot_width, saturation_y, ORANGE, 2, '6,4'))
    body.append(text_element(left + plot_width + 8, saturation_y + 4, '95% saturation', 11, fill=ORANGE))
    body.append(text_element(left + plot_width / 2, height - 24,
                             'Maximum distinct active TaskId count in a window', 13, 'middle', TEXT, 'bold'))
    body.append(text_element(27, top + plot_height / 2, 'P99 port utilization', 13, 'middle', TEXT, 'bold', -90))

    labeled = set(port_key(row) for row in sorted(
        available,
        key=lambda row: (
            number(row, 'p99_utilization'),
            number(row, 'active_task_flow_count_max')),
        reverse=True)[:label_count])
    legend_y = top + 20
    label_index = 0
    for row in available:
        tasks = number(row, 'active_task_flow_count_max')
        utilization = number(row, 'p99_utilization')
        x = left + tasks / x_max * plot_width
        y = top + plot_height - utilization / y_max * plot_height
        color = RED if utilization >= 0.95 else NAVY
        radius = 4 + 4 * min(1.0, number(row, 'saturated_fraction'))
        tooltip = (
            '{} | max active TaskId {} | P99 utilization {:.2%} | P99 {:.3f} Gbps'.format(
                port_label(row), int(tasks), utilization, number(row, 'p99_gbps')))
        body.append(
            '<circle cx="{:.2f}" cy="{:.2f}" r="{:.2f}" fill="{}" fill-opacity="0.72" '
            'stroke="#ffffff" stroke-width="1"><title>{}</title></circle>'.format(
                x, y, radius, color, esc(tooltip)))
        if port_key(row) in labeled:
            body.append(line_element(
                left + plot_width + 22, legend_y + label_index * 27,
                left + plot_width + 38, legend_y + label_index * 27, color, 3))
            body.append(text_element(
                left + plot_width + 45, legend_y + 4 + label_index * 27,
                '{} · {} tasks'.format(port_label(row), int(tasks)), 10))
            label_index += 1
    write_svg(path, width, height, 'Port load versus simultaneous task flows', body)
    return True


def write_dashboard(
    path: Path, generated_svgs: Sequence[str], report: Mapping[str, object],
) -> None:
    flow_available = bool(report.get('flow_metrics_available', False))
    cards = [
        ('Physical ports', report.get('directed_physical_ports', '?')),
        ('Switch bundles', report.get('directed_switch_bundles', '?')),
        ('Flow metrics', 'available' if flow_available else 'unavailable'),
        ('Saturation threshold', '{:.0%}'.format(float(report.get('saturation_ratio', 0.95)))),
    ]
    card_html = ''.join(
        '<div class="card"><b>{}</b><span>{}</span></div>'.format(esc(label), esc(value))
        for label, value in cards)
    figures = ''.join(
        '<section><object data="{}" type="image/svg+xml"></object>'
        '<p><a href="{}">Open SVG</a></p></section>'.format(esc(name), esc(name))
        for name in generated_svgs)
    reason = report.get('flow_metrics_reason', '')
    document = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Port hotspot charts</title>
<style>
body {{ margin: 0; padding: 28px; background: #f4f7fb; color: #172033; font-family: Arial, sans-serif; }}
h1 {{ margin: 0 0 8px; }} .note {{ color: #5f6b7a; max-width: 1100px; }}
.cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 22px 0; }}
.card {{ background: white; border: 1px solid #dbe4ee; border-radius: 8px; padding: 14px 20px; min-width: 170px; }}
.card b, .card span {{ display: block; }} .card span {{ font-size: 23px; margin-top: 6px; }}
section {{ background: white; border: 1px solid #dbe4ee; border-radius: 8px; margin: 18px 0; padding: 12px; overflow: auto; }}
object {{ width: 100%; min-width: 900px; height: 720px; }} a {{ color: #075985; }}
</style></head><body>
<h1>ns-3-UB physical-port hotspot analysis</h1>
<p class="note">{reason}</p><div class="cards">{cards}</div>{figures}
</body></html>'''.format(reason=esc(reason), cards=card_html, figures=figures)
    path.write_text(document, encoding='utf-8')


def generate_port_plots(
    analysis_dir: Path,
    output_dir: Path,
    requested_window_us: Optional[int] = 1000,
    all_windows: bool = False,
    top_k: int = 20,
    timeseries_top_k: int = 8,
    max_points: int = 2500,
) -> List[str]:
    analysis_dir = analysis_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = load_csv(analysis_dir / 'physical_port_hotspot_summary.csv')
    bundle_summaries = load_csv(analysis_dir / 'bundle_lane_balance_summary.csv')
    report_path = analysis_dir / 'port_analysis_summary.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    plot_windows = choose_windows(summaries, requested_window_us, all_windows)

    timeseries_path = analysis_dir / 'physical_port_timeseries.csv'
    timeseries = load_csv(timeseries_path) if timeseries_path.is_file() else []
    generated: List[str] = []
    warnings: List[str] = []
    for window_us in plot_windows:
        rows = [row for row in summaries if integer(row, 'window_us') == window_us]
        bundle_rows = [
            row for row in bundle_summaries if integer(row, 'window_us') == window_us]

        name = 'port_topk_{}us.svg'.format(window_us)
        plot_top_ports(rows, output_dir / name, top_k, window_us)
        generated.append(name)

        name = 'port_p99_heatmap_{}us.svg'.format(window_us)
        max_capacity = max(number(row, 'capacity_gbps') for row in rows)
        plot_port_heatmap(
            rows, output_dir / name, window_us, 'p99_gbps',
            'Physical-port P99 carried throughput',
            '{} µs windows · fixed 0–{:g} Gbps scale · hover a cell for endpoints'.format(
                window_us, max_capacity), NAVY, False)
        generated.append(name)

        name = 'port_saturation_heatmap_{}us.svg'.format(window_us)
        plot_port_heatmap(
            rows, output_dir / name, window_us, 'saturated_fraction',
            'Physical-port saturation persistence',
            '{} µs windows · fraction with carried rate ≥95% of port capacity'.format(window_us),
            RED, True)
        generated.append(name)

        flow_name = 'port_load_vs_task_flows_{}us.svg'.format(window_us)
        if plot_load_vs_task_flows(rows, output_dir / flow_name, window_us):
            generated.append(flow_name)
        else:
            warnings.append(
                'active task-flow chart skipped for {}us: compact TaskId metrics '
                'are unavailable'.format(window_us))

        imbalance_name = 'bundle_lane_imbalance_{}us.svg'.format(window_us)
        before = len(generated)
        plot_bundle_imbalance(bundle_rows, output_dir / imbalance_name, top_k, window_us)
        if (output_dir / imbalance_name).is_file():
            generated.append(imbalance_name)
        if len(generated) == before:
            warnings.append('no parallel-link bundle rows for {}us'.format(window_us))

        timeline_name = 'port_timeseries_{}us.svg'.format(window_us)
        try:
            plot_port_timeseries(
                rows, timeseries, output_dir / timeline_name,
                window_us, timeseries_top_k, max_points)
            generated.append(timeline_name)
        except ValueError as exc:
            warnings.append(str(exc))

    dashboard_svgs = list(generated)
    dashboard_name = 'port_hotspot_plots.html'
    write_dashboard(output_dir / dashboard_name, dashboard_svgs, report)
    generated.append(dashboard_name)
    manifest = {
        'backend': 'svg-standard-library',
        'analysis_dir': str(analysis_dir),
        'output_dir': str(output_dir),
        'plot_windows_us': plot_windows,
        'top_k': top_k,
        'timeseries_top_k': timeseries_top_k,
        'max_points': max_points,
        'generated_files': generated,
        'warnings': warnings,
        'flow_metrics_available': report.get('flow_metrics_available', False),
    }
    (output_dir / 'port_plot_manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return generated


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Render physical-port hotspot SVGs without third-party packages.')
    parser.add_argument(
        'analysis_dir', type=Path,
        help='directory containing analyze_port_hotspots.py CSV outputs')
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--plot-window-us', type=int, default=1000)
    parser.add_argument('--all-windows', action='store_true')
    parser.add_argument('--top-k', type=int, default=20)
    parser.add_argument('--timeseries-top-k', type=int, default=8)
    parser.add_argument('--max-points', type=int, default=2500)
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    try:
        if args.top_k <= 0 or args.timeseries_top_k <= 0 or args.max_points < 10:
            raise ValueError('top-k values must be positive and max-points must be >= 10')
        output_dir = args.output_dir or args.analysis_dir / 'plots'
        generated = generate_port_plots(
            args.analysis_dir, output_dir,
            None if args.all_windows else args.plot_window_us,
            args.all_windows, args.top_k, args.timeseries_top_k,
            args.max_points)
        print('=' * 88)
        print('Dependency-free physical-port SVG rendering complete')
        print('=' * 88)
        print('Output : {}'.format(output_dir.expanduser().resolve()))
        for name in generated:
            print('  {}'.format(name))
        manifest = json.loads(
            (output_dir / 'port_plot_manifest.json').read_text(encoding='utf-8'))
        for warning in manifest.get('warnings', []):
            print('[WARN] {}'.format(warning))
        print('=' * 88)
        return 0
    except Exception as exc:
        print('[ERROR] {}'.format(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
