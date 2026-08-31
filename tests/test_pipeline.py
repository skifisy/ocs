import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trace_to_dag = load_module(
    'mooncake_trace_to_dag', ROOT / 'scripts' / 'mooncake_trace_to_dag.py')
dag_to_traffic = load_module(
    'task_dag_to_ub_traffic', ROOT / 'scripts' / 'task_dag_to_ub_traffic.py')


def tiny_model():
    return trace_to_dag.Model({
        'model': {'kv_bytes_per_token': 8, 'block_tokens': 4, 'num_layers': 2},
        'timing': {
            'prefill_us_per_token': 2.0,
            'decode_us_per_token': 3.0,
            'storage_latency_us': 5,
        },
        'compute': {
            'p_nodes': [0], 'd_nodes': [1], 'server_ports': [0],
            'p_placement': {'hash_blocks': 1, 'exclude_hash_ids': []},
        },
        'storage': {'nodes': [2], 'ports': [0]},
        'kv_delivery': {
            'rewrite_full_prefix': True,
            'storage_to_d_full_prefix': True,
            'layer_pipeline': {
                'enabled': True,
                'split_prefix_store_write_by_layer': True,
                'storage_to_d_streaming': False,
            },
        },
    })


class PipelineTest(unittest.TestCase):
    def test_layer_pipeline_dag_and_phase_delays(self):
        model = tiny_model()
        requests = [
            {'request_id': 0, 'timestamp_ms': 0, 'input_length': 4,
             'output_length': 1, 'hash_ids': [10]},
            {'request_id': 1, 'timestamp_ms': 1, 'input_length': 8,
             'output_length': 2, 'hash_ids': [10, 20]},
        ]
        tasks, debug, _ = trace_to_dag.generate_dag(requests, model)

        second = [row for row in tasks if row['request_id'] == 1]
        reads = [row for row in second if row['task_type'] == 'PREFIX_READ']
        computes = [row for row in second if row['task_type'] == 'PREFILL_LAYER']
        writes = [row for row in second if row['task_type'] == 'PREFIX_STORE_WRITE']
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(computes), 2)
        self.assertEqual(len(writes), 4)  # 2 blocks x 2 layers
        self.assertEqual([row['duration_us'] for row in computes], [4, 4])
        self.assertEqual(sorted({row['layer_ready_offset_us'] for row in writes}), [4, 8])
        self.assertEqual(debug[1]['prefix_hits'], 1)
        self.assertEqual(debug[1]['prefix_misses'], 1)

        converted, next_phase, next_task, phase_debug = dag_to_traffic.convert_request(
            1, second, next_phase_id=10, next_task_id=100, priority=7,
            storage_latency_us=99)
        self.assertEqual(next_phase, 14)
        self.assertEqual(next_task, 108)  # 1 read + 4 writes + 2 S->D + 1 D->S
        self.assertEqual(phase_debug['commit_delay_us'], 5)
        self.assertEqual(phase_debug['decode_delay_us'], 6)

        write_rows = [row for row in converted if row['phaseId'] == 11]
        self.assertEqual({row['dependOnPhases'] for row in write_rows}, {'10'})
        self.assertEqual({row['delay'] for row in write_rows}, {'4us', '8us'})
        s_to_d_rows = [row for row in converted if row['phaseId'] == 12]
        self.assertEqual({row['delay'] for row in s_to_d_rows}, {'5us'})
        self.assertEqual({row['dependOnPhases'] for row in s_to_d_rows}, {'11'})

    def test_same_timestamp_requests_share_cache_snapshot(self):
        model = tiny_model()
        requests = [
            {'request_id': 0, 'timestamp_ms': 0, 'input_length': 4,
             'output_length': 0, 'hash_ids': [10]},
            {'request_id': 1, 'timestamp_ms': 0, 'input_length': 4,
             'output_length': 0, 'hash_ids': [10]},
        ]
        _, debug, _ = trace_to_dag.generate_dag(requests, model)
        self.assertEqual([row['prefix_hits'] for row in debug], [0, 0])

    def test_uint32_split(self):
        size = dag_to_traffic.UINT32_MAX * 2 + 7
        self.assertEqual(
            dag_to_traffic.split_u32(size),
            [dag_to_traffic.UINT32_MAX, dag_to_traffic.UINT32_MAX, 7])
        with self.assertRaises(ValueError):
            dag_to_traffic.split_u32(-1)

    def test_streaming_group_reader_rejects_noncontiguous_request(self):
        fields = ['task_id', 'request_id', 'task_class', 'task_type',
                  'release_time_us', 'duration_us', 'src_node', 'dst_node', 'bytes']
        rows = [
            [0, 1, 'NETWORK', 'PREFIX_READ', 0, 0, 2, 0, 1],
            [1, 2, 'NETWORK', 'PREFIX_READ', 0, 0, 2, 0, 1],
            [2, 1, 'NETWORK', 'PREFIX_READ', 0, 0, 2, 0, 1],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dag.csv'
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(fields)
                writer.writerows(rows)
            with self.assertRaises(ValueError):
                list(dag_to_traffic.iter_request_groups(path))


if __name__ == '__main__':
    unittest.main()
