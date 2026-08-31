#!/usr/bin/env python3
"""Convert a Mooncake JSONL trace into a store-mediated P/D task DAG.

The model implemented here is intentionally explicit:

1. Cached prefix blocks are read from Storage to a Prefill (P) node.
2. Missing prefix tokens are computed layer by layer on P.
3. When layer ``i`` becomes ready, the full-prefix KV bytes belonging to that
   layer are written from P to Storage.  Layer ``i + 1`` computation does not
   wait for this network write, so computation and network transfer overlap.
4. Storage waits for every layer write, then applies a fixed commit delay.
5. Storage sends the full prefix to a Decode (D) node.
6. D decodes and writes the generated KV back to Storage.

The output is an application-level DAG.  Network tasks have zero duration;
their actual completion times are determined later by ns-3-UB.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


TASK_FIELDS = [
    'task_id', 'request_id', 'phase', 'task_class', 'task_type',
    'release_time_us', 'duration_us', 'src_node', 'src_port', 'dst_node',
    'dst_port', 'bytes', 'hash_id', 'block_index', 'block_tokens',
    'layer_index', 'layer_count', 'layer_ready_offset_us', 'p_node', 'd_node',
    'depends_on', 'comment'
]


def load_json(path):
    """Load a UTF-8 JSON configuration file."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_trace(path, max_requests=None):
    """Load Mooncake JSONL records and assign deterministic request IDs.

    Supported timestamp keys are ``timestamp`` (the original Mooncake trace)
    and ``timestamp_ms``.  Both are interpreted as milliseconds.
    """
    if max_requests is not None and max_requests < 0:
        raise ValueError('--max-requests must be >= 0')

    reqs = []
    with open(path, encoding='utf-8') as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            if max_requests is not None and len(reqs) >= max_requests:
                break
            try:
                raw = json.loads(line)
                timestamp = raw.get('timestamp_ms', raw.get('timestamp'))
                if timestamp is None:
                    raise KeyError('timestamp')
                req = {
                    'request_id': len(reqs),
                    'timestamp_ms': int(float(timestamp)),
                    'input_length': int(raw['input_length']),
                    'output_length': int(raw['output_length']),
                    'hash_ids': [int(x) for x in raw['hash_ids']],
                }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f'{path}:{line_number}: invalid trace record: {exc}') from exc
            if req['timestamp_ms'] < 0 or req['input_length'] < 0 or req['output_length'] < 0:
                raise ValueError(f'{path}:{line_number}: lengths and timestamp must be >= 0')
            reqs.append(req)

    if not reqs:
        raise ValueError(f'{path}: trace contains no requests')
    reqs.sort(key=lambda r: (r['timestamp_ms'], r['request_id']))
    return reqs


def stable_hash_int(*parts):
    """Return a stable 64-bit integer hash for placement decisions.

    Python's built-in ``hash`` is randomized between processes, so it must not
    be used when a replay needs repeatable P/D/Storage placement.
    """
    encoded = '|'.join(str(x) for x in parts).encode('utf-8')
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], 'big', signed=False)


def join_deps(task_ids):
    """Serialize an AND-dependency list for the CSV representation."""
    return ';'.join(str(x) for x in task_ids)


def effective_block_tokens(input_length, num_blocks, block_index, block_tokens):
    """Return the token count represented by one hash block.

    All blocks except the last are treated as full.  The final block receives
    the remaining tokens and is clamped to one block.  This mirrors the source
    trace convention and assumes hash count matches input length.
    """
    if num_blocks <= 0 or block_index < 0 or block_index >= num_blocks:
        raise ValueError('invalid hash block index/count')
    if block_index < num_blocks - 1:
        return block_tokens
    remaining = input_length - block_tokens * (num_blocks - 1)
    return max(0, min(block_tokens, remaining))


class Model:
    """Validated configuration and deterministic placement/timing helpers."""

    def __init__(self, cfg):
        model = cfg['model']
        timing = cfg['timing']
        compute = cfg['compute']
        storage = cfg['storage']
        delivery = cfg['kv_delivery']
        pipeline = delivery.get('layer_pipeline', {})
        placement = compute.get('p_placement', {})

        self.kv_bytes_per_token = int(model['kv_bytes_per_token'])
        self.block_tokens = int(model['block_tokens'])
        self.num_layers = int(model.get('num_layers', 78))
        self.prefill_us_per_token = float(timing['prefill_us_per_token'])
        self.decode_us_per_token = float(timing['decode_us_per_token'])
        self.storage_latency_us = int(timing['storage_latency_us'])

        self.p_nodes = [int(x) for x in compute['p_nodes']]
        self.d_nodes = [int(x) for x in compute['d_nodes']]
        self.server_ports = [int(x) for x in compute['server_ports']]
        self.p_hash_blocks = int(placement.get('hash_blocks', 2))
        self.p_exclude_hash_ids = {int(x) for x in placement.get('exclude_hash_ids', [])}
        self.storage_nodes = [int(x) for x in storage['nodes']]
        self.storage_ports = [int(x) for x in storage['ports']]

        self.rewrite_full_prefix = bool(delivery['rewrite_full_prefix'])
        self.storage_to_d_full_prefix = bool(delivery['storage_to_d_full_prefix'])
        self.layer_pipeline_enabled = bool(pipeline['enabled'])
        self.split_prefix_store_write_by_layer = bool(pipeline['split_prefix_store_write_by_layer'])
        self.storage_to_d_streaming = bool(pipeline['storage_to_d_streaming'])
        self.validate()

    def validate(self):
        if self.kv_bytes_per_token <= 0 or self.block_tokens <= 0:
            raise ValueError('model byte/token and block-token values must be > 0')
        if self.num_layers <= 0:
            raise ValueError('model.num_layers must be > 0')
        if self.prefill_us_per_token < 0 or self.decode_us_per_token < 0 or self.storage_latency_us < 0:
            raise ValueError('timing values must be >= 0')
        if not self.p_nodes or not self.d_nodes or not self.server_ports:
            raise ValueError('compute P nodes, D nodes, and server ports must be non-empty')
        if not self.storage_nodes or not self.storage_ports:
            raise ValueError('storage nodes and ports must be non-empty')
        if self.p_hash_blocks <= 0:
            raise ValueError('compute.p_placement.hash_blocks must be > 0')
        if not self.layer_pipeline_enabled:
            raise ValueError('kv_delivery.layer_pipeline.enabled must be true for v6')
        if not self.split_prefix_store_write_by_layer:
            raise ValueError('split_prefix_store_write_by_layer must be true for v6')
        if self.storage_to_d_streaming:
            raise ValueError('storage_to_d_streaming must be false for v6')
        if not self.rewrite_full_prefix:
            raise ValueError('rewrite_full_prefix must be true for this model')
        if not self.storage_to_d_full_prefix:
            raise ValueError('storage_to_d_full_prefix must be true for this model')

    def prefill_duration_us(self, miss_tokens):
        return max(0, int(round(miss_tokens * self.prefill_us_per_token)))

    def layer_compute_schedule_us(self, miss_tokens):
        """Split total Prefill time exactly across layers.

        Returns ``[(layer_duration_us, cumulative_ready_us), ...]``.
        """
        total = self.prefill_duration_us(miss_tokens)
        quotient, remainder = divmod(total, self.num_layers)
        ready = 0
        schedule = []
        for layer_index in range(self.num_layers):
            duration = quotient + (1 if layer_index < remainder else 0)
            ready += duration
            schedule.append((duration, ready))
        return schedule

    def layer_bytes(self, total_bytes, layer_index):
        """Split block KV bytes across layers without losing a byte."""
        quotient, remainder = divmod(total_bytes, self.num_layers)
        return quotient + (1 if layer_index < remainder else 0)

    def decode_duration_us(self, output_tokens):
        return max(0, int(round(output_tokens * self.decode_us_per_token)))

    def prefix_key(self, req):
        usable = [h for h in req['hash_ids'] if h not in self.p_exclude_hash_ids]
        # A request-ID fallback keeps empty/noise-only prefixes distributed.
        return tuple(usable[:self.p_hash_blocks]) or ('request', req['request_id'])

    def choose_p(self, req):
        key = self.prefix_key(req)
        node = self.p_nodes[stable_hash_int('p-node', *key) % len(self.p_nodes)]
        return node, key

    def choose_d(self, req):
        return self.d_nodes[stable_hash_int('d-node', req['request_id']) % len(self.d_nodes)]

    def choose_storage(self, tag):
        return self.storage_nodes[stable_hash_int('storage', tag) % len(self.storage_nodes)]

    def choose_storage_port(self, tag):
        return self.storage_ports[stable_hash_int('storage-port', tag) % len(self.storage_ports)]

    def choose_server_port(self, direction, request_id, tag):
        index = stable_hash_int('server-port', direction, request_id, tag) % len(self.server_ports)
        return self.server_ports[index]


def make_task(task_id, request_id, phase, task_class, task_type,
              release_time_us='', duration_us=0, src_node='', src_port='',
              dst_node='', dst_port='', num_bytes=0, hash_id='', block_index='',
              block_tokens=0, layer_index='', layer_count='',
              layer_ready_offset_us=0, p_node='', d_node='', depends_on=None,
              comment=''):
    """Create one row with a stable schema shared by all task classes."""
    return {
        'task_id': task_id, 'request_id': request_id, 'phase': phase,
        'task_class': task_class, 'task_type': task_type,
        'release_time_us': release_time_us, 'duration_us': duration_us,
        'src_node': src_node, 'src_port': src_port, 'dst_node': dst_node,
        'dst_port': dst_port, 'bytes': int(num_bytes), 'hash_id': hash_id,
        'block_index': block_index, 'block_tokens': block_tokens,
        'layer_index': layer_index, 'layer_count': layer_count,
        'layer_ready_offset_us': int(layer_ready_offset_us), 'p_node': p_node,
        'd_node': d_node, 'depends_on': join_deps(depends_on or []),
        'comment': comment
    }


def classify_prefix(req, seen_snapshot, model):
    """Classify blocks using independent hash membership.

    This is block-level reuse, not longest-contiguous-prefix semantics.  A hit
    may therefore appear after a miss when its hash already exists.
    """
    blocks = []
    cached_tokens = 0
    miss_tokens = 0
    for idx, block_hash in enumerate(req['hash_ids']):
        tokens = effective_block_tokens(
            req['input_length'], len(req['hash_ids']), idx, model.block_tokens)
        hit = block_hash in seen_snapshot
        blocks.append({'block_index': idx, 'hash_id': block_hash, 'tokens': tokens, 'hit': hit})
        if hit:
            cached_tokens += tokens
        else:
            miss_tokens += tokens
    return blocks, cached_tokens, miss_tokens


def build_formal_request_tasks(req, model, seen_snapshot, next_task_id):
    """Build the complete task DAG for one non-warmup request."""
    arrival_us = req['timestamp_ms'] * 1000
    p_node, prefix_key = model.choose_p(req)
    d_node = model.choose_d(req)
    blocks, cached_tokens, miss_tokens = classify_prefix(req, seen_snapshot, model)
    tasks = []
    read_ids = []
    full_write_ids = []
    storage_to_d_ids = []
    new_hashes = [b['hash_id'] for b in blocks if not b['hit']]

    # HIT blocks first pay fixed storage access latency, then traverse Storage -> P.
    for block in blocks:
        if not block['hit']:
            continue
        block_hash = block['hash_id']
        tokens = block['tokens']
        task_id = next_task_id
        next_task_id += 1
        read_ids.append(task_id)
        tasks.append(make_task(
            task_id, req['request_id'], 'FORMAL', 'NETWORK', 'PREFIX_READ',
            release_time_us=arrival_us + model.storage_latency_us,
            src_node=model.choose_storage(block_hash),
            src_port=model.choose_storage_port(block_hash), dst_node=p_node,
            dst_port=model.choose_server_port('prefix-read-p', req['request_id'], block_hash),
            num_bytes=tokens * model.kv_bytes_per_token, hash_id=block_hash,
            block_index=block['block_index'], block_tokens=tokens,
            p_node=p_node, d_node=d_node, depends_on=[],
            comment='HIT: Storage -> P. Prefill waits for all HIT reads.'))

    # MISS Prefill is a serial chain of per-layer compute tasks.
    layer_schedule = model.layer_compute_schedule_us(miss_tokens)
    prefill_layer_ids = []
    if miss_tokens > 0:
        previous_compute_id = None
        for layer_index, (layer_duration_us, layer_ready_us) in enumerate(layer_schedule):
            task_id = next_task_id
            next_task_id += 1
            prefill_layer_ids.append(task_id)
            if layer_index == 0:
                dependencies = list(read_ids)
                release = arrival_us if not read_ids else ''
            else:
                dependencies = [previous_compute_id]
                release = ''
            tasks.append(make_task(
                task_id, req['request_id'], 'FORMAL', 'COMPUTE', 'PREFILL_LAYER',
                release_time_us=release, duration_us=layer_duration_us,
                src_node=p_node, dst_node=p_node, block_tokens=miss_tokens,
                layer_index=layer_index, layer_count=model.num_layers,
                layer_ready_offset_us=layer_ready_us, p_node=p_node, d_node=d_node,
                depends_on=dependencies,
                comment=(f'Prefill layer {layer_index}/{model.num_layers - 1}; '
                         f'MISS tokens={miss_tokens}; cumulative ready={layer_ready_us}us.')))
            previous_compute_id = task_id

    # Each ready layer rewrites that layer's KV for every prefix block.
    # Writes of layer i may overlap computation of layer i+1.
    for block in blocks:
        block_hash = block['hash_id']
        tokens = block['tokens']
        block_total_bytes = tokens * model.kv_bytes_per_token
        storage_node = model.choose_storage(block_hash)
        storage_port = model.choose_storage_port(block_hash)
        for layer_index in range(model.num_layers):
            layer_bytes = model.layer_bytes(block_total_bytes, layer_index)
            if layer_bytes <= 0:
                continue
            if miss_tokens > 0:
                layer_ready_us = layer_schedule[layer_index][1]
                dependencies = [prefill_layer_ids[layer_index]]
            else:
                layer_ready_us = 0
                dependencies = list(read_ids)
            release = arrival_us + layer_ready_us if not read_ids else ''
            task_id = next_task_id
            next_task_id += 1
            full_write_ids.append(task_id)
            tasks.append(make_task(
                task_id, req['request_id'], 'FORMAL', 'NETWORK', 'PREFIX_STORE_WRITE',
                release_time_us=release, src_node=p_node,
                src_port=model.choose_server_port(
                    'full-prefix-write-p', req['request_id'], block_hash),
                dst_node=storage_node, dst_port=storage_port, num_bytes=layer_bytes,
                hash_id=block_hash, block_index=block['block_index'],
                block_tokens=tokens, layer_index=layer_index,
                layer_count=model.num_layers, layer_ready_offset_us=layer_ready_us,
                p_node=p_node, d_node=d_node, depends_on=dependencies,
                comment=(f'P -> Storage layer-pipelined full-prefix write; '
                         f'layer={layer_index}/{model.num_layers - 1}, '
                         f'ready_offset={layer_ready_us}us.')))

    # Storage commit is a local timer and gates the full-prefix delivery to D.
    store_commit_id = None
    if full_write_ids:
        store_commit_id = next_task_id
        next_task_id += 1
        tasks.append(make_task(
            store_commit_id, req['request_id'], 'FORMAL', 'TIMER',
            'PREFIX_STORE_COMMIT', duration_us=model.storage_latency_us,
            p_node=p_node, d_node=d_node, depends_on=full_write_ids,
            comment='All prefix writes finished; fixed Storage commit/access delay.'))

    storage_to_d_deps = [store_commit_id] if store_commit_id is not None else []
    for block in blocks:
        block_hash = block['hash_id']
        tokens = block['tokens']
        task_id = next_task_id
        next_task_id += 1
        storage_to_d_ids.append(task_id)
        tasks.append(make_task(
            task_id, req['request_id'], 'FORMAL', 'NETWORK', 'STORAGE_TO_D',
            src_node=model.choose_storage(block_hash),
            src_port=model.choose_storage_port(block_hash), dst_node=d_node,
            dst_port=model.choose_server_port('storage-to-d', req['request_id'], block_hash),
            num_bytes=tokens * model.kv_bytes_per_token, hash_id=block_hash,
            block_index=block['block_index'], block_tokens=tokens,
            p_node=p_node, d_node=d_node, depends_on=storage_to_d_deps,
            comment='Full-prefix Storage -> D after all prefix writes commit.'))

    decode_id = None
    if req['output_length'] > 0:
        decode_id = next_task_id
        next_task_id += 1
        tasks.append(make_task(
            decode_id, req['request_id'], 'FORMAL', 'COMPUTE', 'DECODE',
            duration_us=model.decode_duration_us(req['output_length']),
            src_node=d_node, dst_node=d_node, block_tokens=req['output_length'],
            p_node=p_node, d_node=d_node, depends_on=storage_to_d_ids,
            comment='Decode starts after all Storage -> D prefix flows finish.'))

        decode_tag = f"decode:{req['request_id']}"
        task_id = next_task_id
        next_task_id += 1
        tasks.append(make_task(
            task_id, req['request_id'], 'FORMAL', 'NETWORK', 'DECODE_WRITE',
            src_node=d_node,
            src_port=model.choose_server_port(
                'decode-write-d', req['request_id'], decode_tag),
            dst_node=model.choose_storage(decode_tag),
            dst_port=model.choose_storage_port(decode_tag),
            num_bytes=req['output_length'] * model.kv_bytes_per_token,
            block_tokens=req['output_length'], p_node=p_node, d_node=d_node,
            depends_on=[decode_id], comment='Decode-generated KV: D -> Storage.'))

    debug = {
        'request_id': req['request_id'], 'phase': 'FORMAL',
        'timestamp_ms': req['timestamp_ms'], 'input_length': req['input_length'],
        'output_length': req['output_length'], 'num_hash_blocks': len(req['hash_ids']),
        'p_node': p_node, 'd_node': d_node,
        'prefix_key': ','.join(str(x) for x in prefix_key),
        'prefix_hits': sum(1 for b in blocks if b['hit']),
        'prefix_misses': sum(1 for b in blocks if not b['hit']),
        'cached_prefix_tokens': cached_tokens, 'miss_prefix_tokens': miss_tokens,
        'prefill_duration_us': model.prefill_duration_us(miss_tokens),
        'num_layers': model.num_layers, 'prefill_layer_tasks': len(prefill_layer_ids),
        'prefix_read_tasks': len(read_ids), 'full_prefix_write_tasks': len(full_write_ids),
        'storage_to_d_tasks': len(storage_to_d_ids),
        'full_prefix_bytes': sum(b['tokens'] * model.kv_bytes_per_token for b in blocks),
        'read_gate_tasks': join_deps(read_ids),
        'prefill_layer_task_ids': join_deps(prefill_layer_ids),
        'store_commit_task_id': '' if store_commit_id is None else store_commit_id
    }
    return tasks, debug, new_hashes, next_task_id


def build_warmup_debug(req, model, seen_snapshot):
    """Warmup updates cache history but deliberately emits no simulation tasks."""
    p_node, prefix_key = model.choose_p(req)
    d_node = model.choose_d(req)
    blocks, cached_tokens, miss_tokens = classify_prefix(req, seen_snapshot, model)
    debug = {
        'request_id': req['request_id'], 'phase': 'WARMUP',
        'timestamp_ms': req['timestamp_ms'], 'input_length': req['input_length'],
        'output_length': req['output_length'], 'num_hash_blocks': len(req['hash_ids']),
        'p_node': p_node, 'd_node': d_node,
        'prefix_key': ','.join(str(x) for x in prefix_key),
        'prefix_hits': sum(1 for b in blocks if b['hit']),
        'prefix_misses': sum(1 for b in blocks if not b['hit']),
        'cached_prefix_tokens': cached_tokens, 'miss_prefix_tokens': miss_tokens,
        'prefill_duration_us': model.prefill_duration_us(miss_tokens),
        'num_layers': model.num_layers, 'prefill_layer_tasks': 0,
        'prefix_read_tasks': 0, 'full_prefix_write_tasks': 0,
        'storage_to_d_tasks': 0,
        'full_prefix_bytes': sum(b['tokens'] * model.kv_bytes_per_token for b in blocks),
        'read_gate_tasks': '', 'prefill_layer_task_ids': '', 'store_commit_task_id': ''
    }
    return debug, [b['hash_id'] for b in blocks if not b['hit']]


def generate_dag(reqs, model, warmup_requests=0):
    """Generate tasks using a cache snapshot shared by equal-timestamp requests."""
    if warmup_requests < 0:
        raise ValueError('--warmup-requests must be >= 0')
    if warmup_requests >= len(reqs):
        raise ValueError('--warmup-requests must be smaller than loaded requests')

    warmup_ids = {r['request_id'] for r in reqs[:warmup_requests]}
    by_timestamp = defaultdict(list)
    for req in reqs:
        by_timestamp[req['timestamp_ms']].append(req)

    seen_blocks = set()
    tasks = []
    request_debug = []
    next_task_id = 0
    for timestamp in sorted(by_timestamp):
        # Equal-time requests cannot become cache hits merely because Python
        # happened to process a peer first.
        snapshot = set(seen_blocks)
        newly_seen = []
        for req in by_timestamp[timestamp]:
            if req['request_id'] in warmup_ids:
                debug, new_hashes = build_warmup_debug(req, model, snapshot)
            else:
                req_tasks, debug, new_hashes, next_task_id = build_formal_request_tasks(
                    req, model, snapshot, next_task_id)
                tasks.extend(req_tasks)
            request_debug.append(debug)
            newly_seen.extend(new_hashes)
        seen_blocks.update(newly_seen)

    return tasks, request_debug, build_summary(tasks, request_debug, warmup_requests, model)


def build_summary(tasks, request_debug, warmup_requests, model):
    formal = [row for row in request_debug if row['phase'] == 'FORMAL']
    task_count = Counter(task['task_type'] for task in tasks)
    bytes_by_type = Counter()
    for task in tasks:
        if task['task_class'] == 'NETWORK':
            bytes_by_type[task['task_type']] += task['bytes']
    p_requests = Counter(row['p_node'] for row in formal)
    d_requests = Counter(row['d_node'] for row in formal)
    hits = sum(row['prefix_hits'] for row in formal)
    misses = sum(row['prefix_misses'] for row in formal)
    refs = hits + misses
    return {
        'warmup_requests': warmup_requests, 'formal_requests': len(formal),
        'formal_prefix_refs': refs, 'formal_hits': hits, 'formal_misses': misses,
        'formal_hit_ratio': hits / refs if refs else 0.0,
        'formal_tasks_total': len(tasks), 'task_count_by_type': dict(task_count),
        'network_bytes_by_type': dict(bytes_by_type),
        'p_requests': dict(sorted(p_requests.items())),
        'd_requests': dict(sorted(d_requests.items())),
        'p_nodes': model.p_nodes, 'd_nodes': model.d_nodes, 'pd_affinity': False,
        'kv_delivery_policy': (
            'Storage->P HIT; P Prefill MISS layer-by-layer; each ready layer '
            'immediately rewrites FULL-prefix layer KV to Storage; Storage sends '
            'FULL prefix to D after all layer writes commit')
    }


def write_csv(path, rows, fieldnames=None):
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0].keys())
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(summary):
    print('=' * 94)
    print('Mooncake trace -> Store-mediated P/D Task DAG (v6 layer-pipelined)')
    print('=' * 94)
    print(f"Warm-up requests                 : {summary['warmup_requests']:,}")
    print(f"Formal requests                  : {summary['formal_requests']:,}")
    print(f"Formal prefix refs               : {summary['formal_prefix_refs']:,}")
    print(f"Formal hits                      : {summary['formal_hits']:,}")
    print(f"Formal misses                    : {summary['formal_misses']:,}")
    print(f"Formal hit ratio                 : {summary['formal_hit_ratio'] * 100:.2f}%")
    print(f"Formal DAG tasks                 : {summary['formal_tasks_total']:,}")
    print('\nTask count by type:')
    for key, value in summary['task_count_by_type'].items():
        print(f'  {key:<24} {value:,}')
    print('\nRequests per P:')
    for node, value in summary['p_requests'].items():
        print(f'  P {node:<5} {value:,}')
    print('\nRequests per D:')
    for node, value in summary['d_requests'].items():
        print(f'  D {node:<5} {value:,}')
    print('=' * 94)


def main():
    parser = argparse.ArgumentParser(
        description='Mooncake trace -> Store-mediated P/D DAG.')
    parser.add_argument('trace', help='Mooncake JSONL trace path')
    parser.add_argument(
        '--config', default='configs/mooncake_pd_store_config_v6_layer_pipeline.json')
    parser.add_argument('--max-requests', type=int, default=None)
    parser.add_argument('--warmup-requests', type=int, default=0)
    parser.add_argument('--out-dir', default='mooncake_pd_store_dag_out')
    args = parser.parse_args()

    model = Model(load_json(args.config))
    requests = load_trace(args.trace, args.max_requests)
    tasks, request_debug, summary = generate_dag(
        requests, model, args.warmup_requests)
    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / 'task_dag.csv', tasks, TASK_FIELDS)
    write_csv(output_dir / 'requests_debug.csv', request_debug)
    with open(output_dir / 'summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print_summary(summary)
    print('\nOutput:')
    print(f"  {output_dir / 'task_dag.csv'}")
    print(f"  {output_dir / 'requests_debug.csv'}")
    print(f"  {output_dir / 'summary.json'}")


if __name__ == '__main__':
    main()
