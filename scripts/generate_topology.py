import csv
from collections import defaultdict
from pathlib import Path


NUM_COMPUTE_SERVERS = 16
COMPUTE_SERVER_PORTS = 64
NUM_COMPUTE_LEAF = 16
NUM_STORAGE_SERVERS = 21
STORAGE_SERVER_PORTS = 12
NUM_STORAGE_LEAF = 4
NUM_SPINE = 10
LEAF_UPLINK_PORTS = 64
LEAF_TOTAL_PORTS = 128
SPINE_TOTAL_PORTS = 128
LINK_RATE_GBPS = 400
LINK_DELAY_US = 1.0

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = PROJECT_ROOT / 'ns-3-ub' / 'scratch' / 'mooncake_pd_storage_topology'


def generate_ids():
    compute_server_start = 0
    compute_server_ids = list(range(compute_server_start, compute_server_start + NUM_COMPUTE_SERVERS))
    storage_server_start = compute_server_start + NUM_COMPUTE_SERVERS
    storage_server_ids = list(range(storage_server_start, storage_server_start + NUM_STORAGE_SERVERS))
    compute_leaf_start = storage_server_start + NUM_STORAGE_SERVERS
    compute_leaf_ids = list(range(compute_leaf_start, compute_leaf_start + NUM_COMPUTE_LEAF))
    storage_leaf_start = compute_leaf_start + NUM_COMPUTE_LEAF
    storage_leaf_ids = list(range(storage_leaf_start, storage_leaf_start + NUM_STORAGE_LEAF))
    spine_start = storage_leaf_start + NUM_STORAGE_LEAF
    spine_ids = list(range(spine_start, spine_start + NUM_SPINE))
    return compute_server_ids, storage_server_ids, compute_leaf_ids, storage_leaf_ids, spine_ids


def generate_compute_links(links, link_id, compute_server_ids, compute_leaf_ids):
    servers_per_group = 4
    leafs_per_group = 4
    links_per_visit = 2
    num_rounds = COMPUTE_SERVER_PORTS // (leafs_per_group * links_per_visit)

    if NUM_COMPUTE_SERVERS % servers_per_group != 0:
        raise ValueError('NUM_COMPUTE_SERVERS must be divisible by 4')
    if NUM_COMPUTE_LEAF % leafs_per_group != 0:
        raise ValueError('NUM_COMPUTE_LEAF must be divisible by 4')

    num_groups = NUM_COMPUTE_SERVERS // servers_per_group
    if num_groups != NUM_COMPUTE_LEAF // leafs_per_group:
        raise ValueError('Compute Server groups and Compute Leaf groups must have the same count')

    for group_index in range(num_groups):
        server_group_start = group_index * servers_per_group
        leaf_group_start = group_index * leafs_per_group
        server_group = compute_server_ids[server_group_start:server_group_start + servers_per_group]
        leaf_group = compute_leaf_ids[leaf_group_start:leaf_group_start + leafs_per_group]
        for local_server_index, server_id in enumerate(server_group):
            for round_index in range(num_rounds):
                for leaf_offset, leaf_id in enumerate(leaf_group):
                    for lane in range(links_per_visit):
                        server_port = round_index * leafs_per_group * links_per_visit + leaf_offset * links_per_visit + lane
                        leaf_port = local_server_index * (num_rounds * links_per_visit) + round_index * links_per_visit + lane
                        links.append({
                            'link_id': link_id,
                            'src': server_id,
                            'dst': leaf_id,
                            'src_port': server_port,
                            'dst_port': leaf_port,
                            'rate_gbps': LINK_RATE_GBPS,
                            'delay_us': LINK_DELAY_US,
                            'link_type': 'compute_leaf',
                            'group_index': group_index,
                        })
                        link_id += 1
    return link_id


def generate_storage_links(links, link_id, storage_server_ids, storage_leaf_ids):
    links_per_leaf = 3
    if len(storage_leaf_ids) != 4:
        raise ValueError('Storage topology requires exactly 4 Storage Leafs')

    storage_leaf_next_port = {leaf_id: 0 for leaf_id in storage_leaf_ids}
    for server_id in storage_server_ids:
        for leaf_index, leaf_id in enumerate(storage_leaf_ids):
            server_port_start = leaf_index * links_per_leaf
            for offset in range(links_per_leaf):
                server_port = server_port_start + offset
                leaf_port = storage_leaf_next_port[leaf_id]
                if leaf_port >= 63:
                    raise RuntimeError(f'Storage Leaf {leaf_id} exceeds 63 storage ports')
                links.append({
                    'link_id': link_id,
                    'src': server_id,
                    'dst': leaf_id,
                    'src_port': server_port,
                    'dst_port': leaf_port,
                    'rate_gbps': LINK_RATE_GBPS,
                    'delay_us': LINK_DELAY_US,
                    'link_type': 'storage_leaf',
                })
                storage_leaf_next_port[leaf_id] += 1
                link_id += 1

    print()
    print('Storage Leaf downlink distribution:')
    for index, leaf_id in enumerate(storage_leaf_ids):
        print(f'  Storage Leaf {index + 1} (node {leaf_id}): {storage_leaf_next_port[leaf_id]} ports used')
        if storage_leaf_next_port[leaf_id] != 63:
            raise RuntimeError(f'Storage Leaf {leaf_id}: expected 63 ports, got {storage_leaf_next_port[leaf_id]}')
    return link_id


def generate_leaf_spine_links(links, link_id, leaf_ids, spine_ids):
    num_leaf = len(leaf_ids)
    num_spine = len(spine_ids)
    if num_leaf != 20:
        raise ValueError(f'Expected 20 Leafs, got {num_leaf}')
    if num_spine != 10:
        raise ValueError(f'Expected 10 Spines, got {num_spine}')

    leaf_next_port = {leaf_id: 64 for leaf_id in leaf_ids}
    spine_base_next_port = {spine_id: 0 for spine_id in spine_ids}
    spine_extra_next_port = {spine_id: 120 for spine_id in spine_ids}
    base_links_per_spine = 6
    extra_links_per_leaf = 4

    for spine_id in spine_ids:
        for leaf_id in leaf_ids:
            for parallel_index in range(base_links_per_spine):
                leaf_port = leaf_next_port[leaf_id]
                spine_port = spine_base_next_port[spine_id]
                links.append({
                    'link_id': link_id,
                    'src': leaf_id,
                    'dst': spine_id,
                    'src_port': leaf_port,
                    'dst_port': spine_port,
                    'rate_gbps': LINK_RATE_GBPS,
                    'delay_us': LINK_DELAY_US,
                    'link_type': 'leaf_spine',
                    'parallel_index': parallel_index,
                })
                leaf_next_port[leaf_id] += 1
                spine_base_next_port[spine_id] += 1
                link_id += 1

    for spine_id in spine_ids:
        if spine_base_next_port[spine_id] != 120:
            raise RuntimeError(f'Spine {spine_id} base ports used {spine_base_next_port[spine_id]}, expected 120')
    for leaf_id in leaf_ids:
        used = leaf_next_port[leaf_id] - 64
        if used != 60:
            raise RuntimeError(f'Leaf {leaf_id} base uplinks {used}, expected 60')

    for leaf_index, leaf_id in enumerate(leaf_ids):
        extra_start = leaf_index * extra_links_per_leaf % num_spine
        for extra_index in range(extra_links_per_leaf):
            spine_index = (extra_start + extra_index) % num_spine
            spine_id = spine_ids[spine_index]
            leaf_port = leaf_next_port[leaf_id]
            spine_port = spine_extra_next_port[spine_id]
            links.append({
                'link_id': link_id,
                'src': leaf_id,
                'dst': spine_id,
                'src_port': leaf_port,
                'dst_port': spine_port,
                'rate_gbps': LINK_RATE_GBPS,
                'delay_us': LINK_DELAY_US,
                'link_type': 'leaf_spine_extra',
            })
            leaf_next_port[leaf_id] += 1
            spine_extra_next_port[spine_id] += 1
            link_id += 1

    print()
    print('Leaf-Spine port validation')
    print('-' * 50)
    for leaf_id in leaf_ids:
        used = leaf_next_port[leaf_id] - 64
        if used != 64:
            raise RuntimeError(f'Leaf {leaf_id}: {used}/64 uplinks')
        print(f'Leaf {leaf_id}: {used}/64 uplinks')
    for spine_id in spine_ids:
        base_used = spine_base_next_port[spine_id]
        extra_used = spine_extra_next_port[spine_id] - 120
        total_used = base_used + extra_used
        if total_used != 128:
            raise RuntimeError(f'Spine {spine_id}: {total_used}/128 ports')
        print(f'Spine {spine_id}: {total_used}/128 ports')
    print('Leaf-Spine validation PASSED')
    return link_id


def validate_topology(links, compute_server_ids, storage_server_ids, compute_leaf_ids, storage_leaf_ids, spine_ids):
    print()
    print('=' * 70)
    print('Complete topology validation')
    print('=' * 70)
    compute_count = sum(1 for link in links if link['link_type'] == 'compute_leaf')
    storage_count = sum(1 for link in links if link['link_type'] == 'storage_leaf')
    leaf_spine_count = sum(1 for link in links if link['link_type'] in ('leaf_spine', 'leaf_spine_extra'))
    expected_compute = NUM_COMPUTE_SERVERS * COMPUTE_SERVER_PORTS
    expected_storage = NUM_STORAGE_SERVERS * STORAGE_SERVER_PORTS
    expected_leaf_spine = (NUM_COMPUTE_LEAF + NUM_STORAGE_LEAF) * LEAF_UPLINK_PORTS
    expected_total = expected_compute + expected_storage + expected_leaf_spine
    print(f'Compute Server-Leaf : {compute_count}')
    print(f'Storage Server-Leaf : {storage_count}')
    print(f'Leaf-Spine          : {leaf_spine_count}')
    print(f'Total links         : {len(links)}')
    if compute_count != expected_compute:
        raise RuntimeError(f'Expected {expected_compute} compute links, got {compute_count}')
    if storage_count != expected_storage:
        raise RuntimeError(f'Expected {expected_storage} storage links, got {storage_count}')
    if leaf_spine_count != expected_leaf_spine:
        raise RuntimeError(f'Expected {expected_leaf_spine} Leaf-Spine links, got {leaf_spine_count}')
    if len(links) != expected_total:
        raise RuntimeError(f'Expected {expected_total} total links, got {len(links)}')

    port_limits = {}
    for node_id in compute_server_ids:
        port_limits[node_id] = COMPUTE_SERVER_PORTS
    for node_id in storage_server_ids:
        port_limits[node_id] = STORAGE_SERVER_PORTS
    for node_id in compute_leaf_ids:
        port_limits[node_id] = LEAF_TOTAL_PORTS
    for node_id in storage_leaf_ids:
        port_limits[node_id] = LEAF_TOTAL_PORTS
    for node_id in spine_ids:
        port_limits[node_id] = SPINE_TOTAL_PORTS

    used_ports = defaultdict(set)
    for link in links:
        endpoints = ((link['src'], link['src_port']), (link['dst'], link['dst_port']))
        for node_id, port_id in endpoints:
            if node_id not in port_limits:
                raise RuntimeError(f'Unknown node ID {node_id}')
            port_num = port_limits[node_id]
            if not 0 <= port_id < port_num:
                raise RuntimeError(f'Port out of range: node={node_id}, port={port_id}, portNum={port_num}')
            if port_id in used_ports[node_id]:
                raise RuntimeError(f'Physical port reused: node={node_id}, port={port_id}')
            used_ports[node_id].add(port_id)

    for server_id in compute_server_ids:
        used = len(used_ports[server_id])
        if used != 64:
            raise RuntimeError(f'Compute Server {server_id}: {used}/64 ports used')
    for server_id in storage_server_ids:
        used = len(used_ports[server_id])
        if used != 12:
            raise RuntimeError(f'Storage Server {server_id}: {used}/12 ports used')
    for leaf_id in compute_leaf_ids:
        used = len(used_ports[leaf_id])
        if used != 128:
            raise RuntimeError(f'Compute Leaf {leaf_id}: {used}/128 ports used')
    for leaf_id in storage_leaf_ids:
        used = len(used_ports[leaf_id])
        if used != 127:
            raise RuntimeError(f'Storage Leaf {leaf_id}: {used} ports used; expected 127')
    for spine_id in spine_ids:
        used = len(used_ports[spine_id])
        if used != 128:
            raise RuntimeError(f'Spine {spine_id}: {used}/128 ports used')

    print()
    print('Port-range validation PASSED')
    print('Port-uniqueness validation PASSED')
    print('Topology validation PASSED')
    print('=' * 70)


def generate_node_csv(path, compute_server_ids, storage_server_ids, compute_leaf_ids, storage_leaf_ids, spine_ids):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['nodeId', 'nodeType', 'portNum', 'allocationDelay', 'forwardDelay'])
        writer.writerow([f'{compute_server_ids[0]}..{compute_server_ids[-1]}', 'DEVICE', COMPUTE_SERVER_PORTS, '1ns', ''])
        writer.writerow([f'{storage_server_ids[0]}..{storage_server_ids[-1]}', 'DEVICE', STORAGE_SERVER_PORTS, '1ns', ''])
        writer.writerow([f'{compute_leaf_ids[0]}..{compute_leaf_ids[-1]}', 'SWITCH', LEAF_TOTAL_PORTS, '1ns', ''])
        writer.writerow([f'{storage_leaf_ids[0]}..{storage_leaf_ids[-1]}', 'SWITCH', LEAF_TOTAL_PORTS, '1ns', ''])
        writer.writerow([f'{spine_ids[0]}..{spine_ids[-1]}', 'SWITCH', SPINE_TOTAL_PORTS, '1ns', ''])


def generate_topology_csv(links, path):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['nodeId1', 'portId1', 'nodeId2', 'portId2', 'bandwidth', 'delay'])
        for link in links:
            writer.writerow([
                link['src'],
                link['src_port'],
                link['dst'],
                link['dst_port'],
                f"{link['rate_gbps']}Gbps",
                f"{link['delay_us']:g}us",
            ])


def main():
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    compute_server_ids, storage_server_ids, compute_leaf_ids, storage_leaf_ids, spine_ids = generate_ids()

    print('=' * 70)
    print('Node allocation')
    print('=' * 70)
    print(f'Compute Servers : {compute_server_ids[0]}..{compute_server_ids[-1]}')
    print(f'Storage Servers : {storage_server_ids[0]}..{storage_server_ids[-1]}')
    print(f'Compute Leafs   : {compute_leaf_ids[0]}..{compute_leaf_ids[-1]}')
    print(f'Storage Leafs   : {storage_leaf_ids[0]}..{storage_leaf_ids[-1]}')
    print(f'Spines          : {spine_ids[0]}..{spine_ids[-1]}')

    links = []
    link_id = 0
    link_id = generate_compute_links(links, link_id, compute_server_ids, compute_leaf_ids)
    link_id = generate_storage_links(links, link_id, storage_server_ids, storage_leaf_ids)
    all_leaf_ids = compute_leaf_ids + storage_leaf_ids
    link_id = generate_leaf_spine_links(links, link_id, all_leaf_ids, spine_ids)
    validate_topology(links, compute_server_ids, storage_server_ids, compute_leaf_ids, storage_leaf_ids, spine_ids)

    node_file = CASE_DIR / 'node.csv'
    topology_file = CASE_DIR / 'topology.csv'
    generate_node_csv(node_file, compute_server_ids, storage_server_ids, compute_leaf_ids, storage_leaf_ids, spine_ids)
    generate_topology_csv(links, topology_file)

    print()
    print('=' * 70)
    print('Generated files')
    print('=' * 70)
    print(node_file)
    print(topology_file)
    print()
    print(f'Total physical links: {len(links)}')
    print(f'Next link_id: {link_id}')


if __name__ == '__main__':
    main()
