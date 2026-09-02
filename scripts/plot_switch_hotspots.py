#!/usr/bin/env python3
"""Create switch-hotspot figures from analyzer CSV outputs.

This script is intentionally independent from raw ns-3 PortTrace processing.
It reads the CSV/JSON files produced by ``analyze_switch_hotspots.py`` and can
load Matplotlib from a private ``pip --target`` directory.
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


SUMMARY_FILE = 'switch_hotspot_summary.csv'
TIMESERIES_FILE = 'switch_bundle_timeseries.csv'
REPORT_FILE = 'analysis_summary.json'
BLUE_DEEP = '#2171b5'
BLUE_DARKEST = '#08306b'
BLUE_LIGHT = '#9ecae1'
BLUE_PALE = '#eff6ff'
RED = '#cb181d'
GRID = '#d9d9d9'
TEXT = '#252525'
LINE_COLORS = [
    '#08306b', '#08519c', '#2171b5', '#4292c6', '#6baed6',
    '#9ecae1', '#3182bd', '#756bb1', '#31a354', '#636363',
]


def resolve_analysis_dir(path: Path) -> Path:
    """Accept either the analysis output directory or the ns-3 case directory."""
    path = path.expanduser().resolve()
    if (path / SUMMARY_FILE).is_file() and (path / TIMESERIES_FILE).is_file():
        return path
    candidate = path / 'output' / 'switch_hotspots'
    if (candidate / SUMMARY_FILE).is_file() and (candidate / TIMESERIES_FILE).is_file():
        return candidate
    raise FileNotFoundError(
        'cannot find {} and {} under {} or {}'.format(
            SUMMARY_FILE, TIMESERIES_FILE, path, candidate))


def load_matplotlib(package_dir: Optional[Path]):
    """Load a headless Matplotlib, optionally from a private package directory."""
    if package_dir is not None:
        resolved = package_dir.expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(
                'Matplotlib package directory does not exist: {}'.format(resolved))
        sys.path.insert(0, str(resolved))
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        hint = ''
        if package_dir is not None:
            hint = (
                '\nInstall it with:\n  {} -m pip install --target {} matplotlib'.format(
                    sys.executable, package_dir.expanduser().resolve()))
        raise RuntimeError(
            'Matplotlib failed to import: {}.{}'.format(exc, hint)) from exc
    return matplotlib, plt


def load_report(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def load_summaries(path: Path) -> List[dict]:
    integer_fields = {
        'window_us', 'src_switch', 'dst_switch', 'parallel_links',
        'total_tx_bytes', 'hot_windows', 'longest_hot_duration_us',
        'excess_bytes_above_threshold', 'saturated_windows', 'observed_bins',
    }
    text_fields = {'src_role', 'dst_role'}
    rows = []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        required = {
            'window_us', 'src_switch', 'dst_switch', 'src_role', 'dst_role',
            'capacity_gbps', 'threshold_gbps', 'average_gbps', 'p99_gbps',
            'max_gbps', 'hot_fraction',
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError('{} is missing required columns {}'.format(
                path, sorted(required)))
        for raw in reader:
            row = {}
            for key, value in raw.items():
                if key in text_fields:
                    row[key] = value
                elif key in integer_fields:
                    row[key] = int(value)
                else:
                    row[key] = float(value)
            rows.append(row)
    if not rows:
        raise ValueError('{} contains no summary rows'.format(path))
    return rows


def parse_formats(text: str) -> List[str]:
    supported = {'png', 'svg', 'pdf'}
    formats = []
    for item in text.split(','):
        value = item.strip().lower().lstrip('.')
        if value and value not in formats:
            formats.append(value)
    if not formats:
        raise ValueError('--formats must contain at least one of png,svg,pdf')
    bad = [value for value in formats if value not in supported]
    if bad:
        raise ValueError('unsupported plot format(s): {}'.format(', '.join(bad)))
    return formats


def choose_windows(
    summaries: Sequence[Mapping[str, object]],
    requested_window: Optional[int],
    all_windows: bool,
) -> List[int]:
    available = sorted({int(row['window_us']) for row in summaries})
    if all_windows:
        return available
    chosen = requested_window
    if chosen is None:
        chosen = 1000 if 1000 in available else available[0]
    if chosen not in available:
        raise ValueError(
            'plot window {}us is unavailable; choose one of {}'.format(
                chosen, available))
    return [chosen]


def downsample_max(
    points: Sequence[Tuple[float, float]],
    max_points: int,
) -> List[Tuple[float, float]]:
    """Preserve local peaks while limiting rasterization cost."""
    if max_points <= 0 or len(points) <= max_points:
        return list(points)
    bucket_size = int(math.ceil(len(points) / float(max_points)))
    result = []
    for offset in range(0, len(points), bucket_size):
        bucket = points[offset:offset + bucket_size]
        result.append(max(bucket, key=lambda point: point[1]))
    return result


def load_selected_timeseries(
    path: Path,
    window_us: int,
    selected_pairs: Sequence[Tuple[int, int]],
    first_event_us: float,
) -> Dict[Tuple[int, int], List[Tuple[float, float]]]:
    selected = set(selected_pairs)
    result: Dict[Tuple[int, int], List[Tuple[float, float]]] = defaultdict(list)
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        required = {
            'window_us', 'window_start_us', 'src_switch', 'dst_switch',
            'throughput_gbps',
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError('{} is missing required columns {}'.format(
                path, sorted(required)))
        for row in reader:
            if int(row['window_us']) != window_us:
                continue
            pair = int(row['src_switch']), int(row['dst_switch'])
            if pair not in selected:
                continue
            elapsed_ms = (float(row['window_start_us']) - first_event_us) / 1000.0
            result[pair].append((elapsed_ms, float(row['throughput_gbps'])))
    for points in result.values():
        points.sort(key=lambda point: point[0])
    return dict(result)


def save_figure(
    figure,
    output_dir: Path,
    stem: str,
    formats: Iterable[str],
    dpi: int,
) -> List[str]:
    files = []
    for extension in formats:
        path = output_dir / '{}.{}'.format(stem, extension)
        kwargs = {'bbox_inches': 'tight'}
        if extension == 'png':
            kwargs['dpi'] = dpi
        figure.savefig(str(path), **kwargs)
        files.append(path.name)
    return files


def svg_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def svg_text(
    x: float,
    y: float,
    value: object,
    size: int = 13,
    anchor: str = 'start',
    weight: str = 'normal',
    fill: str = TEXT,
    rotate: Optional[float] = None,
) -> str:
    transform = ''
    if rotate is not None:
        transform = ' transform="rotate({:.3f} {:.3f} {:.3f})"'.format(
            rotate, x, y)
    return (
        '<text x="{:.3f}" y="{:.3f}" font-size="{}" text-anchor="{}" '
        'font-weight="{}" fill="{}"{}>{}</text>'.format(
            x, y, size, anchor, weight, fill, transform, svg_escape(value)))


def svg_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str = TEXT,
    width: float = 1.0,
    dash: Optional[str] = None,
) -> str:
    dash_attr = '' if dash is None else ' stroke-dasharray="{}"'.format(dash)
    return (
        '<line x1="{:.3f}" y1="{:.3f}" x2="{:.3f}" y2="{:.3f}" '
        'stroke="{}" stroke-width="{:.3f}"{} />'.format(
            x1, y1, x2, y2, color, width, dash_attr))


def svg_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str,
    stroke: str = 'none',
    stroke_width: float = 0.0,
) -> str:
    return (
        '<rect x="{:.3f}" y="{:.3f}" width="{:.3f}" height="{:.3f}" '
        'fill="{}" stroke="{}" stroke-width="{:.3f}" />'.format(
            x, y, max(0.0, width), max(0.0, height), fill, stroke, stroke_width))


def svg_document(width: int, height: int, title: str, elements: Sequence[str]) -> str:
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" '
        'viewBox="0 0 {} {}">'.format(width, height, width, height),
        '<title>{}</title>'.format(svg_escape(title)),
        '<rect width="100%" height="100%" fill="white" />',
        '<g font-family="Arial, Helvetica, sans-serif">',
        *elements,
        '</g>',
        '</svg>',
        '',
    ])


def write_svg(
    output_dir: Path,
    stem: str,
    width: int,
    height: int,
    title: str,
    elements: Sequence[str],
) -> str:
    path = output_dir / '{}.svg'.format(stem)
    path.write_text(svg_document(width, height, title, elements), encoding='utf-8')
    return path.name


def nice_ticks(maximum: float, count: int = 5) -> List[float]:
    if maximum <= 0:
        return [0.0, 1.0]
    raw_step = maximum / max(1, count)
    magnitude = 10.0 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized <= 1.0:
        step = 1.0 * magnitude
    elif normalized <= 2.0:
        step = 2.0 * magnitude
    elif normalized <= 5.0:
        step = 5.0 * magnitude
    else:
        step = 10.0 * magnitude
    limit = math.ceil(maximum / step) * step
    return [index * step for index in range(int(round(limit / step)) + 1)]


def interpolate_color(low: str, high: str, fraction: float) -> str:
    fraction = min(1.0, max(0.0, fraction))
    low_rgb = [int(low[index:index + 2], 16) for index in (1, 3, 5)]
    high_rgb = [int(high[index:index + 2], 16) for index in (1, 3, 5)]
    values = [
        int(round(left + (right - left) * fraction))
        for left, right in zip(low_rgb, high_rgb)
    ]
    return '#{:02x}{:02x}{:02x}'.format(*values)


def svg_top_k(
    rows: Sequence[Mapping[str, object]],
    window_us: int,
    top_k: int,
    output_dir: Path,
) -> Tuple[List[str], List[Mapping[str, object]]]:
    top = sorted(
        rows,
        key=lambda row: (float(row['p99_gbps']), float(row['max_gbps'])),
        reverse=True)[:top_k]
    if not top:
        return [], []
    display_rows = list(reversed(top))
    width = 1200
    left, right, top_margin, bottom = 145, 55, 80, 115
    row_height = 45
    height = top_margin + bottom + row_height * len(display_rows)
    plot_width = width - left - right
    plot_height = row_height * len(display_rows)
    threshold = float(top[0]['threshold_gbps'])
    value_max = max(
        [threshold]
        + [float(row['max_gbps']) for row in display_rows]
        + [float(row['capacity_gbps']) for row in display_rows])
    ticks = nice_ticks(value_max * 1.03)
    axis_max = ticks[-1]

    elements = [
        svg_text(width / 2, 32,
                 'Top switch-pair hotspots ({} µs windows)'.format(window_us),
                 20, 'middle', 'bold'),
    ]
    for tick in ticks:
        x = left + tick / axis_max * plot_width
        elements.append(svg_line(x, top_margin, x, top_margin + plot_height, GRID, 1.0))
        elements.append(svg_text(x, top_margin + plot_height + 25,
                                 '{:g}'.format(tick), 12, 'middle'))
    elements.append(svg_line(
        left, top_margin + plot_height, width - right,
        top_margin + plot_height, TEXT, 1.2))

    for index, row in enumerate(display_rows):
        center = top_margin + index * row_height + row_height / 2
        p99 = float(row['p99_gbps'])
        maximum = float(row['max_gbps'])
        capacity = float(row['capacity_gbps'])
        elements.append(svg_text(
            left - 12, center + 4,
            '{}→{}'.format(row['src_switch'], row['dst_switch']),
            13, 'end'))
        elements.append(svg_rect(
            left, center - 15, p99 / axis_max * plot_width, 13, BLUE_DEEP))
        elements.append(svg_rect(
            left, center + 2, maximum / axis_max * plot_width, 13, BLUE_LIGHT))
        capacity_x = left + capacity / axis_max * plot_width
        elements.append(svg_line(
            capacity_x, center - 18, capacity_x, center + 18, TEXT, 3.0))

    threshold_x = left + threshold / axis_max * plot_width
    elements.append(svg_line(
        threshold_x, top_margin - 5, threshold_x,
        top_margin + plot_height, RED, 2.0, '7 5'))
    elements.extend([
        svg_text(left + plot_width / 2, height - 53,
                 'Carried load (Gbps)', 14, 'middle'),
        svg_text(22, top_margin + plot_height / 2,
                 'Directed switch pair', 14, 'middle', rotate=-90),
        svg_rect(left, height - 30, 18, 10, BLUE_DEEP),
        svg_text(left + 25, height - 20, 'P99 carried load', 12),
        svg_rect(left + 175, height - 30, 18, 10, BLUE_LIGHT),
        svg_text(left + 200, height - 20, 'Maximum carried load', 12),
        svg_line(left + 405, height - 34, left + 405, height - 17, TEXT, 3.0),
        svg_text(left + 417, height - 20, 'Actual bundle capacity', 12),
        svg_line(left + 625, height - 25, left + 650, height - 25,
                 RED, 2.0, '7 5'),
        svg_text(left + 660, height - 20,
                 '4×400G threshold ({:g} Gbps)'.format(threshold), 12),
    ])
    filename = write_svg(
        output_dir, 'hotspot_topk_{}us'.format(window_us),
        width, height, 'Top switch-pair hotspots', elements)
    return [filename], top


def svg_timeline(
    timeseries_path: Path,
    top: Sequence[Mapping[str, object]],
    window_us: int,
    first_event_us: float,
    max_points: int,
    output_dir: Path,
) -> List[str]:
    pairs = [(int(row['src_switch']), int(row['dst_switch'])) for row in top]
    series = load_selected_timeseries(
        timeseries_path, window_us, pairs, first_event_us)
    points_by_pair = {
        pair: downsample_max(series.get(pair, []), max_points)
        for pair in pairs
    }
    all_points = [point for points in points_by_pair.values() for point in points]
    if not all_points:
        return []
    width, height = 1280, 680
    left, right, top_margin, bottom = 95, 55, 70, 130
    plot_width = width - left - right
    plot_height = height - top_margin - bottom
    threshold = float(top[0]['threshold_gbps'])
    x_min = min(point[0] for point in all_points)
    x_max = max(point[0] for point in all_points)
    if x_max <= x_min:
        x_max = x_min + 1.0
    observed_max = max(point[1] for point in all_points)
    ticks = nice_ticks(max(threshold, observed_max) * 1.05)
    y_max = ticks[-1]

    def x_coord(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def y_coord(value: float) -> float:
        return top_margin + plot_height - value / y_max * plot_height

    elements = [
        svg_text(width / 2, 31,
                 'Top switch-pair carried-load timeline ({} µs windows)'.format(
                     window_us), 20, 'middle', 'bold'),
    ]
    for tick in ticks:
        y = y_coord(tick)
        elements.append(svg_line(left, y, width - right, y, GRID, 1.0))
        elements.append(svg_text(left - 12, y + 4, '{:g}'.format(tick), 12, 'end'))
    for index in range(6):
        value = x_min + (x_max - x_min) * index / 5.0
        x = x_coord(value)
        elements.append(svg_line(x, top_margin, x, top_margin + plot_height, GRID, 1.0))
        elements.append(svg_text(x, top_margin + plot_height + 24,
                                 '{:.3g}'.format(value), 12, 'middle'))
    elements.append(svg_line(left, top_margin, left, top_margin + plot_height, TEXT, 1.2))
    elements.append(svg_line(left, top_margin + plot_height, width - right,
                             top_margin + plot_height, TEXT, 1.2))

    for index, pair in enumerate(pairs):
        points = points_by_pair[pair]
        if not points:
            continue
        color = LINE_COLORS[index % len(LINE_COLORS)]
        coordinates = ' '.join(
            '{:.3f},{:.3f}'.format(x_coord(point[0]), y_coord(point[1]))
            for point in points)
        elements.append(
            '<polyline points="{}" fill="none" stroke="{}" '
            'stroke-width="1.6" stroke-linejoin="round" />'.format(
                coordinates, color))
    threshold_y = y_coord(threshold)
    elements.append(svg_line(
        left, threshold_y, width - right, threshold_y, RED, 2.0, '7 5'))
    elements.extend([
        svg_text(left + plot_width / 2, height - 75,
                 'Elapsed time from first observed switch TX (ms)',
                 14, 'middle'),
        svg_text(23, top_margin + plot_height / 2,
                 'Carried load (Gbps)', 14, 'middle', rotate=-90),
    ])
    legend_y = height - 38
    for index, pair in enumerate(pairs):
        column = index % 5
        row = index // 5
        x = left + column * 165
        y = legend_y + row * 20
        color = LINE_COLORS[index % len(LINE_COLORS)]
        elements.append(svg_line(x, y - 4, x + 25, y - 4, color, 2.2))
        elements.append(svg_text(
            x + 32, y, '{}→{}'.format(pair[0], pair[1]), 11))
    threshold_legend_x = left + 5 * 165
    elements.append(svg_line(
        threshold_legend_x, legend_y - 4,
        threshold_legend_x + 25, legend_y - 4, RED, 2.0, '7 5'))
    elements.append(svg_text(
        threshold_legend_x + 32, legend_y,
        '4×400G ({:g} Gbps)'.format(threshold), 11))
    filename = write_svg(
        output_dir, 'hotspot_timeseries_{}us'.format(window_us),
        width, height, 'Switch-pair carried-load timeline', elements)
    return [filename]


def svg_heatmap(
    rows: Sequence[Mapping[str, object]],
    src_role: str,
    dst_role: str,
    name: str,
    window_us: int,
    output_dir: Path,
) -> List[str]:
    selected = [row for row in rows
                if row['src_role'] == src_role and row['dst_role'] == dst_role]
    if not selected:
        return []
    src_ids = sorted({int(row['src_switch']) for row in selected})
    dst_ids = sorted({int(row['dst_switch']) for row in selected})
    values = {
        (int(row['src_switch']), int(row['dst_switch'])): float(row['p99_gbps'])
        for row in selected
    }
    threshold = max(float(row['threshold_gbps']) for row in selected)
    observed_max = max(values.values(), default=0.0)
    color_max = max(threshold, observed_max, 1.0)
    cell_width, cell_height = 62, 30
    left, top_margin, right, bottom = 100, 80, 180, 80
    width = left + right + cell_width * len(dst_ids)
    height = top_margin + bottom + cell_height * len(src_ids)
    elements = [
        svg_text(width / 2, 30,
                 'P99 carried load: {}→{} ({} µs, Gbps)'.format(
                     src_role, dst_role, window_us),
                 19, 'middle', 'bold'),
    ]
    for column, dst in enumerate(dst_ids):
        x = left + column * cell_width + cell_width / 2
        elements.append(svg_text(x, top_margin - 15, dst, 12, 'middle'))
    for row_index, src in enumerate(src_ids):
        y = top_margin + row_index * cell_height
        elements.append(svg_text(left - 12, y + cell_height / 2 + 4,
                                 src, 12, 'end'))
        for column, dst in enumerate(dst_ids):
            value = values.get((src, dst), 0.0)
            fraction = value / color_max
            color = interpolate_color(BLUE_PALE, BLUE_DARKEST, fraction)
            x = left + column * cell_width
            elements.append(svg_rect(
                x, y, cell_width, cell_height, color, 'white', 0.8))
            if cell_width >= 55:
                text_color = 'white' if fraction >= 0.52 else TEXT
                elements.append(svg_text(
                    x + cell_width / 2, y + cell_height / 2 + 4,
                    '{:.0f}'.format(value), 10, 'middle', fill=text_color))
    plot_height = cell_height * len(src_ids)
    elements.extend([
        svg_text(left + cell_width * len(dst_ids) / 2, height - 28,
                 '{} switch'.format(dst_role.title()), 14, 'middle'),
        svg_text(24, top_margin + plot_height / 2,
                 '{} switch'.format(src_role.title()),
                 14, 'middle', rotate=-90),
    ])
    bar_x = left + cell_width * len(dst_ids) + 42
    bar_y = top_margin
    bar_width = 24
    segments = 80
    for index in range(segments):
        fraction = 1.0 - index / float(segments - 1)
        y = bar_y + index * plot_height / segments
        elements.append(svg_rect(
            bar_x, y, bar_width, plot_height / segments + 0.5,
            interpolate_color(BLUE_PALE, BLUE_DARKEST, fraction)))
    elements.extend([
        svg_text(bar_x + bar_width + 10, bar_y + 5,
                 '{:.0f}'.format(color_max), 11),
        svg_text(bar_x + bar_width + 10, bar_y + plot_height,
                 '0', 11),
        svg_text(bar_x - 5, bar_y + plot_height + 26,
                 'Darker = hotter', 11),
    ])
    filename = write_svg(
        output_dir, '{}_p99_heatmap_{}us'.format(name, window_us),
        width, height, 'P99 carried-load heatmap', elements)
    return [filename]


def svg_window_comparison(
    summaries: Sequence[Mapping[str, object]],
    reference_top: Sequence[Mapping[str, object]],
    reference_window_us: int,
    output_dir: Path,
) -> List[str]:
    pairs = [(int(row['src_switch']), int(row['dst_switch']))
             for row in reference_top]
    windows = sorted({int(row['window_us']) for row in summaries})
    if not pairs or len(windows) < 2:
        return []
    lookup = {
        (int(row['src_switch']), int(row['dst_switch']), int(row['window_us'])):
            float(row['p99_gbps'])
        for row in summaries
    }
    threshold = float(reference_top[0]['threshold_gbps'])
    observed_max = max(
        (lookup.get((pair[0], pair[1], window), 0.0)
         for pair in pairs for window in windows), default=0.0)
    ticks = nice_ticks(max(threshold, observed_max) * 1.05)
    y_max = ticks[-1]
    width, height = 1100, 650
    left, right, top_margin, bottom = 95, 60, 75, 125
    plot_width = width - left - right
    plot_height = height - top_margin - bottom

    def x_coord(index: int) -> float:
        if len(windows) == 1:
            return left + plot_width / 2
        return left + index / float(len(windows) - 1) * plot_width

    def y_coord(value: float) -> float:
        return top_margin + plot_height - value / y_max * plot_height

    elements = [
        svg_text(width / 2, 29, 'Hotspot persistence across time scales',
                 20, 'middle', 'bold'),
        svg_text(width / 2, 51,
                 'Pairs ranked at {} µs'.format(reference_window_us),
                 13, 'middle'),
    ]
    for tick in ticks:
        y = y_coord(tick)
        elements.append(svg_line(left, y, width - right, y, GRID, 1.0))
        elements.append(svg_text(left - 12, y + 4, '{:g}'.format(tick), 12, 'end'))
    for index, window in enumerate(windows):
        x = x_coord(index)
        elements.append(svg_line(x, top_margin, x, top_margin + plot_height, GRID, 1.0))
        elements.append(svg_text(x, top_margin + plot_height + 25,
                                 '{}'.format(window), 12, 'middle'))
    elements.append(svg_line(left, top_margin, left, top_margin + plot_height, TEXT, 1.2))
    elements.append(svg_line(left, top_margin + plot_height, width - right,
                             top_margin + plot_height, TEXT, 1.2))
    for pair_index, pair in enumerate(pairs):
        color = LINE_COLORS[pair_index % len(LINE_COLORS)]
        coordinates = []
        for index, window in enumerate(windows):
            value = lookup.get((pair[0], pair[1], window), 0.0)
            x, y = x_coord(index), y_coord(value)
            coordinates.append('{:.3f},{:.3f}'.format(x, y))
            elements.append(
                '<circle cx="{:.3f}" cy="{:.3f}" r="3.2" fill="{}" />'.format(
                    x, y, color))
        elements.append(
            '<polyline points="{}" fill="none" stroke="{}" '
            'stroke-width="1.7" />'.format(' '.join(coordinates), color))
    threshold_y = y_coord(threshold)
    elements.append(svg_line(
        left, threshold_y, width - right, threshold_y, RED, 2.0, '7 5'))
    elements.extend([
        svg_text(left + plot_width / 2, height - 70,
                 'Aggregation window (µs)', 14, 'middle'),
        svg_text(23, top_margin + plot_height / 2,
                 'P99 carried load (Gbps)', 14, 'middle', rotate=-90),
    ])
    legend_y = height - 30
    for index, pair in enumerate(pairs):
        column = index % 5
        row = index // 5
        x = left + column * 155
        y = legend_y + row * 18
        color = LINE_COLORS[index % len(LINE_COLORS)]
        elements.append(svg_line(x, y - 4, x + 22, y - 4, color, 2.2))
        elements.append(svg_text(
            x + 28, y, '{}→{}'.format(pair[0], pair[1]), 10))
    filename = write_svg(
        output_dir,
        'hotspot_window_comparison_{}us_top'.format(reference_window_us),
        width, height, 'Hotspot persistence across time scales', elements)
    return [filename]


def write_svg_dashboard(output_dir: Path, files: Sequence[str]) -> str:
    svg_files = [name for name in files if name.endswith('.svg')]
    cards = []
    for name in svg_files:
        title = Path(name).stem.replace('_', ' ')
        cards.append(
            '<section><h2>{}</h2><img src="{}" alt="{}"></section>'.format(
                svg_escape(title), svg_escape(name), svg_escape(title)))
    document = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Switch hotspot plots</title>
<style>
body{{margin:0;background:#f5f7fa;color:#252525;font-family:Arial,Helvetica,sans-serif}}
main{{max-width:1500px;margin:0 auto;padding:24px}}
h1{{font-size:26px;margin:0 0 20px}}
section{{background:white;border:1px solid #d9e2ec;border-radius:10px;margin:0 0 22px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.05)}}
h2{{font-size:17px;margin:0 0 12px;color:#334e68}}
img{{display:block;width:100%;height:auto}}
</style>
</head>
<body><main><h1>Switch hotspot analysis</h1>{}</main></body>
</html>
'''.format(''.join(cards))
    path = output_dir / 'hotspot_plots.html'
    path.write_text(document, encoding='utf-8')
    return path.name


def generate_svg_plots(
    analysis_dir: Path,
    output_dir: Path,
    plot_windows: Sequence[int],
    top_k: int,
    max_points: int,
) -> List[str]:
    """Generate dependency-free SVG figures and an HTML dashboard."""
    summaries = load_summaries(analysis_dir / SUMMARY_FILE)
    report = load_report(analysis_dir / REPORT_FILE)
    first_event_us = float(report.get('first_switch_tx_us', 0.0))
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    reference_top = []
    reference_window = plot_windows[0]
    for window_us in plot_windows:
        rows = [row for row in summaries if int(row['window_us']) == window_us]
        top_files, top = svg_top_k(rows, window_us, top_k, output_dir)
        files.extend(top_files)
        files.extend(svg_timeline(
            analysis_dir / TIMESERIES_FILE, top, window_us,
            first_event_us, max_points, output_dir))
        files.extend(svg_heatmap(
            rows, 'LEAF', 'SPINE', 'leaf_to_spine', window_us, output_dir))
        files.extend(svg_heatmap(
            rows, 'SPINE', 'LEAF', 'spine_to_leaf', window_us, output_dir))
        if not reference_top:
            reference_top = top
            reference_window = window_us
    files.extend(svg_window_comparison(
        summaries, reference_top, reference_window, output_dir))
    files.append(write_svg_dashboard(output_dir, files))
    manifest = {
        'backend': 'svg',
        'analysis_dir': str(analysis_dir),
        'output_dir': str(output_dir),
        'plot_windows_us': list(plot_windows),
        'top_k': top_k,
        'formats': ['svg', 'html'],
        'generated_files': files,
    }
    with (output_dir / 'plot_manifest.json').open('w', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    return files


def plot_top_k(
    plt,
    rows: Sequence[Mapping[str, object]],
    window_us: int,
    top_k: int,
    output_dir: Path,
    formats: Sequence[str],
    dpi: int,
) -> Tuple[List[str], List[Mapping[str, object]]]:
    ranked = sorted(
        rows,
        key=lambda row: (float(row['p99_gbps']), float(row['max_gbps'])),
        reverse=True)
    top = ranked[:top_k]
    if not top:
        return [], []
    display_rows = list(reversed(top))
    labels = ['{}→{}'.format(row['src_switch'], row['dst_switch'])
              for row in display_rows]
    p99_values = [float(row['p99_gbps']) for row in display_rows]
    max_values = [float(row['max_gbps']) for row in display_rows]
    capacities = [float(row['capacity_gbps']) for row in display_rows]
    threshold = float(top[0]['threshold_gbps'])
    positions = list(range(len(display_rows)))

    figure, axis = plt.subplots(
        figsize=(11, max(5.0, 0.48 * len(display_rows) + 2.2)))
    axis.barh(
        [position - 0.18 for position in positions], p99_values,
        height=0.34, color='#2171b5', label='P99 carried load')
    axis.barh(
        [position + 0.18 for position in positions], max_values,
        height=0.34, color='#9ecae1', label='Maximum carried load')
    axis.scatter(
        capacities, positions, marker='|', s=220, color='#252525',
        linewidths=1.7, label='Actual bundle capacity', zorder=4)
    axis.axvline(
        threshold, color='#cb181d', linestyle='--', linewidth=1.4,
        label='4×400G threshold ({:g} Gbps)'.format(threshold))
    axis.set_yticks(positions)
    axis.set_yticklabels(labels)
    axis.set_xlabel('Carried load (Gbps)')
    axis.set_ylabel('Directed switch pair')
    axis.set_title('Top switch-pair hotspots ({} µs windows)'.format(window_us))
    axis.grid(axis='x', color='#d9d9d9', linewidth=0.7, alpha=0.8)
    axis.set_axisbelow(True)
    axis.legend(loc='best', frameon=False)
    figure.tight_layout()
    files = save_figure(
        figure, output_dir, 'hotspot_topk_{}us'.format(window_us), formats, dpi)
    plt.close(figure)
    return files, top


def plot_timeline(
    plt,
    timeseries_path: Path,
    top: Sequence[Mapping[str, object]],
    window_us: int,
    first_event_us: float,
    max_points: int,
    output_dir: Path,
    formats: Sequence[str],
    dpi: int,
) -> List[str]:
    pairs = [(int(row['src_switch']), int(row['dst_switch'])) for row in top]
    series = load_selected_timeseries(
        timeseries_path, window_us, pairs, first_event_us)
    if not any(series.values()):
        return []
    threshold = float(top[0]['threshold_gbps'])
    figure, axis = plt.subplots(figsize=(12, 6.2))
    colors = plt.cm.Blues([
        0.90 - 0.55 * index / max(1, len(pairs) - 1)
        for index in range(len(pairs))
    ])
    for pair, color in zip(pairs, colors):
        points = downsample_max(series.get(pair, []), max_points)
        if points:
            axis.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                linewidth=1.15, color=color,
                label='{}→{}'.format(pair[0], pair[1]))
    axis.axhline(
        threshold, color='#cb181d', linestyle='--', linewidth=1.35,
        label='4×400G threshold ({:g} Gbps)'.format(threshold))
    axis.set_xlabel('Elapsed time from first observed switch TX (ms)')
    axis.set_ylabel('Carried load (Gbps)')
    axis.set_title(
        'Top switch-pair carried-load timeline ({} µs windows)'.format(window_us))
    axis.grid(color='#d9d9d9', linewidth=0.7, alpha=0.75)
    axis.legend(ncol=2, fontsize=8, frameon=False)
    figure.tight_layout()
    files = save_figure(
        figure, output_dir, 'hotspot_timeseries_{}us'.format(window_us),
        formats, dpi)
    plt.close(figure)
    return files


def plot_direction_heatmap(
    plt,
    rows: Sequence[Mapping[str, object]],
    src_role: str,
    dst_role: str,
    name: str,
    window_us: int,
    output_dir: Path,
    formats: Sequence[str],
    dpi: int,
) -> List[str]:
    selected = [row for row in rows
                if row['src_role'] == src_role and row['dst_role'] == dst_role]
    if not selected:
        return []
    src_ids = sorted({int(row['src_switch']) for row in selected})
    dst_ids = sorted({int(row['dst_switch']) for row in selected})
    values = {
        (int(row['src_switch']), int(row['dst_switch'])): float(row['p99_gbps'])
        for row in selected
    }
    matrix = [[values.get((src, dst), 0.0) for dst in dst_ids] for src in src_ids]
    threshold = max(float(row['threshold_gbps']) for row in selected)
    observed_max = max((value for line in matrix for value in line), default=0.0)
    color_max = max(threshold, observed_max, 1.0)

    figure, axis = plt.subplots(
        figsize=(max(8.0, len(dst_ids) * 0.68),
                 max(5.0, len(src_ids) * 0.38 + 2.0)))
    image = axis.imshow(
        matrix, aspect='auto', interpolation='nearest', cmap='Blues',
        vmin=0.0, vmax=color_max)
    axis.set_xticks(range(len(dst_ids)))
    axis.set_xticklabels(dst_ids)
    axis.set_yticks(range(len(src_ids)))
    axis.set_yticklabels(src_ids)
    axis.set_xlabel('{} switch'.format(dst_role.title()))
    axis.set_ylabel('{} switch'.format(src_role.title()))
    axis.set_title(
        'P99 carried load: {}→{} ({} µs, Gbps)'.format(
            src_role, dst_role, window_us))
    colorbar = figure.colorbar(image, ax=axis)
    colorbar.set_label('P99 carried load (Gbps); darker = hotter')
    figure.tight_layout()
    files = save_figure(
        figure, output_dir, '{}_p99_heatmap_{}us'.format(name, window_us),
        formats, dpi)
    plt.close(figure)
    return files


def plot_window_comparison(
    plt,
    summaries: Sequence[Mapping[str, object]],
    reference_top: Sequence[Mapping[str, object]],
    reference_window_us: int,
    output_dir: Path,
    formats: Sequence[str],
    dpi: int,
) -> List[str]:
    """Show whether the selected hotspots persist at longer time scales."""
    pairs = [(int(row['src_switch']), int(row['dst_switch']))
             for row in reference_top]
    windows = sorted({int(row['window_us']) for row in summaries})
    lookup = {
        (int(row['src_switch']), int(row['dst_switch']), int(row['window_us'])):
            float(row['p99_gbps'])
        for row in summaries
    }
    if not pairs or len(windows) < 2:
        return []

    figure, axis = plt.subplots(figsize=(10.5, 6.0))
    colors = plt.cm.Blues([
        0.90 - 0.55 * index / max(1, len(pairs) - 1)
        for index in range(len(pairs))
    ])
    for pair, color in zip(pairs, colors):
        values = [lookup.get((pair[0], pair[1], window), 0.0)
                  for window in windows]
        axis.plot(
            windows, values, marker='o', linewidth=1.4, markersize=4,
            color=color, label='{}→{}'.format(pair[0], pair[1]))
    threshold = float(reference_top[0]['threshold_gbps'])
    axis.axhline(
        threshold, color='#cb181d', linestyle='--', linewidth=1.35,
        label='4×400G threshold ({:g} Gbps)'.format(threshold))
    axis.set_xscale('log')
    axis.set_xticks(windows)
    axis.set_xticklabels(['{:g}'.format(window) for window in windows])
    axis.set_xlabel('Aggregation window (µs, logarithmic scale)')
    axis.set_ylabel('P99 carried load (Gbps)')
    axis.set_title(
        'Hotspot persistence across time scales\n'
        '(pairs ranked at {} µs)'.format(reference_window_us))
    axis.grid(color='#d9d9d9', linewidth=0.7, alpha=0.75)
    axis.legend(ncol=2, fontsize=8, frameon=False)
    figure.tight_layout()
    files = save_figure(
        figure, output_dir,
        'hotspot_window_comparison_{}us_top'.format(reference_window_us),
        formats, dpi)
    plt.close(figure)
    return files


def generate_plots(
    analysis_dir: Path,
    output_dir: Path,
    plot_windows: Sequence[int],
    top_k: int,
    formats: Sequence[str],
    dpi: int,
    max_points: int,
    package_dir: Optional[Path] = None,
) -> List[str]:
    _, plt = load_matplotlib(package_dir)
    summaries = load_summaries(analysis_dir / SUMMARY_FILE)
    report = load_report(analysis_dir / REPORT_FILE)
    first_event_us = float(report.get('first_switch_tx_us', 0.0))
    output_dir.mkdir(parents=True, exist_ok=True)

    files = []
    reference_top = []
    reference_window = plot_windows[0]
    for window_us in plot_windows:
        rows = [row for row in summaries if int(row['window_us']) == window_us]
        top_files, top = plot_top_k(
            plt, rows, window_us, top_k, output_dir, formats, dpi)
        files.extend(top_files)
        files.extend(plot_timeline(
            plt, analysis_dir / TIMESERIES_FILE, top, window_us,
            first_event_us, max_points, output_dir, formats, dpi))
        files.extend(plot_direction_heatmap(
            plt, rows, 'LEAF', 'SPINE', 'leaf_to_spine', window_us,
            output_dir, formats, dpi))
        files.extend(plot_direction_heatmap(
            plt, rows, 'SPINE', 'LEAF', 'spine_to_leaf', window_us,
            output_dir, formats, dpi))
        if not reference_top:
            reference_top = top
            reference_window = window_us

    files.extend(plot_window_comparison(
        plt, summaries, reference_top, reference_window,
        output_dir, formats, dpi))
    manifest = {
        'analysis_dir': str(analysis_dir),
        'output_dir': str(output_dir),
        'plot_windows_us': list(plot_windows),
        'top_k': top_k,
        'formats': list(formats),
        'generated_files': files,
    }
    manifest_path = output_dir / 'plot_manifest.json'
    with manifest_path.open('w', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    return files


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Generate figures from analyze_switch_hotspots.py CSV outputs; '
            'raw ns-3 logs are not read again.'))
    parser.add_argument(
        'input_path', type=Path,
        help='ns-3 case directory or its output/switch_hotspots directory')
    parser.add_argument(
        '--output-dir', type=Path, default=None,
        help='figure directory (default: the switch_hotspots analysis directory)')
    parser.add_argument(
        '--plot-window-us', type=int, default=None,
        help='time window to plot (default: 1000us if available)')
    parser.add_argument(
        '--all-windows', action='store_true',
        help='generate ranking, timeline, and heatmaps for every analyzed window')
    parser.add_argument(
        '--top-k', type=int, default=10,
        help='number of directed switch pairs in ranking/timeline plots')
    parser.add_argument(
        '--formats', default='png',
        help='comma-separated output formats: png,svg,pdf (default: png)')
    parser.add_argument('--dpi', type=int, default=180, help='PNG resolution')
    parser.add_argument(
        '--max-points', type=int, default=5000,
        help='maximum displayed timeline points per switch pair; peaks are retained')
    parser.add_argument(
        '--matplotlib-path', type=Path, default=None,
        help='private package directory populated by pip install --target')
    parser.add_argument(
        '--backend', choices=('auto', 'matplotlib', 'svg'), default='auto',
        help=(
            'rendering backend: auto tries Matplotlib then falls back to '
            'dependency-free SVG (default: auto)'))
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    try:
        if args.top_k <= 0:
            raise ValueError('--top-k must be > 0')
        if args.dpi <= 0:
            raise ValueError('--dpi must be > 0')
        if args.max_points <= 0:
            raise ValueError('--max-points must be > 0')
        analysis_dir = resolve_analysis_dir(args.input_path)
        summaries = load_summaries(analysis_dir / SUMMARY_FILE)
        windows = choose_windows(
            summaries, args.plot_window_us, args.all_windows)
        output_dir = (args.output_dir.expanduser().resolve()
                      if args.output_dir is not None else analysis_dir)
        backend = args.backend
        if backend == 'svg':
            files = generate_svg_plots(
                analysis_dir=analysis_dir,
                output_dir=output_dir,
                plot_windows=windows,
                top_k=args.top_k,
                max_points=args.max_points,
            )
        else:
            formats = parse_formats(args.formats)
            try:
                files = generate_plots(
                    analysis_dir=analysis_dir,
                    output_dir=output_dir,
                    plot_windows=windows,
                    top_k=args.top_k,
                    formats=formats,
                    dpi=args.dpi,
                    max_points=args.max_points,
                    package_dir=args.matplotlib_path,
                )
                backend = 'matplotlib'
            except RuntimeError as exc:
                if backend != 'auto':
                    raise
                print(
                    '[WARN] {}\n[WARN] Falling back to dependency-free SVG.'.format(
                        exc), file=sys.stderr)
                files = generate_svg_plots(
                    analysis_dir=analysis_dir,
                    output_dir=output_dir,
                    plot_windows=windows,
                    top_k=args.top_k,
                    max_points=args.max_points,
                )
                backend = 'svg'
        print('=' * 88)
        print('Switch hotspot plot generation complete')
        print('=' * 88)
        print('CSV input       : {}'.format(analysis_dir))
        print('Figure output   : {}'.format(output_dir))
        print('Backend         : {}'.format(backend))
        print('Plot windows    : {}'.format(', '.join(
            '{}us'.format(window) for window in windows)))
        print('Generated files : {}'.format(len(files)))
        for name in files:
            print('  {}'.format(name))
        print('  plot_manifest.json')
        print('=' * 88)
        return 0
    except Exception as exc:
        print('[ERROR] {}'.format(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
