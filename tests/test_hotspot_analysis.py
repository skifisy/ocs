import csv
import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hotspots = load_module(
    'analyze_switch_hotspots', ROOT / 'scripts' / 'analyze_switch_hotspots.py')
plotter = load_module(
    'plot_switch_hotspots', ROOT / 'scripts' / 'plot_switch_hotspots.py')
port_hotspots = load_module(
    'analyze_port_hotspots', ROOT / 'scripts' / 'analyze_port_hotspots.py')
port_plotter = load_module(
    'plot_port_hotspots', ROOT / 'scripts' / 'plot_port_hotspots.py')
l1_hotspots = load_module(
    'analyze_l1_pair_hotspots', ROOT / 'scripts' / 'analyze_l1_pair_hotspots.py')
l1_plotter = load_module(
    'plot_l1_pair_hotspots', ROOT / 'scripts' / 'plot_l1_pair_hotspots.py')


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class HotspotAnalysisTest(unittest.TestCase):
    def make_case(self, root):
        case = Path(root) / 'case'
        runlog = case / 'runlog'
        runlog.mkdir(parents=True)
        write_text(case / 'node.csv', '''nodeId,nodeType,portNum,allocationDelay,forwardDelay
0..1,DEVICE,1,1ns,
2..3,SWITCH,3,1ns,
''')
        write_text(case / 'topology.csv', '''nodeId1,portId1,nodeId2,portId2,bandwidth,delay
0,0,2,0,10Gbps,1us
1,0,3,0,10Gbps,1us
2,1,3,1,10Gbps,1us
2,2,3,2,10Gbps,1us
''')
        write_text(case / 'traffic.csv', '''taskId,sourceNode,destNode,dataSize(Byte),opType,priority,delay,phaseId,dependOnPhases
0,0,1,1000,URMA_WRITE,7,0us,1,
1,1,0,2000,URMA_READ,7,0us,2,
''')
        write_text(runlog / 'PortTrace_node_2_port_1.tr', '''[10us] Port Tx, port ID: 1 PacketSize: 62500
[30us] Port Rx, port ID: 1 PacketSize: 999999
[60us] Port Tx, port ID: 1 PacketSize: 62500
[110us] Port Tx, port ID: 1 PacketSize: 125000
''')
        write_text(runlog / 'PortTrace_node_2_port_2.tr', '''[20us] Port Tx, port ID: 2 PacketSize: 62500
[120us] Port Tx, port ID: 99 PacketSize: 123
[150us] Port Tx, port ID: 2 PacketSize: 125000
''')
        write_text(runlog / 'PortTrace_node_3_port_1.tr', '''[25us] Port Tx, port ID: 1 PacketSize: 25000
''')
        return case

    def test_rate_and_sparse_percentile(self):
        self.assertEqual(hotspots.parse_rate_gbps('400Gbps'), 400.0)
        self.assertEqual(hotspots.parse_rate_gbps('1.6Tbps'), 1600.0)
        self.assertAlmostEqual(hotspots.throughput_gbps(125000, 100), 10.0)
        self.assertEqual(hotspots.sparse_percentile([3.0], 100, 0.50), 0.0)
        self.assertGreater(hotspots.sparse_percentile([3.0], 100, 1.0), 0.0)

    def test_end_to_end_switch_bundle_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            output = Path(tmp) / 'analysis'
            report = hotspots.analyze_case(
                case_dir=case,
                output_dir=output,
                windows_us=[100, 1000],
                threshold_gbps=12.0,
                saturation_ratio=0.95,
                make_plot_files=False,
                progress_every=0,
            )

            self.assertEqual(report['directed_switch_bundles'], 2)
            self.assertEqual(report['directed_physical_lanes'], 4)
            self.assertEqual(report['missing_zero_traffic_trace_files'], 1)
            self.assertEqual(report['wrong_port_lines'], 1)
            self.assertEqual(report['switch_tx_events'], 6)

            with (output / 'switch_bundle_timeseries.csv').open(
                    newline='', encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            forward_100 = [
                row for row in rows
                if row['src_switch'] == '2' and row['dst_switch'] == '3'
                and row['window_us'] == '100'
            ]
            self.assertEqual(len(forward_100), 2)
            self.assertAlmostEqual(float(forward_100[0]['throughput_gbps']), 15.0)
            self.assertAlmostEqual(float(forward_100[1]['throughput_gbps']), 20.0)
            self.assertEqual(forward_100[0]['parallel_links'], '2')
            self.assertAlmostEqual(float(forward_100[0]['capacity_gbps']), 20.0)
            self.assertEqual(forward_100[0]['above_threshold'], '1')
            self.assertAlmostEqual(float(forward_100[0]['lane_skew']), 4.0 / 3.0)

            with (output / 'switch_hotspot_summary.csv').open(
                    newline='', encoding='utf-8') as f:
                summary = list(csv.DictReader(f))
            forward_summary = next(
                row for row in summary
                if row['src_switch'] == '2' and row['dst_switch'] == '3'
                and row['window_us'] == '100')
            self.assertEqual(forward_summary['hot_windows'], '2')
            self.assertAlmostEqual(float(forward_summary['hot_fraction']), 1.0)
            self.assertEqual(forward_summary['longest_hot_duration_us'], '200')
            self.assertAlmostEqual(float(forward_summary['max_gbps']), 20.0)
            self.assertAlmostEqual(float(forward_summary['max_utilization']), 1.0)

            with (output / 'traffic_endpoint_summary.csv').open(
                    newline='', encoding='utf-8') as f:
                endpoint_rows = list(csv.DictReader(f))
            self.assertEqual(len(endpoint_rows), 2)
            self.assertEqual(report['traffic_payload_bytes'], 3000)

            saved_report = json.loads(
                (output / 'analysis_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(saved_report['threshold_gbps'], 12.0)

    @unittest.skipUnless(
        importlib.util.find_spec('matplotlib') is not None,
        'matplotlib is not installed')
    def test_generate_plots_from_existing_csv_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            output = Path(tmp) / 'analysis'
            hotspots.analyze_case(
                case_dir=case,
                output_dir=output,
                windows_us=[100, 1000],
                threshold_gbps=12.0,
                make_plot_files=False,
                progress_every=0,
            )
            summaries = plotter.load_summaries(
                output / 'switch_hotspot_summary.csv')
            windows = plotter.choose_windows(summaries, 100, False)
            files = plotter.generate_plots(
                analysis_dir=output,
                output_dir=output,
                plot_windows=windows,
                top_k=2,
                formats=['png'],
                dpi=72,
                max_points=100,
            )
            self.assertIn('hotspot_topk_100us.png', files)
            self.assertIn('hotspot_timeseries_100us.png', files)
            self.assertIn('hotspot_window_comparison_100us_top.png', files)
            for filename in files:
                self.assertTrue((output / filename).is_file())
            manifest = json.loads(
                (output / 'plot_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['plot_windows_us'], [100])
            self.assertEqual(manifest['generated_files'], files)

    def test_generate_dependency_free_svg_plots(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            output = Path(tmp) / 'analysis'
            hotspots.analyze_case(
                case_dir=case,
                output_dir=output,
                windows_us=[100, 1000],
                threshold_gbps=12.0,
                make_plot_files=False,
                progress_every=0,
            )
            files = plotter.generate_svg_plots(
                analysis_dir=output,
                output_dir=output,
                plot_windows=[100],
                top_k=2,
                max_points=100,
            )
            self.assertIn('hotspot_topk_100us.svg', files)
            self.assertIn('hotspot_timeseries_100us.svg', files)
            self.assertIn('hotspot_window_comparison_100us_top.svg', files)
            self.assertIn('hotspot_plots.html', files)
            svg = (output / 'hotspot_topk_100us.svg').read_text(
                encoding='utf-8')
            self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', svg)
            self.assertIn('4×400G threshold', svg)
            manifest = json.loads(
                (output / 'plot_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['backend'], 'svg')
            self.assertEqual(manifest['generated_files'], files)

    def test_end_to_end_physical_port_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            write_text(case / 'network_attribute.txt', '''default ns3::UbTransportChannel::UsePacketSpray "false"
default ns3::UbLdstApi::UsePacketSpray "true"
global UB_RECORD_PKT_TRACE "false"
''')
            output = Path(tmp) / 'port_analysis'
            report = port_hotspots.analyze_port_hotspots(
                case_dir=case,
                output_dir=output,
                windows_us=[100, 1000],
                saturation_ratio=0.95,
                spare_ratio=0.50,
                timeseries_window_us=100,
                progress_every=0,
            )
            self.assertEqual(report['directed_physical_ports'], 4)
            self.assertFalse(report['flow_metrics_available'])
            self.assertFalse(report['network_config']['ub_record_pkt_trace'])
            self.assertTrue(report['network_config']['ldst_use_packet_spray'])

            with (output / 'physical_port_hotspot_summary.csv').open(
                    newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            port_1 = next(
                row for row in rows
                if row['window_us'] == '100'
                and row['src_switch'] == '2' and row['src_port'] == '1')
            self.assertAlmostEqual(float(port_1['p99_gbps']), 10.0)
            self.assertAlmostEqual(float(port_1['saturated_fraction']), 1.0)
            self.assertEqual(port_1['longest_saturated_duration_us'], '200')
            self.assertEqual(port_1['active_task_flow_count_p99'], '')
            self.assertEqual(port_1['flow_metrics_available'], '0')

            with (output / 'bundle_lane_balance_summary.csv').open(
                    newline='', encoding='utf-8') as stream:
                bundle_rows = list(csv.DictReader(stream))
            forward = next(
                row for row in bundle_rows
                if row['window_us'] == '100'
                and row['src_switch'] == '2' and row['dst_switch'] == '3')
            self.assertAlmostEqual(
                float(forward['potential_imbalance_fraction']), 0.5)
            self.assertEqual(forward['longest_potential_imbalance_us'], '100')

    def test_compact_task_id_port_trace_counts_active_task_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            runlog = case / 'runlog'
            write_text(runlog / 'PortTrace_node_2_port_1.tr', '''[10us] Port Tx, port ID: 1 PacketSize: 62500 TaskId: 7
[60us] Port Tx, port ID: 1 PacketSize: 62500 TaskId: 8
[110us] Port Tx, port ID: 1 PacketSize: 125000 TaskId: 7
''')
            write_text(runlog / 'PortTrace_node_2_port_2.tr', '''[20us] Port Tx, port ID: 2 PacketSize: 62500 TaskId: 7
[150us] Port Tx, port ID: 2 PacketSize: 125000 TaskId: 9
''')
            write_text(runlog / 'PortTrace_node_3_port_1.tr', '''[25us] Port Tx, port ID: 1 PacketSize: 25000 TaskId: NA
''')
            output = Path(tmp) / 'port_analysis'
            report = port_hotspots.analyze_port_hotspots(
                case_dir=case,
                output_dir=output,
                windows_us=[100],
                timeseries_window_us=100,
                progress_every=0,
            )
            self.assertTrue(report['flow_metrics_available'])
            self.assertEqual(report['task_trace_coverage'], 1.0)
            self.assertFalse(report['hash_key_metrics_available'])

            with (output / 'physical_port_hotspot_summary.csv').open(
                    newline='', encoding='utf-8') as stream:
                summaries = list(csv.DictReader(stream))
            port_1 = next(
                row for row in summaries
                if row['src_switch'] == '2' and row['src_port'] == '1')
            self.assertEqual(float(port_1['active_task_flow_count_max']), 2.0)
            self.assertEqual(port_1['flow_metrics_available'], '1')
            self.assertEqual(port_1['active_hash_key_count_p99'], '')

            with (output / 'physical_port_timeseries.csv').open(
                    newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            first = next(
                row for row in rows
                if row['src_switch'] == '2' and row['src_port'] == '1'
                and row['window_index'] == '0')
            self.assertEqual(first['active_task_flow_count'], '2')

            plot_dir = output / 'plots'
            files = port_plotter.generate_port_plots(
                output, plot_dir, requested_window_us=100,
                top_k=3, timeseries_top_k=2, max_points=100)
            self.assertIn('port_load_vs_task_flows_100us.svg', files)
            ET.parse(plot_dir / 'port_load_vs_task_flows_100us.svg')

    def test_generate_dependency_free_port_svg_plots(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            output = Path(tmp) / 'port_analysis'
            port_hotspots.analyze_port_hotspots(
                case_dir=case,
                output_dir=output,
                windows_us=[100, 1000],
                timeseries_window_us=100,
                progress_every=0,
            )
            plot_dir = output / 'plots'
            files = port_plotter.generate_port_plots(
                analysis_dir=output,
                output_dir=plot_dir,
                requested_window_us=100,
                top_k=3,
                timeseries_top_k=2,
                max_points=100,
            )
            expected = {
                'port_topk_100us.svg',
                'port_p99_heatmap_100us.svg',
                'port_saturation_heatmap_100us.svg',
                'bundle_lane_imbalance_100us.svg',
                'port_timeseries_100us.svg',
                'port_hotspot_plots.html',
            }
            self.assertTrue(expected.issubset(set(files)))
            for name in expected:
                self.assertTrue((plot_dir / name).is_file())
                if name.endswith('.svg'):
                    ET.parse(plot_dir / name)
            manifest = json.loads(
                (plot_dir / 'port_plot_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['backend'], 'svg-standard-library')
            self.assertFalse(manifest['flow_metrics_available'])

    def test_exact_l1_pair_traffic_and_task_flow_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = self.make_case(tmp)
            write_text(case / 'runlog' / 'L1PairTrace.tr', '''[10us] L1 Pair Tx, SrcL1: 2 DstL1: 3 PacketUid: 10 PacketSize: 125000 TaskId: 7
[20us] L1 Pair Tx, SrcL1: 2 DstL1: 3 PacketUid: 11 PacketSize: 125000 TaskId: 8
[110us] L1 Pair Tx, SrcL1: 2 DstL1: 3 PacketUid: 12 PacketSize: 125000 TaskId: 7
[30us] L1 Pair Tx, SrcL1: 3 DstL1: 2 PacketUid: 13 PacketSize: 62500 TaskId: 9
[130us] L1 Pair Tx, SrcL1: 3 DstL1: 2 PacketUid: 14 PacketSize: 62500 TaskId: NA
[50us] L1 Pair Tx, SrcL1: 2 DstL1: 2 PacketUid: 15 PacketSize: 1000 TaskId: 7
''')
            output = Path(tmp) / 'l1_analysis'
            report = l1_hotspots.analyze_l1_pairs(
                case_dir=case,
                output_dir=output,
                windows_us=[100, 1000],
                timeseries_window_us=100,
                timeseries_top_k=2,
            )
            self.assertEqual(report['observed_directed_l1_pairs'], 2)
            self.assertEqual(report['matched_packet_events'], 5)
            self.assertEqual(report['local_l1_events_excluded'], 1)
            self.assertEqual(report['task_tagged_packet_events'], 4)
            self.assertEqual(report['unattributed_packet_events'], 1)

            with (output / 'l1_pair_summary.csv').open(
                    newline='', encoding='utf-8') as stream:
                summaries = list(csv.DictReader(stream))
            forward = next(
                row for row in summaries
                if row['window_us'] == '100'
                and row['src_l1'] == '2' and row['dst_l1'] == '3')
            self.assertAlmostEqual(float(forward['p99_gbps']), 19.9)
            self.assertAlmostEqual(float(forward['max_gbps']), 20.0)
            self.assertEqual(forward['distinct_task_flow_count'], '2')
            self.assertAlmostEqual(
                float(forward['active_task_flow_count_max']), 2.0)
            self.assertEqual(forward['active_task_flows_at_peak'], '2')

            with (output / 'l1_pair_timeseries.csv').open(
                    newline='', encoding='utf-8') as stream:
                timeseries = list(csv.DictReader(stream))
            first = next(
                row for row in timeseries
                if row['src_l1'] == '2' and row['dst_l1'] == '3'
                and row['window_index'] == '0')
            self.assertAlmostEqual(float(first['throughput_gbps']), 20.0)
            self.assertEqual(first['active_task_flow_count'], '2')

            plot_dir = output / 'plots'
            files = l1_plotter.generate_plots(
                output, plot_dir, window_us=100,
                top_k=2, timeseries_top_k=2, max_points=100)
            expected = {
                'l1_pair_p99_traffic_heatmap_100us.svg',
                'l1_pair_p99_task_flows_heatmap_100us.svg',
                'l1_pair_topk_100us.svg',
                'l1_pair_traffic_vs_flows_100us.svg',
                'l1_pair_timeseries_100us.svg',
                'l1_pair_hotspot_plots.html',
            }
            self.assertTrue(expected.issubset(files))
            for name in expected:
                if name.endswith('.svg'):
                    ET.parse(plot_dir / name)


if __name__ == '__main__':
    unittest.main()
