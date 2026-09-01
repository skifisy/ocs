from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_CASE_DIR = Path('~/pd-ns3-ub/ns-3-ub/scratch/mooncake_pd_storage_topology').expanduser()


@dataclass(frozen=True)
class Node:
    node_id: int
    node_type: str
    port_num: int


@dataclass(frozen=True)
class Edge:
    neighbor: int
    local_port: int
    remote_port: int


@dataclass(frozen=True)
class RouteEntry:
    node_id: int
    dst_node_id: int
    dst_port_id: int
    out_ports: Tuple[int, ...]
    metrics: Tuple[int, ...]
    next_hops: Tuple[int, ...]


def parse_node_range(text: str) -> List[int]:
    text = text.strip()
    if '..' not in text:
        return [int(text)]
    left, right = text.split('..', 1)
    start = int(left)
    end = int(right)
    if end < start:
        raise ValueError(f'Invalid node range: {text}')
    return list(range(start, end + 1))


def read_nodes(path: Path) -> Dict[int, Node]:
    if not path.exists():
        raise FileNotFoundError(f'node.csv not found: {path}')
    nodes: Dict[int, Node] = {}
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'nodeId', 'nodeType', 'portNum'}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path} must contain columns {sorted(required)}; got {reader.fieldnames}')
        for row in reader:
            ids = parse_node_range(row['nodeId'])
            node_type = row['nodeType'].strip().upper()
            port_num = int(row['portNum'])
            if port_num <= 0:
                raise ValueError(f"Node range {row['nodeId']} has invalid portNum={port_num}")
            for node_id in ids:
                if node_id in nodes:
                    raise ValueError(f'Duplicate node ID {node_id} in {path}')
                nodes[node_id] = Node(node_id=node_id, node_type=node_type, port_num=port_num)
    if not nodes:
        raise ValueError(f'No nodes found in {path}')
    return nodes


def read_topology(path: Path, nodes: Dict[int, Node]) -> Dict[int, List[Edge]]:
    if not path.exists():
        raise FileNotFoundError(f'topology.csv not found: {path}')
    graph: Dict[int, List[Edge]] = defaultdict(list)
    used_ports = set()
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'nodeId1', 'portId1', 'nodeId2', 'portId2'}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path} must contain columns {sorted(required)}; got {reader.fieldnames}')
        for line_no, row in enumerate(reader, start=2):
            a = int(row['nodeId1'])
            pa = int(row['portId1'])
            b = int(row['nodeId2'])
            pb = int(row['portId2'])
            if a not in nodes or b not in nodes:
                raise ValueError(f'{path}:{line_no}: undeclared node in link {a}<->{b}')
            if not 0 <= pa < nodes[a].port_num:
                raise ValueError(f'{path}:{line_no}: node {a} port {pa} outside 0..{nodes[a].port_num - 1}')
            if not 0 <= pb < nodes[b].port_num:
                raise ValueError(f'{path}:{line_no}: node {b} port {pb} outside 0..{nodes[b].port_num - 1}')
            for endpoint in ((a, pa), (b, pb)):
                if endpoint in used_ports:
                    raise ValueError(f'{path}:{line_no}: physical port reused: node {endpoint[0]} port {endpoint[1]}')
                used_ports.add(endpoint)
            graph[a].append(Edge(neighbor=b, local_port=pa, remote_port=pb))
            graph[b].append(Edge(neighbor=a, local_port=pb, remote_port=pa))

    for node_id in graph:
        graph[node_id].sort(key=lambda e: (e.neighbor, e.local_port, e.remote_port))
    isolated = sorted(node_id for node_id in nodes if not graph[node_id])
    if isolated:
        raise ValueError(f'Isolated nodes in topology: {isolated}')
    return graph


def switch_ids(nodes: Dict[int, Node]) -> List[int]:
    return sorted(node_id for node_id, node in nodes.items() if node.node_type == 'SWITCH')


def device_ids(nodes: Dict[int, Node]) -> List[int]:
    return sorted(node_id for node_id, node in nodes.items() if node.node_type == 'DEVICE')


def get_edge_for_local_port(graph: Dict[int, List[Edge]], node_id: int, port_id: int) -> Edge:
    matches = [edge for edge in graph[node_id] if edge.local_port == port_id]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one link on node {node_id} port {port_id}, found {len(matches)}')
    return matches[0]


def validate_device_links_to_switches(nodes: Dict[int, Node], graph: Dict[int, List[Edge]]) -> None:
    bad_links = []
    for dev_id in device_ids(nodes):
        for edge in graph[dev_id]:
            neighbor_type = nodes[edge.neighbor].node_type
            if neighbor_type != 'SWITCH':
                bad_links.append((dev_id, edge.local_port, edge.neighbor, neighbor_type))
    if bad_links:
        preview = bad_links[:10]
        raise ValueError(
            'DEVICE-to-non-SWITCH links found. This generator assumes DEVICE nodes are endpoint-only.\n'
            f'Examples: {preview}'
        )


def bfs_switch_distances(
    target_switch: int,
    nodes: Dict[int, Node],
    graph: Dict[int, List[Edge]],
) -> Dict[int, int]:
    if nodes[target_switch].node_type != 'SWITCH':
        raise ValueError(f'Target {target_switch} is not a SWITCH')
    dist = {target_switch: 0}
    q = deque([target_switch])
    while q:
        current = q.popleft()
        candidate_distance = dist[current] + 1
        for edge in graph[current]:
            neighbor = edge.neighbor
            if nodes[neighbor].node_type != 'SWITCH':
                continue
            if neighbor not in dist:
                dist[neighbor] = candidate_distance
                q.append(neighbor)
    return dist


def destination_ports(
    nodes: Dict[int, Node],
    graph: Dict[int, List[Edge]],
    destination_kind: str,
) -> Iterable[Tuple[int, int]]:
    if destination_kind == 'devices':
        ids = device_ids(nodes)
    else:
        ids = sorted(nodes)
    for node_id in ids:
        linked_ports = {edge.local_port for edge in graph[node_id]}
        for port_id in range(nodes[node_id].port_num):
            if port_id in linked_ports:
                yield node_id, port_id


def build_routes(
    nodes: Dict[int, Node],
    graph: Dict[int, List[Edge]],
    destination_kind: str = 'devices',
) -> List[RouteEntry]:
    validate_device_links_to_switches(nodes, graph)
    all_switches = switch_ids(nodes)
    all_devices = device_ids(nodes)
    switch_distance_cache: Dict[int, Dict[int, int]] = {}
    entries: List[RouteEntry] = []

    for dst_node, dst_port in destination_ports(nodes, graph, destination_kind):
        dst_type = nodes[dst_node].node_type
        if dst_type != 'DEVICE':
            raise ValueError(
                '--destinations=all is not supported safely yet for SWITCH destination ports. '
                'Use the default --destinations=devices.'
            )

        dst_edge = get_edge_for_local_port(graph, dst_node, dst_port)
        target_switch = dst_edge.neighbor
        if nodes[target_switch].node_type != 'SWITCH':
            raise ValueError(f'Destination DEVICE {dst_node} port {dst_port} is not attached to a SWITCH')
        target_switch_port = dst_edge.remote_port

        if target_switch not in switch_distance_cache:
            switch_distance_cache[target_switch] = bfs_switch_distances(
                target_switch=target_switch,
                nodes=nodes,
                graph=graph,
            )
        dist_to_target_switch = switch_distance_cache[target_switch]

        for current in all_switches:
            if current == target_switch:
                entries.append(RouteEntry(
                    node_id=current,
                    dst_node_id=dst_node,
                    dst_port_id=dst_port,
                    out_ports=(target_switch_port,),
                    metrics=(1,),
                    next_hops=(dst_node,),
                ))
                continue

            if current not in dist_to_target_switch:
                raise ValueError(
                    f'Switch {current} cannot reach destination attachment switch {target_switch} '
                    f'for {dst_node}:{dst_port}'
                )

            current_switch_distance = dist_to_target_switch[current]
            candidates: List[Edge] = []
            for edge in graph[current]:
                neighbor = edge.neighbor
                if nodes[neighbor].node_type != 'SWITCH':
                    continue
                neighbor_distance = dist_to_target_switch.get(neighbor)
                if neighbor_distance == current_switch_distance - 1:
                    candidates.append(edge)
            if not candidates:
                raise RuntimeError(
                    f'No shortest-path switch output for current={current}, '
                    f'destination={dst_node}:{dst_port}'
                )

            metric = current_switch_distance + 1
            candidates.sort(key=lambda e: (e.local_port, e.neighbor, e.remote_port))
            entries.append(RouteEntry(
                node_id=current,
                dst_node_id=dst_node,
                dst_port_id=dst_port,
                out_ports=tuple(edge.local_port for edge in candidates),
                metrics=tuple(metric for _ in candidates),
                next_hops=tuple(edge.neighbor for edge in candidates),
            ))

        for current in all_devices:
            if current == dst_node:
                continue
            source_candidates: List[Tuple[int, int, int]] = []
            for edge in graph[current]:
                neighbor_switch = edge.neighbor
                if nodes[neighbor_switch].node_type != 'SWITCH':
                    continue
                switch_distance = dist_to_target_switch.get(neighbor_switch)
                if switch_distance is None:
                    continue
                total_metric = 1 + switch_distance + 1
                source_candidates.append((total_metric, edge.local_port, neighbor_switch))
            if not source_candidates:
                raise RuntimeError(f'DEVICE {current} has no route to {dst_node}:{dst_port}')

            best_metric = min(item[0] for item in source_candidates)
            best = sorted(
                (item for item in source_candidates if item[0] == best_metric),
                key=lambda item: (item[1], item[2]),
            )
            entries.append(RouteEntry(
                node_id=current,
                dst_node_id=dst_node,
                dst_port_id=dst_port,
                out_ports=tuple(item[1] for item in best),
                metrics=tuple(best_metric for _ in best),
                next_hops=tuple(item[2] for item in best),
            ))

    entries.sort(key=lambda route: (route.node_id, route.dst_node_id, route.dst_port_id))
    return entries


def join_ints(values: Sequence[int]) -> str:
    return ' '.join(str(value) for value in values)


def write_routing_table(path: Path, entries: Sequence[RouteEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['nodeId', 'dstNodeId', 'dstPortId', 'outPorts', 'metrics'])
        for entry in entries:
            if len(entry.out_ports) != len(entry.metrics):
                raise RuntimeError(f'outPorts/metrics length mismatch in {entry}')
            if not entry.out_ports:
                raise RuntimeError(f'Empty outPorts in {entry}')
            writer.writerow([
                entry.node_id,
                entry.dst_node_id,
                entry.dst_port_id,
                join_ints(entry.out_ports),
                join_ints(entry.metrics),
            ])


def write_debug_plan(path: Path, entries: Sequence[RouteEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['nodeId', 'dstNodeId', 'dstPortId', 'outPorts', 'nextHops', 'metrics'])
        for entry in entries:
            writer.writerow([
                entry.node_id,
                entry.dst_node_id,
                entry.dst_port_id,
                join_ints(entry.out_ports),
                join_ints(entry.next_hops),
                join_ints(entry.metrics),
            ])


def physical_link_count(graph: Dict[int, List[Edge]]) -> int:
    return sum(len(edges) for edges in graph.values()) // 2


def destination_endpoint_count(nodes: Dict[int, Node], graph: Dict[int, List[Edge]]) -> int:
    return sum(1 for _ in destination_ports(nodes, graph, 'devices'))


def find_route(
    entries: Sequence[RouteEntry],
    node_id: int,
    dst_node_id: int,
    dst_port_id: int,
) -> Optional[RouteEntry]:
    for entry in entries:
        if (
            entry.node_id == node_id
            and entry.dst_node_id == dst_node_id
            and entry.dst_port_id == dst_port_id
        ):
            return entry
    return None


def print_route_example(
    entries: Sequence[RouteEntry],
    node_id: int,
    dst_node_id: int,
    dst_port_id: int,
) -> None:
    entry = find_route(entries, node_id, dst_node_id, dst_port_id)
    if entry is None:
        return
    print(f'Example route: {node_id} -> {dst_node_id}:port{dst_port_id}')
    print(f'  outPorts : {join_ints(entry.out_ports)}')
    print(f'  nextHops : {join_ints(entry.next_hops)}')
    print(f'  metrics  : {join_ints(entry.metrics)}')
    print()


def validate_entries(
    entries: Sequence[RouteEntry],
    nodes: Dict[int, Node],
    graph: Dict[int, List[Edge]],
) -> None:
    seen_keys = set()
    for entry in entries:
        key = (entry.node_id, entry.dst_node_id, entry.dst_port_id)
        if key in seen_keys:
            raise RuntimeError(f'Duplicate routing key: {key}')
        seen_keys.add(key)
        if len(entry.out_ports) != len(entry.metrics):
            raise RuntimeError(f'outPorts/metrics mismatch: {key}')
        if len(entry.out_ports) != len(entry.next_hops):
            raise RuntimeError(f'outPorts/nextHops mismatch: {key}')
        if len(set(entry.out_ports)) != len(entry.out_ports):
            raise RuntimeError(f'Duplicate outPort in route {key}: {entry.out_ports}')

        edge_by_port = {edge.local_port: edge for edge in graph[entry.node_id]}
        for out_port, next_hop in zip(entry.out_ports, entry.next_hops):
            edge = edge_by_port.get(out_port)
            if edge is None:
                raise RuntimeError(f'Route {key} uses nonexistent outPort {out_port} on node {entry.node_id}')
            if edge.neighbor != next_hop:
                raise RuntimeError(
                    f'Route {key}: outPort {out_port} points to {edge.neighbor}, expected nextHop {next_hop}'
                )

        if nodes[entry.node_id].node_type == 'SWITCH':
            for next_hop in entry.next_hops:
                if nodes[next_hop].node_type == 'DEVICE':
                    if not (next_hop == entry.dst_node_id and entry.metrics[0] == 1):
                        raise RuntimeError(
                            f'Invalid transit route {key}: SWITCH {entry.node_id} sends toward DEVICE {next_hop}'
                        )


def summarize(
    nodes: Dict[int, Node],
    graph: Dict[int, List[Edge]],
    entries: Sequence[RouteEntry],
) -> None:
    devs = device_ids(nodes)
    switches = switch_ids(nodes)
    print('=' * 72)
    print('ns-3-UB routing_table generation summary')
    print('=' * 72)
    print(f'Nodes                    : {len(nodes)}')
    print(f'  DEVICE                 : {len(devs)}')
    print(f'  SWITCH                 : {len(switches)}')
    print(f'Physical links           : {physical_link_count(graph)}')
    print(f'DEVICE destination ports : {destination_endpoint_count(nodes, graph)}')
    print(f'Aggregated route rows    : {len(entries)}')
    print()
    print_route_example(entries, 0, 16, 0)
    print_route_example(entries, 37, 16, 0)
    print_route_example(entries, 0, 16, 3)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Generate ns-3-UB shortest-path/ECMP routing_table.csv from node.csv + topology.csv.'
    )
    parser.add_argument(
        '--case-dir',
        type=Path,
        default=DEFAULT_CASE_DIR,
        help=f'ns-3-UB case directory (default: {DEFAULT_CASE_DIR})',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='routing_table.csv path (default: <case-dir>/routing_table.csv)',
    )
    parser.add_argument(
        '--debug-plan',
        type=Path,
        default=None,
        help='optionally write a readable routing_debug.csv including next-hop node IDs',
    )
    parser.add_argument(
        '--destinations',
        choices=('devices', 'all'),
        default='devices',
        help="destination type. Use default 'devices'. 'all' is reserved and currently rejected for SWITCH ports.",
    )
    parser.add_argument(
        '--plan-only',
        action='store_true',
        help='compute, validate, and print examples without writing routing_table.csv',
    )
    args = parser.parse_args()

    case_dir = args.case_dir.expanduser().resolve()
    node_path = case_dir / 'node.csv'
    topology_path = case_dir / 'topology.csv'
    output_path = args.output.expanduser().resolve() if args.output else case_dir / 'routing_table.csv'

    try:
        nodes = read_nodes(node_path)
        graph = read_topology(topology_path, nodes)
        entries = build_routes(nodes=nodes, graph=graph, destination_kind=args.destinations)
        validate_entries(entries, nodes, graph)
        summarize(nodes, graph, entries)

        if args.debug_plan is not None:
            debug_path = args.debug_plan.expanduser().resolve()
            write_debug_plan(debug_path, entries)
            print(f'[OK] Wrote debug routing plan: {debug_path}')
        if args.plan_only:
            print('[INFO] --plan-only selected; routing_table.csv not written.')
            return 0

        write_routing_table(output_path, entries)
        print(f'[OK] Wrote ns-3-UB routing table: {output_path}')
        return 0
    except Exception as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
