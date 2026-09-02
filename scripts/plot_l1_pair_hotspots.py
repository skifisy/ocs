#!/usr/bin/env python3
"""Render L1-pair traffic and task-flow SVGs using only the standard library."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from plot_port_hotspots import (
    BG,
    BLUE,
    GRID,
    MISSING,
    MUTED,
    NAVY,
    ORANGE,
    PALE_BLUE,
    RED,
    SERIES_COLORS,
    TEXT,
    downsample_max,
    esc,
    line_element,
    mix_color,
    nice_ticks,
    rect_element,
    text_element,
    write_svg,
)


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


def pair_key(row: Mapping[str, str]) -> Tuple[int, int]:
    return integer(row, 'src_l1'), integer(row, 'dst_l1')


def pair_label(row: Mapping[str, str]) -> str:
    return 'L1 {} → {}'.format(row['src_l1'], row['dst_l1'])


def choose_window(rows: Sequence[dict], requested: int) -> int:
    available = sorted({integer(row, 'window_us') for row in rows})
    if requested not in available:
        raise ValueError(
            'plot window {}us is unavailable; choose one of {}'.format(
                requested, available))
    return requested


def plot_pair_heatmap(
    rows: Sequence[dict], path: Path, window_us: int, metric: str,
    title: str, subtitle: str, high_color: str, value_label: str,
) -> None:
    l1_nodes = sorted(
        {integer(row, 'src_l1') for row in rows}
        | {integer(row, 'dst_l1') for row in rows})
    lookup = {pair_key(row): row for row in rows}
    if not l1_nodes:
        raise ValueError('no L1 pair rows for heatmap')
    left, right, top, bottom = 112, 175, 120, 105
    cell = max(24, min(42, int(720 / len(l1_nodes))))
    width = left + right + cell * len(l1_nodes)
    height = top + bottom + cell * len(l1_nodes)
    maximum = max((number(row, metric) for row in rows), default=0.0)
    scale_max = max(maximum, 1.0)
    body: List[str] = [
        text_element(30, 38, title, 23, weight='bold'),
        text_element(30, 66, subtitle, 13, fill=MUTED),
    ]
    for column, dst_l1 in enumerate(l1_nodes):
        x = left + column * cell + cell / 2
        body.append(text_element(x, top - 12, dst_l1, 11, 'middle', MUTED))
    for row_index, src_l1 in enumerate(l1_nodes):
        y = top + row_index * cell
        body.append(text_element(
            left - 11, y + cell * 0.68, src_l1, 11, 'end', MUTED))
        for column, dst_l1 in enumerate(l1_nodes):
            x = left + column * cell
            if src_l1 == dst_l1:
                body.append(rect_element(x, y, cell - 1, cell - 1, '#f3f4f6'))
                continue
            record = lookup.get((src_l1, dst_l1))
            value = number(record, metric) if record else 0.0
            color = mix_color('#f7fbff', high_color, value / scale_max)
            tooltip = 'L1 {} → {} | {} {:.3f}'.format(
                src_l1, dst_l1, value_label, value)
            if record:
                tooltip += ' | total tasks {} | P99 traffic {:.3f} Gbps'.format(
                    integer(record, 'distinct_task_flow_count'),
                    number(record, 'p99_gbps'))
            body.append(rect_element(
                x, y, cell - 1, cell - 1, color, '#ffffff', tooltip))
            if cell >= 32 and value > 0:
                display = '{:.0f}'.format(value)
                text_color = '#ffffff' if value / scale_max > 0.58 else TEXT
                body.append(text_element(
                    x + (cell - 1) / 2, y + cell * 0.67,
                    display, 9, 'middle', text_color))
    body.append(text_element(
        left + len(l1_nodes) * cell / 2, height - 30,
        'Destination L1 switch node', 13, 'middle', TEXT, 'bold'))
    body.append(text_element(
        28, top + len(l1_nodes) * cell / 2,
        'Source L1 switch node', 13, 'middle', TEXT, 'bold', -90))
    legend_x = left + len(l1_nodes) * cell + 30
    legend_y = top
    legend_h = min(270, len(l1_nodes) * cell)
    for step in range(80):
        fraction = 1 - step / 79
        body.append(rect_element(
            legend_x, legend_y + step * legend_h / 80,
            22, legend_h / 80 + 1,
            mix_color('#f7fbff', high_color, fraction)))
    body.append(text_element(
        legend_x + 32, legend_y + 10, '{:.1f}'.format(scale_max), 11, fill=MUTED))
    body.append(text_element(
        legend_x + 32, legend_y + legend_h, '0', 11, fill=MUTED))
    write_svg(path, width, height, title, body)


def plot_top_pairs(
    rows: Sequence[dict], path: Path, window_us: int, top_k: int,
) -> None:
    ranked = sorted(rows, key=lambda row: (
        number(row, 'p99_gbps'), number(row, 'max_gbps')), reverse=True)[:top_k]
    width = 1320
    left, right, top, bottom = 215, 240, 105, 80
    row_height = 39
    height = top + bottom + row_height * len(ranked)
    plot_width = width - left - right
    maximum = max((number(row, 'max_gbps') for row in ranked), default=1.0)
    ticks = nice_ticks(maximum * 1.05, 6)
    axis_max = max(ticks)
    body: List[str] = [
        text_element(34, 40, 'Top directed L1 pairs by P99 traffic', 24, weight='bold'),
        text_element(
            34, 68,
            '{} µs windows · dark=P99 · light=maximum · flow count is distinct taskId'.format(
                window_us), 13, fill=MUTED),
    ]
    for tick in ticks:
        x = left + tick / axis_max * plot_width
        body.append(line_element(x, top - 8, x, height - bottom + 4))
        body.append(text_element(
            x, height - bottom + 27, '{:g}'.format(tick), 11, 'middle', MUTED))
    for index, row in enumerate(ranked):
        y = top + index * row_height
        p99 = number(row, 'p99_gbps')
        maximum_value = number(row, 'max_gbps')
        label = pair_label(row)
        tooltip = '{} | P99 {:.3f} Gbps | max {:.3f} Gbps | P99 tasks {:.1f}'.format(
            label, p99, maximum_value,
            number(row, 'active_task_flow_count_p99'))
        body.append(text_element(left - 12, y + 21, label, 12, 'end'))
        body.append(rect_element(
            left, y + 6, maximum_value / axis_max * plot_width, 21,
            PALE_BLUE, title=tooltip))
        body.append(rect_element(
            left, y + 10, p99 / axis_max * plot_width, 13,
            NAVY, title=tooltip))
        body.append(text_element(
            left + plot_width + 12, y + 20,
            'P99 tasks {:.0f} · max {:.0f}'.format(
                number(row, 'active_task_flow_count_p99'),
                number(row, 'active_task_flow_count_max')),
            11, fill=MUTED))
    body.append(text_element(
        left + plot_width / 2, height - 21,
        'Carried throughput (Gbps)', 13, 'middle', TEXT, 'bold'))
    write_svg(path, width, height, 'Top directed L1 pairs', body)


def plot_traffic_vs_flows(
    rows: Sequence[dict], path: Path, window_us: int, label_count: int = 12,
) -> None:
    width, height = 1180, 730
    left, right, top, bottom = 105, 285, 100, 90
    plot_width, plot_height = width - left - right, height - top - bottom
    max_flows = max((number(row, 'active_task_flow_count_p99') for row in rows), default=1.0)
    max_rate = max((number(row, 'p99_gbps') for row in rows), default=1.0)
    x_ticks = nice_ticks(max_flows * 1.05, 6)
    y_ticks = nice_ticks(max_rate * 1.05, 6)
    x_max, y_max = max(x_ticks), max(y_ticks)
    body: List[str] = [
        text_element(34, 38, 'L1-pair traffic versus simultaneous task flows', 24, weight='bold'),
        text_element(
            34, 66,
            '{} µs windows · one point per directed L1 pair'.format(window_us),
            13, fill=MUTED),
    ]
    for tick in x_ticks:
        x = left + tick / x_max * plot_width
        body.append(line_element(x, top, x, top + plot_height))
        body.append(text_element(
            x, top + plot_height + 25, '{:g}'.format(tick), 11, 'middle', MUTED))
    for tick in y_ticks:
        y = top + plot_height - tick / y_max * plot_height
        body.append(line_element(left, y, left + plot_width, y))
        body.append(text_element(
            left - 10, y + 4, '{:g}'.format(tick), 11, 'end', MUTED))
    body.append(text_element(
        left + plot_width / 2, height - 24,
        'P99 distinct active taskId count', 13, 'middle', TEXT, 'bold'))
    body.append(text_element(
        27, top + plot_height / 2,
        'P99 carried throughput (Gbps)', 13, 'middle', TEXT, 'bold', -90))
    labeled = set(pair_key(row) for row in sorted(
        rows, key=lambda row: (
            number(row, 'p99_gbps'), number(row, 'active_task_flow_count_p99')),
        reverse=True)[:label_count])
    legend_index = 0
    for row in rows:
        flows = number(row, 'active_task_flow_count_p99')
        rate = number(row, 'p99_gbps')
        x = left + flows / x_max * plot_width
        y = top + plot_height - rate / y_max * plot_height
        share = number(row, 'total_network_traffic_share')
        radius = 4 + 12 * math.sqrt(max(0.0, share))
        color = ORANGE if number(row, 'max_share_of_src_l1_traffic') >= 0.5 else NAVY
        tooltip = '{} | P99 {:.3f} Gbps | P99 tasks {:.1f} | total share {:.2%}'.format(
            pair_label(row), rate, flows, share)
        body.append(
            '<circle cx="{:.2f}" cy="{:.2f}" r="{:.2f}" fill="{}" '
            'fill-opacity="0.74" stroke="#ffffff"><title>{}</title></circle>'.format(
                x, y, radius, color, esc(tooltip)))
        if pair_key(row) in labeled:
            legend_y = top + 20 + legend_index * 27
            body.append(line_element(
                left + plot_width + 22, legend_y,
                left + plot_width + 38, legend_y, color, 3))
            body.append(text_element(
                left + plot_width + 45, legend_y + 4,
                '{} · {:.0f} tasks'.format(pair_label(row), flows), 10))
            legend_index += 1
    write_svg(path, width, height, 'L1-pair traffic versus task flows', body)


def plot_pair_timeseries(
    summary_rows: Sequence[dict], timeseries_rows: Sequence[dict], path: Path,
    window_us: int, top_k: int, max_points: int,
) -> None:
    chosen = sorted(summary_rows, key=lambda row: (
        number(row, 'p99_gbps'), number(row, 'max_gbps')), reverse=True)[
            :min(top_k, len(SERIES_COLORS))]
    chosen_keys = {pair_key(row) for row in chosen}
    relevant = [
        row for row in timeseries_rows
        if integer(row, 'window_us') == window_us and pair_key(row) in chosen_keys]
    if not relevant:
        raise ValueError(
            'l1_pair_timeseries.csv has no rows for {}us; rerun analysis with '
            '--timeseries-window-us {}'.format(window_us, window_us))
    all_indices = [integer(row, 'window_index') for row in relevant]
    first_index, last_index = min(all_indices), max(all_indices)
    indices = list(range(first_index, last_index + 1))
    rates: Dict[Tuple[int, int], Dict[int, float]] = defaultdict(dict)
    flows: Dict[Tuple[int, int], Dict[int, float]] = defaultdict(dict)
    for row in relevant:
        key = pair_key(row)
        index = integer(row, 'window_index')
        rates[key][index] = number(row, 'throughput_gbps')
        flows[key][index] = number(row, 'active_task_flow_count')

    width, height = 1380, 970
    left, right, top, bottom = 100, 320, 95, 80
    plot_width = width - left - right
    panel_height = 330
    gap = 100
    rate_top = top
    flow_top = top + panel_height + gap
    max_rate = max((number(row, 'throughput_gbps') for row in relevant), default=1.0)
    max_flow = max((number(row, 'active_task_flow_count') for row in relevant), default=1.0)
    rate_ticks = nice_ticks(max_rate * 1.05, 6)
    flow_ticks = nice_ticks(max_flow * 1.05, 6)
    rate_max, flow_max = max(rate_ticks), max(flow_ticks)
    body: List[str] = [
        text_element(34, 38, 'Top L1-pair timelines', 24, weight='bold'),
        text_element(
            34, 66,
            '{} µs windows · upper=traffic · lower=simultaneous task flows'.format(
                window_us), 13, fill=MUTED),
    ]
    for panel_top, ticks, axis_max, label in [
        (rate_top, rate_ticks, rate_max, 'Throughput (Gbps)'),
        (flow_top, flow_ticks, flow_max, 'Active task flows'),
    ]:
        for tick in ticks:
            y = panel_top + panel_height - tick / axis_max * panel_height
            body.append(line_element(left, y, left + plot_width, y))
            body.append(text_element(
                left - 10, y + 4, '{:g}'.format(tick), 11, 'end', MUTED))
        body.append(text_element(
            27, panel_top + panel_height / 2, label,
            13, 'middle', TEXT, 'bold', -90))
    duration_ms = (last_index - first_index + 1) * window_us / 1000.0
    for part in range(6):
        fraction = part / 5
        x = left + fraction * plot_width
        body.append(text_element(
            x, flow_top + panel_height + 27,
            '{:.1f}'.format(duration_ms * fraction), 11, 'middle', MUTED))
    body.append(text_element(
        left + plot_width / 2, height - 20,
        'Time from plotted interval start (ms)', 13, 'middle', TEXT, 'bold'))
    span = max(1, last_index - first_index)
    for series_index, summary in enumerate(chosen):
        key = pair_key(summary)
        color = SERIES_COLORS[series_index]
        for panel_top, source, axis_max in [
            (rate_top, rates, rate_max), (flow_top, flows, flow_max),
        ]:
            values = [source[key].get(index, 0.0) for index in indices]
            xs, ys = downsample_max(indices, values, max_points)
            points = ' '.join('{:.2f},{:.2f}'.format(
                left + (x - first_index) / span * plot_width,
                panel_top + panel_height - y / axis_max * panel_height)
                for x, y in zip(xs, ys))
            body.append(
                '<polyline points="{}" fill="none" stroke="{}" '
                'stroke-width="1.6"><title>{}</title></polyline>'.format(
                    points, color, esc(pair_label(summary))))
        legend_y = top + 25 + series_index * 42
        body.append(line_element(
            left + plot_width + 25, legend_y,
            left + plot_width + 50, legend_y, color, 3))
        body.append(text_element(
            left + plot_width + 58, legend_y + 4, pair_label(summary), 11))
        body.append(text_element(
            left + plot_width + 58, legend_y + 20,
            'P99 {:.1f}G · {:.0f} tasks'.format(
                number(summary, 'p99_gbps'),
                number(summary, 'active_task_flow_count_p99')),
            10, fill=MUTED))
    write_svg(path, width, height, 'Top L1-pair timelines', body)


def write_dashboard(
    path: Path, generated_svgs: Sequence[str], report: Mapping[str, object],
) -> None:
    cards = [
        ('L1 switches', len(report.get('l1_nodes', []))),
        ('Directed pairs', report.get('observed_directed_l1_pairs', '?')),
        ('Packet events', report.get('matched_packet_events', '?')),
        ('Task-tag coverage', '{:.1%}'.format(
            float(report.get('task_trace_coverage', 0.0)))),
    ]
    card_html = ''.join(
        '<div class="card"><b>{}</b><span>{}</span></div>'.format(
            html.escape(str(label)), html.escape(str(value)))
        for label, value in cards)
    figures = ''.join(
        '<section><object data="{}" type="image/svg+xml"></object>'
        '<p><a href="{}">Open SVG</a></p></section>'.format(
            html.escape(name), html.escape(name))
        for name in generated_svgs)
    document = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>L1 pair hotspot charts</title><style>
body {{ margin:0; padding:28px; background:#f4f7fb; color:#172033; font-family:Arial,sans-serif; }}
.cards {{ display:flex; flex-wrap:wrap; gap:12px; margin:22px 0; }}
.card {{ background:white; border:1px solid #dbe4ee; border-radius:8px; padding:14px 20px; min-width:170px; }}
.card b,.card span {{ display:block; }} .card span {{ font-size:23px; margin-top:6px; }}
section {{ background:white; border:1px solid #dbe4ee; border-radius:8px; margin:18px 0; padding:12px; overflow:auto; }}
object {{ width:100%; min-width:900px; height:760px; }} a {{ color:#075985; }}
</style></head><body><h1>ns-3-UB directed L1-pair analysis</h1>
<p>Traffic is counted once per end-to-end packet. Flow count means distinct taskId per window.</p>
<div class="cards">{cards}</div>{figures}</body></html>'''.format(
        cards=card_html, figures=figures)
    path.write_text(document, encoding='utf-8')


def generate_plots(
    analysis_dir: Path, output_dir: Path, window_us: int = 1000,
    top_k: int = 20, timeseries_top_k: int = 8, max_points: int = 2500,
) -> List[str]:
    analysis_dir = analysis_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = load_csv(analysis_dir / 'l1_pair_summary.csv')
    timeseries = load_csv(analysis_dir / 'l1_pair_timeseries.csv')
    report_path = analysis_dir / 'l1_pair_analysis.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    choose_window(summaries, window_us)
    rows = [row for row in summaries if integer(row, 'window_us') == window_us]
    generated: List[str] = []

    name = 'l1_pair_p99_traffic_heatmap_{}us.svg'.format(window_us)
    plot_pair_heatmap(
        rows, output_dir / name, window_us, 'p99_gbps',
        'Directed L1-pair P99 carried traffic',
        '{} µs windows · x=destination L1 · y=source L1'.format(window_us),
        NAVY, 'P99 Gbps')
    generated.append(name)

    name = 'l1_pair_p99_task_flows_heatmap_{}us.svg'.format(window_us)
    plot_pair_heatmap(
        rows, output_dir / name, window_us, 'active_task_flow_count_p99',
        'Directed L1-pair P99 simultaneous task flows',
        '{} µs windows · distinct taskId count · not routing hash-key count'.format(
            window_us), RED, 'P99 tasks')
    generated.append(name)

    name = 'l1_pair_topk_{}us.svg'.format(window_us)
    plot_top_pairs(rows, output_dir / name, window_us, top_k)
    generated.append(name)

    name = 'l1_pair_traffic_vs_flows_{}us.svg'.format(window_us)
    plot_traffic_vs_flows(rows, output_dir / name, window_us)
    generated.append(name)

    name = 'l1_pair_timeseries_{}us.svg'.format(window_us)
    plot_pair_timeseries(
        rows, timeseries, output_dir / name,
        window_us, timeseries_top_k, max_points)
    generated.append(name)

    dashboard_name = 'l1_pair_hotspot_plots.html'
    write_dashboard(output_dir / dashboard_name, generated, report)
    generated.append(dashboard_name)
    manifest = {
        'backend': 'svg-standard-library',
        'analysis_dir': str(analysis_dir),
        'output_dir': str(output_dir),
        'plot_window_us': window_us,
        'top_k': top_k,
        'timeseries_top_k': timeseries_top_k,
        'generated_files': generated,
    }
    (output_dir / 'l1_pair_plot_manifest.json').write_text(
        json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return generated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Render directed L1-pair SVGs without Matplotlib or Pillow.')
    parser.add_argument('analysis_dir', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--plot-window-us', type=int, default=1000)
    parser.add_argument('--top-k', type=int, default=20)
    parser.add_argument('--timeseries-top-k', type=int, default=8)
    parser.add_argument('--max-points', type=int, default=2500)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.top_k <= 0 or args.timeseries_top_k <= 0 or args.max_points < 10:
            raise ValueError('top-k values must be positive and max-points must be >= 10')
        output_dir = args.output_dir or args.analysis_dir / 'plots'
        generated = generate_plots(
            args.analysis_dir, output_dir, args.plot_window_us,
            args.top_k, args.timeseries_top_k, args.max_points)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print('[ERROR] {}'.format(exc), file=sys.stderr)
        return 2
    print('[OK] L1-pair SVGs written to {}'.format(output_dir.expanduser().resolve()))
    for name in generated:
        print('  {}'.format(name))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
