#!/usr/bin/env python3
"""Lower a Mooncake task DAG into native ns-3-UB traffic phases.

Only NETWORK tasks become traffic rows.  COMPUTE and TIMER tasks are folded
into ``delay`` fields while DAG task dependencies are represented by ns-3-UB
phase dependencies:

* PREFIX_READ has an absolute release delay from the trace.
* PREFIX_STORE_WRITE waits for the read phase (when present), then uses each
  layer's cumulative ready offset so Prefill and P -> Storage traffic overlap.
* STORAGE_TO_D waits for every prefix write plus the Storage commit delay.
* DECODE_WRITE waits for every Storage -> D transfer plus Decode duration.

All network tasks of one request/stage share a phase ID.  A dependent phase is
released only after every traffic task in its predecessor phase completes.
"""

import argparse
import csv
import sys
from collections import defaultdict


UINT32_MAX = (1 << 32) - 1
TYPE_TO_OP = {
    # Reads and writes are initiated by the compute endpoint against Storage.
    # ns-3-UB's LD/ST path emits a small LOAD request followed by the data
    # response, and emits STORE data followed by an ACK.
    'PREFIX_READ': 'MEM_LOAD',
    'PREFIX_STORE_WRITE': 'MEM_STORE',
    'STORAGE_TO_D': 'MEM_LOAD',
    'DECODE_WRITE': 'MEM_STORE',
}
STAGE_ORDER = [
    'PREFIX_READ', 'PREFIX_STORE_WRITE', 'STORAGE_TO_D', 'DECODE_WRITE'
]
OUTPUT_FIELDS = [
    'taskId', 'sourceNode', 'destNode', 'dataSize(Byte)', 'opType', 'priority',
    'delay', 'phaseId', 'dependOnPhases'
]


def configure_csv_field_limit():
    """Raise the CSV field limit as far as the current platform permits."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return limit
        except OverflowError:
            limit //= 10


def iter_request_groups(path):
    """Stream rows one request at a time without loading a large DAG in RAM.

    ``task_dag.csv`` must be grouped by ``request_id``.  Reappearance of an
    already completed request is rejected because phase allocation relies on
    request-local contiguous groups.
    """
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError('task_dag.csv is empty or missing a header')
        required = {
            'task_id', 'request_id', 'task_class', 'task_type',
            'release_time_us', 'duration_us', 'src_node', 'dst_node', 'bytes'
        }
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f'task_dag.csv missing columns: {sorted(missing)}')

        current_request = None
        current_rows = []
        finished = set()
        for row in reader:
            request_id = int(row['request_id'])
            if current_request is None:
                current_request = request_id
            if request_id != current_request:
                finished.add(current_request)
                yield current_request, current_rows
                if request_id in finished:
                    raise ValueError(
                        'task_dag.csv is not grouped by request_id; '
                        f'request {request_id} appears in multiple regions')
                current_request = request_id
                current_rows = []
            current_rows.append(row)
        if current_request is not None:
            yield current_request, current_rows


def intval(value, default=0):
    """Parse an optional integer-valued CSV cell."""
    if value is None or value == '':
        return default
    return int(value)


def us_str(microseconds):
    """Format ns-3-UB's duration syntax."""
    return f'{int(microseconds)}us'


def split_u32(size_bytes):
    """Split a transfer because ns-3-UB's dataSize field is uint32_t."""
    remaining = int(size_bytes)
    if remaining < 0:
        raise ValueError(f'negative data size: {remaining}')
    chunks = []
    while remaining > UINT32_MAX:
        chunks.append(UINT32_MAX)
        remaining -= UINT32_MAX
    if remaining:
        chunks.append(remaining)
    return chunks


def tasks_of_type(rows, task_type):
    return [row for row in rows if row['task_type'] == task_type]


def one_task(rows, task_type):
    """Return a request-local COMPUTE/TIMER task that must be unique."""
    found = tasks_of_type(rows, task_type)
    if len(found) > 1:
        raise ValueError(
            f"request {rows[0]['request_id']}: expected <=1 {task_type}, "
            f'got {len(found)}')
    return found[0] if found else None


def validate_rows(rows):
    required = {
        'task_id', 'request_id', 'task_class', 'task_type', 'release_time_us',
        'duration_us', 'src_node', 'dst_node', 'bytes'
    }
    if not rows:
        raise ValueError('task_dag.csv is empty')
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f'task_dag.csv missing columns: {sorted(missing)}')


def convert_request(request_id, rows, next_phase_id, next_task_id, priority,
                    storage_latency_us):
    """Lower one request DAG into four optional network phases."""
    by_type = {stage: tasks_of_type(rows, stage) for stage in STAGE_ORDER}
    prefill_layers = sorted(
        tasks_of_type(rows, 'PREFILL_LAYER'),
        key=lambda row: intval(row.get('layer_index', 0)))
    commit = one_task(rows, 'PREFIX_STORE_COMMIT')
    decode = one_task(rows, 'DECODE')

    prefill_us = sum(intval(row['duration_us']) for row in prefill_layers)
    commit_us = intval(commit['duration_us']) if commit else 0
    decode_us = intval(decode['duration_us']) if decode else 0

    # Allocate one phase per existing network stage.  IDs are globally unique
    # even though dependencies remain request-local.
    phase = {}
    for stage in STAGE_ORDER:
        if by_type[stage]:
            phase[stage] = next_phase_id
            next_phase_id += 1

    output = []

    def emit_stage(stage, dependency_phase=None, delay_us=0,
                   preserve_release=False, per_row_delay_field=None):
        nonlocal next_task_id
        dependency = '' if dependency_phase is None else str(dependency_phase)
        for source in by_type[stage]:
            if preserve_release:
                row_delay_us = intval(source['release_time_us'])
            elif per_row_delay_field is not None:
                row_delay_us = intval(source.get(per_row_delay_field, 0))
            else:
                row_delay_us = int(delay_us)

            # A zero-byte DAG row produces no network transfer.  Large rows
            # become multiple transfers in the same phase, so the next phase
            # still waits for all chunks.
            for chunk in split_u32(intval(source['bytes'])):
                output.append({
                    'taskId': next_task_id,
                    'sourceNode': intval(source['src_node']),
                    'destNode': intval(source['dst_node']),
                    'dataSize(Byte)': chunk,
                    'opType': TYPE_TO_OP[stage],
                    'priority': priority,
                    'delay': us_str(row_delay_us),
                    'phaseId': phase[stage],
                    'dependOnPhases': dependency,
                })
                next_task_id += 1

    # Absolute time: request arrival + Storage access latency.
    if by_type['PREFIX_READ']:
        emit_stage('PREFIX_READ', preserve_release=True)

    if by_type['PREFIX_STORE_WRITE']:
        if by_type['PREFIX_READ']:
            # Relative to completion of all HIT reads.  Different layers retain
            # different offsets, preserving compute/network pipelining.
            emit_stage(
                'PREFIX_STORE_WRITE',
                dependency_phase=phase['PREFIX_READ'],
                per_row_delay_field='layer_ready_offset_us')
        else:
            # With no HIT-read gate, trace->DAG has already written the absolute
            # arrival + layer-ready time into release_time_us.
            emit_stage('PREFIX_STORE_WRITE', preserve_release=True)

    if by_type['STORAGE_TO_D']:
        if 'PREFIX_STORE_WRITE' not in phase:
            raise ValueError(
                f'request {request_id}: STORAGE_TO_D exists without '
                'PREFIX_STORE_WRITE')
        emit_stage(
            'STORAGE_TO_D', dependency_phase=phase['PREFIX_STORE_WRITE'],
            delay_us=commit_us if commit is not None else storage_latency_us)

    if by_type['DECODE_WRITE']:
        if 'STORAGE_TO_D' not in phase:
            raise ValueError(
                f'request {request_id}: DECODE_WRITE exists without STORAGE_TO_D')
        emit_stage(
            'DECODE_WRITE', dependency_phase=phase['STORAGE_TO_D'],
            delay_us=decode_us)

    debug = {
        'request_id': request_id,
        'read_phase': phase.get('PREFIX_READ', ''),
        'write_phase': phase.get('PREFIX_STORE_WRITE', ''),
        'storage_to_d_phase': phase.get('STORAGE_TO_D', ''),
        'decode_write_phase': phase.get('DECODE_WRITE', ''),
        'prefill_delay_us': prefill_us,
        'prefill_layer_count': len(prefill_layers),
        'max_layer_ready_offset_us': max(
            (intval(row.get('layer_ready_offset_us', 0))
             for row in by_type['PREFIX_STORE_WRITE']), default=0),
        'commit_delay_us': commit_us if commit is not None else storage_latency_us,
        'decode_delay_us': decode_us,
        'ub_network_tasks': len(output),
    }
    return output, next_phase_id, next_task_id, debug


def main():
    parser = argparse.ArgumentParser(
        description='Layer-pipelined Mooncake DAG -> native ns-3-UB traffic CSV.')
    parser.add_argument('task_dag', help='task_dag.csv from mooncake_trace_to_dag.py')
    parser.add_argument('--output', default='traffic.csv')
    parser.add_argument('--debug-output', default='traffic_phase_debug.csv')
    parser.add_argument('--priority', type=int, default=7)
    parser.add_argument('--start-phase-id', type=int, default=1)
    parser.add_argument('--start-task-id', type=int, default=0)
    parser.add_argument('--storage-latency-us', type=int, default=150)
    args = parser.parse_args()

    if args.start_phase_id < 0 or args.start_task_id < 0:
        raise ValueError('start IDs must be >= 0')
    if args.storage_latency_us < 0:
        raise ValueError('--storage-latency-us must be >= 0')

    csv_limit = configure_csv_field_limit()
    next_phase = args.start_phase_id
    next_task = args.start_task_id
    request_count = 0
    network_task_count = 0
    debug_file = None
    try:
        with open(args.output, 'w', newline='', encoding='utf-8') as output_file:
            output_writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
            output_writer.writeheader()
            debug_writer = None
            if args.debug_output:
                debug_file = open(args.debug_output, 'w', newline='', encoding='utf-8')

            for request_id, request_rows in iter_request_groups(args.task_dag):
                validate_rows(request_rows)
                converted, next_phase, next_task, debug = convert_request(
                    request_id, request_rows, next_phase, next_task,
                    args.priority, args.storage_latency_us)
                output_writer.writerows(converted)
                request_count += 1
                network_task_count += len(converted)
                if debug_file is not None:
                    if debug_writer is None:
                        debug_writer = csv.DictWriter(
                            debug_file, fieldnames=list(debug.keys()))
                        debug_writer.writeheader()
                    debug_writer.writerow(debug)
    finally:
        if debug_file is not None:
            debug_file.close()

    if request_count == 0:
        raise ValueError('task_dag.csv contains no task rows')

    print('=' * 88)
    print('Mooncake layer-pipelined task_dag.csv -> native ns-3-UB traffic.csv')
    print('=' * 88)
    print(f'CSV field limit    : {csv_limit:,} bytes')
    print(f'Requests converted : {request_count:,}')
    print(f'UB network tasks   : {network_task_count:,}')
    print(f'Phase IDs used     : {next_phase - args.start_phase_id:,}')
    print(f'Output             : {args.output}')
    if args.debug_output:
        print(f'Phase debug        : {args.debug_output}')
    print('=' * 88)


if __name__ == '__main__':
    main()
