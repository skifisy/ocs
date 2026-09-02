// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/ub-queue-manager.h"
#include "ns3/ub-header.h"

namespace ns3 {

NS_OBJECT_ENSURE_REGISTERED(UbIngressQueue);
NS_OBJECT_ENSURE_REGISTERED(UbQueueManager);
NS_LOG_COMPONENT_DEFINE("UbQueueManager");

/*-----------------------------------------UbIngressQueue----------------------------------------------*/

UbIngressQueue::UbIngressQueue()
{
}

UbIngressQueue::~UbIngressQueue()
{
}

TypeId UbIngressQueue::GetTypeId(void)
{
    static TypeId tid = TypeId("ns3::UbIngressQueue")
        .SetParent<Object>()
        .SetGroupName("UnifiedBus")
        .AddConstructor<UbIngressQueue>();
    return tid;
}

bool UbIngressQueue::IsEmpty()
{
    return true;
}
Ptr<Packet> UbIngressQueue::GetNextPacket()
{
    return nullptr;
}
uint32_t UbIngressQueue::GetNextPacketSize()
{
    return 0;
}

/*-----------------------------------------UbPacketQueue----------------------------------------------*/
bool UbPacketQueue::IsEmpty()
{
    return m_queue.empty();
}

UbPacketQueue::UbPacketQueue()
{
}

UbPacketQueue::~UbPacketQueue()
{
}

Ptr<Packet> UbPacketQueue::GetNextPacket()
{
    auto p = m_queue.front();
    m_queue.pop();
    return p;
}

TypeId UbPacketQueue::GetTypeId(void)
{
    static TypeId tid = TypeId("ns3::UbPacketQueue")
        .SetGroupName("UnifiedBus")
        .SetParent<UbIngressQueue>()
        .AddConstructor<UbPacketQueue>();
    return tid;
}

IngressQueueType UbPacketQueue::GetIqType()
{
    return m_iqType;
}

uint32_t UbPacketQueue::GetNextPacketSize()
{
    if (GetInPortId() == GetOutPortId()) { // crd报文等控制报文
        NS_LOG_DEBUG("[UbPacketQueue GetNextPacketSize] is ctrl pkt");
        UbDatalinkControlCreditHeader  DatalinkControlCreditHeader;
        uint32_t UbDataLinkCtrlSize = DatalinkControlCreditHeader.GetSerializedSize();
        return UbDataLinkCtrlSize;
    }
    Ptr<Packet> p = Front();
    uint32_t nextPktSize = p->GetSize();
    NS_LOG_DEBUG("[UbPacketQueue GetNextPacketSize] is forward pkt, nextPktSize:" << nextPktSize);

    return nextPktSize;
}

/*-----------------------------------------UbQueueManager----------------------------------------------*/
UbQueueManager::UbQueueManager(void)
{
}

TypeId UbQueueManager::GetTypeId(void)
{
    static TypeId tid = TypeId("ns3::UbQueueManager")
        .SetParent<Object>()
        .SetGroupName("UnifiedBus")
        .AddConstructor<UbQueueManager>()
        .AddAttribute("BufferSize",
        "Port Buffer Size in Byte.",
        UintegerValue(utils::DEFAULT_PORT_BUFFER_SIZE),
        MakeUintegerAccessor(&UbQueueManager::m_bufferSize),
        MakeUintegerChecker<uint32_t>());
    return tid;
}

void UbQueueManager::Init()
{
    m_ingressBuf.resize(m_portsNum);
    for (auto& i : m_ingressBuf) {
        i.resize(m_vlNum);
    }
    m_egressBuf.resize(m_portsNum);
    for (auto& i : m_egressBuf) {
        i.resize(m_vlNum);
    }
}

uint64_t UbQueueManager::GetIngressUsed(uint32_t port, uint32_t priority)
{
    return m_ingressBuf[port][priority];
}

bool UbQueueManager::CheckIngress(uint32_t port, uint32_t priority, uint32_t pSize)
{
    return (m_ingressBuf[port][priority] + pSize < m_bufferSize);
}

void UbQueueManager::PushIngress(uint32_t port, uint32_t priority, uint32_t pSize)
{
    m_ingressBuf[port][priority] += pSize;
}

void UbQueueManager::PopIngress(uint32_t port, uint32_t priority, uint32_t pSize)
{
    m_ingressBuf[port][priority] -= pSize;
}

uint64_t UbQueueManager::GetEgressUsed(uint32_t port, uint32_t priority)
{
    return m_egressBuf[port][priority];
}

uint64_t UbQueueManager::GetAllEgressUsed(uint32_t port)
{
    uint64_t sum = 0;
    for (uint32_t i = 0; i < m_vlNum; i++) {
        sum += m_egressBuf[port][i];
    }
    return sum;
}

void UbQueueManager::PushEgress(uint32_t port, uint32_t priority, uint32_t pSize)
{
    m_egressBuf[port][priority] += pSize;
}

void UbQueueManager::PopEgress(uint32_t port, uint32_t priority, uint32_t pSize)
{
    m_egressBuf[port][priority] -= pSize;
}
} // namespace ns3
