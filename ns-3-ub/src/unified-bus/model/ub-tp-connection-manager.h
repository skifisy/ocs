// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_TP_CONNECTION_MANAGER_H
#define UB_TP_CONNECTION_MANAGER_H

#include <unordered_map>
#include <vector>
#include <set>
#include <map>
#include "ub-network-address.h"
#include <mutex>
#include "ns3/log.h"

namespace utils {
/**
 * @brief tp-config.csv 中定义的数据结构
 */
struct Connection {
    uint32_t node1;
    uint32_t port1;
    uint32_t tpn1;
    uint32_t node2;
    uint32_t port2;
    uint32_t tpn2;
    uint32_t priority;
    uint32_t metrics;
};

/**
 * @brief 获取TPN规制的枚举类
 */
enum class GetTpnRuleT {
    BY_PEERNODE = 0,
    BY_PEERNODE_PRIORITY,
    BY_PEERNODE_LOCALPORT,
    BY_PEERNODE_BOTHPORTS,
    BY_ALL,
    OTHER
};

/**
 * @brief 连接索引管理器，管理所有连接
 */
class TpConnectionManager {
public:
    // 添加连接（不需要nodeId参数）
    void AddConnection(const Connection& conn)
    {
        // 存储连接
        m_allConnections.push_back(conn);

        // 为两个节点都建立索引
        BuildIndexesForNode(conn.node1, conn);
        BuildIndexesForNode(conn.node2, conn);
    }

    static uint32_t GetNextTpn(uint32_t nodeId)
    {
        uint32_t res = 0;
        auto it = m_nextTpn.find(nodeId);
        if (it != m_nextTpn.end()) {
            res = it->second;
            m_lock.lock();
            m_nextTpn[nodeId] += 1;
            m_lock.unlock();
        } else {
            res = 0;
            m_lock.lock();
            m_nextTpn[nodeId] = 1;
            m_lock.unlock();
        }
        return res;
    }

    // 获取指定节点的连接管理器视图
    TpConnectionManager GetConnectionManagerByNode(uint32_t nodeId) const
    {
        TpConnectionManager nodeManager;

        // 复制与该节点相关的连接
        auto it = m_nodeConnections.find(nodeId);
        if (it != m_nodeConnections.end()) {
            for (const auto& conn : it->second) {
                nodeManager.m_allConnections.push_back(conn);
                // 只为目标节点建立索引
                nodeManager.BuildIndexesForNode(nodeId, conn);
            }
        }

        return nodeManager;
    }

    // 根据预设配置的规则选择获取tpns
    std::vector<uint32_t> GetTpns(GetTpnRuleT ruler,
                                  bool useShortestPath,
                                  uint32_t localNodeId,
                                  uint32_t peerNodeId,
                                  uint32_t localPort = UINT32_MAX,
                                  uint32_t peerPort = UINT32_MAX,
                                  uint32_t priority = UINT32_MAX) const
    {
        std::vector<std::pair<uint32_t, uint32_t>> resWithMetrics;
        std::vector<uint32_t> res;
        uint32_t minMetrics = UINT32_MAX;
        switch (ruler) {
            case GetTpnRuleT::BY_PEERNODE:
                resWithMetrics = GetTpnsByPeerNode(localNodeId, peerNodeId);
                break;
            case GetTpnRuleT::BY_PEERNODE_PRIORITY:
                resWithMetrics = GetTpnsByPeerNodePriority(localNodeId, peerNodeId, priority);
                break;
            case GetTpnRuleT::BY_PEERNODE_LOCALPORT:
                resWithMetrics = GetTpnsByPeerNodeLocalPort(localNodeId, peerNodeId, localPort);
                break;
            case GetTpnRuleT::BY_PEERNODE_BOTHPORTS:
                resWithMetrics = GetTpnsByPeerNodeBothPorts(localNodeId, peerNodeId, localPort, peerPort);
                break;
            case GetTpnRuleT::BY_ALL:
                resWithMetrics = GetTpnsByFullCriteria(localNodeId, peerNodeId, localPort, peerPort, priority);
                break;
            default:
                break;
        }
        for (auto p : resWithMetrics) {
            minMetrics = std::min(minMetrics, p.second);
        }
        for (auto p : resWithMetrics) {
            if (!useShortestPath) {
                res.push_back(p.first);
            } else if (minMetrics == p.second) {
                res.push_back(p.first);
            }
        }
        return res;
    }

    // 1. 获取节点相关的所有连接
    std::vector<Connection> GetNodeConnections(uint32_t nodeId) const
    {
        auto it = m_nodeConnections.find(nodeId);
        if (it != m_nodeConnections.end()) {
            return it->second;
        }
        return {};
    }

    // 2. 通过对端nodeId找到所有可用tpn
    std::vector<std::pair<uint32_t, uint32_t>> GetTpnsByPeerNode(uint32_t localNodeId, uint32_t peerNodeId) const
    {
        std::vector<std::pair<uint32_t, uint32_t>> tpns;
        auto key = std::make_pair(localNodeId, peerNodeId);
        auto it = m_peerNodeIndex.find(key);
        if (it != m_peerNodeIndex.end()) {
            for (const auto& conn : it->second) {
                // 获取本地节点对应的TPN
                if (conn.node1 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
                } else if (conn.node2 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
                }
            }
        }
        return tpns;
    }

    // 3. 通过对端nodeId+priority找到所有可用tpn
    std::vector<std::pair<uint32_t, uint32_t>> GetTpnsByPeerNodePriority(uint32_t localNodeId,
                                                    uint32_t peerNodeId,
                                                    uint32_t priority) const
    {
        std::vector<std::pair<uint32_t, uint32_t>> tpns;
        auto key = std::make_tuple(localNodeId, peerNodeId, priority);
        auto it = m_peerNodePriorityIndex.find(key);
        if (it != m_peerNodePriorityIndex.end()) {
            for (const auto& conn : it->second) {
                // 获取本地节点对应的TPN
                if (conn.node1 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
                } else if (conn.node2 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
                }
            }
        }
        return tpns;
    }

    // 4. 通过对端nodeId+本端port找到所有可用tpn
    std::vector<std::pair<uint32_t, uint32_t>> GetTpnsByPeerNodeLocalPort(uint32_t localNodeId,
                                                     uint32_t peerNodeId,
                                                     uint32_t localPort) const
    {
        std::vector<std::pair<uint32_t, uint32_t>> tpns;
        auto key = std::make_tuple(localNodeId, peerNodeId, localPort);
        auto it = m_peerNodeLocalPortIndex.find(key);
        if (it != m_peerNodeLocalPortIndex.end()) {
            for (const auto& conn : it->second) {
                // 获取本地节点对应的TPN
                if (conn.node1 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
                } else if (conn.node2 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
                }
            }
        }
        return tpns;
    }

    // 5. 通过对端nodeId+本端port+对端port找到所有可用tpn
    std::vector<std::pair<uint32_t, uint32_t>> GetTpnsByPeerNodeBothPorts(uint32_t localNodeId,
                                                     uint32_t peerNodeId,
                                                     uint32_t localPort,
                                                     uint32_t peerPort) const
    {
        std::vector<std::pair<uint32_t, uint32_t>> tpns;
        auto key = std::make_tuple(localNodeId, peerNodeId, localPort, peerPort);
        auto it = m_bothPortsIndex.find(key);
        if (it != m_bothPortsIndex.end()) {
            for (const auto& conn : it->second) {
                // 获取本地节点对应的TPN
                if (conn.node1 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
                } else if (conn.node2 == localNodeId) {
                    tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
                }
            }
        }
        return tpns;
    }

    // 组合查询：通过完整条件找到所有可用tpn
    std::vector<std::pair<uint32_t, uint32_t>> GetTpnsByFullCriteria(uint32_t localNodeId,
                                               uint32_t peerNodeId,
                                               uint32_t localPort,
                                               uint32_t peerPort,
                                               uint32_t priority) const
    {
        std::vector<std::pair<uint32_t, uint32_t>> tpns;

        // 遍历本节点的所有连接，找到匹配条件的
        auto it = m_nodeConnections.find(localNodeId);
        if (it != m_nodeConnections.end()) {
            for (const auto& conn : it->second) {
                // 检查是否匹配条件
                bool isMatch = false;
                uint32_t localTpn = 0;

                if (conn.node1 == localNodeId && conn.node2 == peerNodeId) {
                    // localNodeId是node1
                    if (conn.port1 == localPort && conn.port2 == peerPort &&
                        conn.priority == priority) {
                        isMatch = true;
                        localTpn = conn.tpn1;
                    }
                } else if (conn.node2 == localNodeId && conn.node1 == peerNodeId) {
                    // localNodeId是node2
                    if (conn.port2 == localPort && conn.port1 == peerPort &&
                        conn.priority == priority) {
                        isMatch = true;
                        localTpn = conn.tpn2;
                    }
                }

                if (isMatch) {
                    tpns.push_back(std::make_pair(localTpn, conn.metrics));
                }
            }
        }
        return tpns;
    }

    // 获取某个节点的所有TPN
    std::vector<uint32_t> GetAllTpnsForNode(uint32_t nodeId) const
    {
        std::vector<uint32_t> tpns;
        auto it = m_nodeConnections.find(nodeId);
        if (it != m_nodeConnections.end()) {
            for (const auto& conn : it->second) {
                if (conn.node1 == nodeId) {
                    tpns.push_back(conn.tpn1);
                } else if (conn.node2 == nodeId) {
                    tpns.push_back(conn.tpn2);
                }
            }
        }
        return tpns;
    }

    // 获取节点的邻居节点
    std::set<uint32_t> GetNeighborNodes(uint32_t nodeId) const
    {
        std::set<uint32_t> neighbors;
        auto it = m_nodeConnections.find(nodeId);
        if (it != m_nodeConnections.end()) {
            for (const auto& conn : it->second) {
                if (conn.node1 == nodeId) {
                    neighbors.insert(conn.node2);
                } else if (conn.node2 == nodeId) {
                    neighbors.insert(conn.node1);
                }
            }
        }
        return neighbors;
    }

    // 获取所有连接
    const std::vector<Connection>& GetAllConnections() const
    {
        return m_allConnections;
    }

    // 获取连接总数
    size_t GetConnectionCount() const
    {
        return m_allConnections.size();
    }

    // 清空指定节点的连接
    void ClearNodeConnections(uint32_t nodeId)
    {
        m_nodeConnections.erase(nodeId);
        ClearNodeFromIndexes(nodeId);

        // 从allConnections_中移除相关连接
        m_allConnections.erase(
            std::remove_if(m_allConnections.begin(), m_allConnections.end(),
                [nodeId](const Connection& conn) {
                    return conn.node1 == nodeId || conn.node2 == nodeId;
                }),
            m_allConnections.end()
        );
    }

    // 清空所有连接
    void Clear()
    {
        m_allConnections.clear();
        m_nodeConnections.clear();
        m_peerNodeIndex.clear();
        m_peerNodePriorityIndex.clear();
        m_peerNodeLocalPortIndex.clear();
        m_bothPortsIndex.clear();
    }

private:
    // 为指定节点建立各种索引
    void BuildIndexesForNode(uint32_t localNodeId, const Connection& conn)
    {
        // 添加到节点连接列表
        m_nodeConnections[localNodeId].push_back(conn);

        uint32_t peerNodeId;
        uint32_t localPort;
        uint32_t peerPort;

        // 确定对端节点和端口
        if (conn.node1 == localNodeId) {
            peerNodeId = conn.node2;
            localPort = conn.port1;
            peerPort = conn.port2;
        } else {
            peerNodeId = conn.node1;
            localPort = conn.port2;
            peerPort = conn.port1;
        }

        // 建立各种索引
        // 索引2: 对端节点
        m_peerNodeIndex[{localNodeId, peerNodeId}].push_back(conn);

        // 索引3: 对端节点+优先级
        m_peerNodePriorityIndex[{localNodeId, peerNodeId, static_cast<uint32_t>(conn.priority)}].push_back(conn);

        // 索引4: 对端节点+本端端口
        m_peerNodeLocalPortIndex[{localNodeId, peerNodeId, localPort}].push_back(conn);

        // 索引5: 对端节点+本端端口+对端端口
        m_bothPortsIndex[{localNodeId, peerNodeId, localPort, peerPort}].push_back(conn);
    }

    // 从索引中清理指定节点
    void ClearNodeFromIndexes(uint32_t nodeId)
    {
        // 清理各种索引中包含该节点的条目
        for (auto it = m_peerNodeIndex.begin(); it != m_peerNodeIndex.end();) {
            if (it->first.first == nodeId) {
                it = m_peerNodeIndex.erase(it);
            } else {
                ++it;
            }
        }

        for (auto it = m_peerNodePriorityIndex.begin(); it != m_peerNodePriorityIndex.end();) {
            if (std::get<0>(it->first) == nodeId) {
                it = m_peerNodePriorityIndex.erase(it);
            } else {
                ++it;
            }
        }

        for (auto it = m_peerNodeLocalPortIndex.begin(); it != m_peerNodeLocalPortIndex.end();) {
            if (std::get<0>(it->first) == nodeId) {
                it = m_peerNodeLocalPortIndex.erase(it);
            } else {
                ++it;
            }
        }

        for (auto it = m_bothPortsIndex.begin(); it != m_bothPortsIndex.end();) {
            if (std::get<0>(it->first) == nodeId) {
                it = m_bothPortsIndex.erase(it);
            } else {
                ++it;
            }
        }
    }

private:
    // 主存储：所有连接
    std::vector<Connection> m_allConnections;

    // 每个节点的相关连接
    std::unordered_map<uint32_t, std::vector<Connection>> m_nodeConnections;

    // 索引2: (localNodeId, peerNodeId) -> connections
    std::map<std::pair<uint32_t, uint32_t>, std::vector<Connection>> m_peerNodeIndex;

    // 索引3: (localNodeId, peerNodeId, priority) -> connections
    std::map<std::tuple<uint32_t, uint32_t, uint32_t>, std::vector<Connection>> m_peerNodePriorityIndex;

    // 索引4: (localNodeId, peerNodeId, localPort) -> connections
    std::map<std::tuple<uint32_t, uint32_t, uint32_t>, std::vector<Connection>> m_peerNodeLocalPortIndex;

    // 索引5: (localNodeId, peerNodeId, localPort, peerPort) -> connections
    std::map<std::tuple<uint32_t, uint32_t, uint32_t, uint32_t>, std::vector<Connection>> m_bothPortsIndex;

    // 全局存储所有节点下一个新建的tpn编号
    static std::map<uint32_t, uint32_t> m_nextTpn;

    static std::mutex m_lock;
};

} // namespace utils

#endif
