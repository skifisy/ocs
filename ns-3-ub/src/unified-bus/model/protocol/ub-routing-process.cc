// SPDX-License-Identifier: GPL-2.0-only
#include <algorithm>
#include <limits>
#include "ns3/ub-controller.h"
#include "ns3/ub-header.h"
#include "ns3/ub-network-address.h"
#include "ns3/ub-port.h"
#include "ns3/ub-queue-manager.h"
#include "ns3/ub-routing-process.h"
#include "ns3/udp-header.h"
#include "ns3/ipv4-header.h"
using namespace utils;

namespace ns3 {
NS_OBJECT_ENSURE_REGISTERED(UbRoutingProcess);
NS_LOG_COMPONENT_DEFINE("UbRoutingProcess");

/*-----------------------------------------UbRoutingProcess----------------------------------------------*/
TypeId UbRoutingProcess::GetTypeId(void)
{
    static TypeId tid = TypeId("ns3::UbRoutingProcess")
        .SetParent<Object>()
        .SetGroupName("UnifiedBus")
        .AddConstructor<UbRoutingProcess>()
        .AddAttribute("RoutingAlgorithm",
                    "Routing algorithm applied by UbRoutingProcess.",
                    EnumValue(UbRoutingAlgorithm::HASH),
                    MakeEnumAccessor<UbRoutingProcess::UbRoutingAlgorithm>(
                                    &UbRoutingProcess::m_routingAlgorithm),
                    MakeEnumChecker(UbRoutingAlgorithm::HASH, "HASH",
                                    UbRoutingAlgorithm::ADAPTIVE, "ADAPTIVE"));
    return tid;
}

UbRoutingProcess::UbRoutingProcess()
{
}

void UbRoutingProcess::AddShortestRoute(const uint32_t destIP, const std::vector<uint16_t>& outPorts)
{
    // 标准化端口集合（排序去重）
    std::vector<uint16_t> target;
    auto itRt = m_rtShortest.find(destIP);
    if (itRt != m_rtShortest.end()) {
        target.insert(target.end(), (*(itRt->second)).begin(), (*(itRt->second)).end());
    }
    target.insert(target.end(), outPorts.begin(), outPorts.end());
    std::vector<uint16_t> normalized = normalizePorts(outPorts);
    
    // 查找或创建共享端口集合
    auto it = m_portSetPool.find(normalized);
    if (it != m_portSetPool.end()) {
        //  已存在相同端口集合，共享指针
        m_rtShortest[destIP] = it->second;
    } else {
        // 创建新端口集合并加入池中
        auto sharedPorts = std::make_shared<std::vector<uint16_t>>(normalized);
        m_portSetPool[normalized] = sharedPorts;
        m_rtShortest[destIP] = sharedPorts;
    }
}

void UbRoutingProcess::AddOtherRoute(const uint32_t destIP, const std::vector<uint16_t>& outPorts)
{
    // 标准化端口集合（排序去重）
    std::vector<uint16_t> target;
    auto itRt = m_rtOther.find(destIP);
    if (itRt != m_rtOther.end()) {
        target.insert(target.end(), (*(itRt->second)).begin(), (*(itRt->second)).end());
    }
    target.insert(target.end(), outPorts.begin(), outPorts.end());
    std::vector<uint16_t> normalized = normalizePorts(target);
    
    // 查找或创建共享端口集合
    auto it = m_portSetPool.find(normalized);
    if (it != m_portSetPool.end()) {
        // 已存在相同端口集合，共享指针
        m_rtOther[destIP] = it->second;
    } else {
        // 创建新端口集合并加入池中
        auto sharedPorts = std::make_shared<std::vector<uint16_t>>(normalized);
        m_portSetPool[normalized] = sharedPorts;
        m_rtOther[destIP] = sharedPorts;
    }
}

const std::vector<uint16_t> UbRoutingProcess::GetShortestOutPorts(const uint32_t destIP)
{
    std::vector<uint16_t> res;
    auto it = m_rtShortest.find(destIP);
    if (it != m_rtShortest.end()) {
        res.insert(res.end(), (*(it->second)).begin(), (*(it->second)).end());
    }
    return res;
}

const std::vector<uint16_t> UbRoutingProcess::GetOtherOutPorts(const uint32_t destIP)
{
    std::vector<uint16_t> res;
    auto it = m_rtOther.find(destIP);
    if (it != m_rtOther.end()) {
        res.insert(res.end(), (*(it->second)).begin(), (*(it->second)).end());
    }
    return res;
}

const std::vector<uint16_t> UbRoutingProcess::GetAllOutPorts(const uint32_t destIP)
{
    std::vector<uint16_t> res;
    auto it = m_rtOther.find(destIP);
    if (it != m_rtOther.end()) {
        res.insert(res.end(), (*(it->second)).begin(), (*(it->second)).end());
    }
    it = m_rtShortest.find(destIP);
    if (it != m_rtShortest.end()) {
        res.insert(res.end(), (*(it->second)).begin(), (*(it->second)).end());
    }
    return res;
}


// 删除路由条目
bool UbRoutingProcess::RemoveShortestRoute(const uint32_t destIP)
{
    return m_rtShortest.erase(destIP) > 0;
}

// 删除路由条目
bool UbRoutingProcess::RemoveOtherRoute(const uint32_t destIP)
{
    return m_rtOther.erase(destIP) > 0;
}

int UbRoutingProcess::SelectAdaptiveOutPort(RoutingKey &rtKey, const std::vector<uint16_t> candidatePorts)
{
    auto node = NodeList::GetNode(m_nodeId);
    auto queueManager = node->GetObject<UbSwitch>()->GetQueueManager();
    uint8_t priority = rtKey.priority;

    auto calcLoadScore = [&](uint16_t outPort) -> uint64_t {
        if (queueManager == nullptr) {
            return 0;
        }
        return queueManager->GetEgressUsed(outPort, static_cast<uint32_t>(priority));
    };

    uint64_t bestScore = std::numeric_limits<uint64_t>::max();
    std::vector<uint16_t> bestPorts;
    for (uint16_t port : candidatePorts) {
        uint64_t score = calcLoadScore(port);
        if (score < bestScore) {
            bestScore = score;
            bestPorts.clear();
            bestPorts.push_back(port);
        } else if (score == bestScore) {
            bestPorts.push_back(port);
        }
    }

    if (bestPorts.empty()) {
        return -1;
    }

    uint16_t selectedPort = bestPorts.front();
    return selectedPort;
}

const std::vector<uint16_t> UbRoutingProcess::GetCandidatePorts(uint32_t &dip, bool useShortestPath, uint16_t inPortId)
{
    auto buildCandidates = [this](bool useShortestPath, uint32_t dip) {
        std::vector<uint16_t> candidates;
        if (useShortestPath) {
            candidates = GetShortestOutPorts(dip);
        } else {
            candidates = GetAllOutPorts(dip);
        }
        return candidates;
    };

    // 1. 首先基于目的节点的port地址进行选择
    std::vector<uint16_t> candidatePorts = buildCandidates(useShortestPath, dip);
    if (candidatePorts.empty()) {
        // 2. 如果找不到，掩盖port地址，使用主机的primary地址进行寻址
        Ipv4Mask mask("255.255.255.0");
        uint32_t maskedDip = Ipv4Address(dip).CombineMask(mask).Get();
        if (maskedDip != dip) {
            candidatePorts = buildCandidates(useShortestPath, maskedDip);
            dip = maskedDip;
        }
    }

    if (inPortId != UINT16_MAX) {
        std::vector<uint16_t> validPorts;
        for (uint16_t port : candidatePorts) {
            if (port != inPortId) {
                validPorts.push_back(port);
            }
        }
        candidatePorts = validPorts;
    }

    return candidatePorts;
}

uint64_t UbRoutingProcess::CalcHash(uint32_t sip, uint32_t dip, uint16_t sport, uint16_t dport, uint8_t priority)
{
    uint8_t buf[13];
    buf[0] = (sip >> 24) & 0xff;
    buf[1] = (sip >> 16) & 0xff;
    buf[2] = (sip >> 8) & 0xff;
    buf[3] = sip & 0xff;
    buf[4] = (dip >> 24) & 0xff;
    buf[5] = (dip >> 16) & 0xff;
    buf[6] = (dip >> 8) & 0xff;
    buf[7] = dip & 0xff;
    buf[8] = (sport >> 8) & 0xff;
    buf[9] = sport & 0xff;
    buf[10] = (dport >> 8) & 0xff;
    buf[11] = dport & 0xff;
    buf[12] = priority;
    std::string str(reinterpret_cast<const char*>(buf), sizeof(buf));
    uint64_t hash = Hash64(str);
    return hash;
}

int UbRoutingProcess::SelectOutPort(RoutingKey &rtKey, const std::vector<uint16_t> candidatePorts)
{
    uint32_t sip = rtKey.sip;
    uint32_t dip = rtKey.dip;
    uint16_t sport = rtKey.sport;
    uint16_t dport = rtKey.dport;
    uint8_t priority = rtKey.priority;
    bool usePacketSpray = rtKey.usePacketSpray;

    uint64_t hash64 = 0;
    if (usePacketSpray) {
        hash64 = CalcHash(sip, dip, sport, dport, priority);
    } else {
        // usePacketSpray == LB_MODE_PER_FLOW
        hash64 = CalcHash(sip, dip, 0, 0, priority);
    }
    uint32_t idx = hash64 % candidatePorts.size();
    return candidatePorts[idx];
}

bool UbRoutingProcess::GetSelectShortestPath()
{
    return m_selectShortestPaths;
}

// 1. GetCandidatePorts基于useShortestPath选择可用的出端口集合
// 2. 基于用户设定的UbRoutingAlgorithm在candidatePorts中选择最终的出端口
// 2.1 如果是 HASH 算法，基于五元组哈希选择出端口(如果是usePacketSpray则使用完整五元组，否则掩盖sport和dport为0)
// 2.2 如果是 ADAPTIVE 算法，基于QueueManager信息选择负载最小的出端口
// 3. 如果找不到出端口，报错
int UbRoutingProcess::GetOutPort(RoutingKey &rtKey, uint16_t inPort)
{
    uint32_t sip = rtKey.sip;
    uint32_t dip = rtKey.dip;
    uint16_t sport = rtKey.sport;
    uint16_t dport = rtKey.dport;
    uint8_t priority = rtKey.priority;
    bool useShortestPath = rtKey.useShortestPath;
    bool usePacketSpray = rtKey.usePacketSpray;
    NS_LOG_DEBUG("[UbRoutingProcess GetOutPort]: sip: " << Ipv4Address(sip)
                << " dip: " << Ipv4Address(dip)
                << " sport: " << sport
                << " dport: " << dport
                << " priority: " << (uint16_t)priority
                << " useShortestPath: " << useShortestPath
                << " usePacketSpray: " << usePacketSpray);
    
    uint32_t tempDip = dip;
    auto candidatePorts = GetCandidatePorts(tempDip, useShortestPath, inPort);
    if (candidatePorts.size() == 0) {
        NS_LOG_ERROR("No candidate ports found for dip: " << Ipv4Address(dip));
        return -1;
    }

    int outPortId = -1;
    if(m_routingAlgorithm == UbRoutingProcess::UbRoutingAlgorithm::HASH){
        outPortId = SelectOutPort(rtKey, candidatePorts);
    } else if(m_routingAlgorithm == UbRoutingProcess::UbRoutingAlgorithm::ADAPTIVE){
        outPortId = SelectAdaptiveOutPort(rtKey, candidatePorts);
    }

    // TODO
    if(useShortestPath){
        m_selectShortestPaths = true;
    } else {
        auto shortestOutPorts = GetShortestOutPorts(tempDip);
        if (std::find(shortestOutPorts.begin(), shortestOutPorts.end(), candidatePorts[outPortId]) != shortestOutPorts.end()) {
            m_selectShortestPaths = true;
        } else {
            m_selectShortestPaths = false;
        }
    }

    // 若找不到出端口，报ASSERT
    NS_ASSERT_MSG(outPortId != -1, "No available output port found");
    
    return outPortId;
}
} // namespace ns3
