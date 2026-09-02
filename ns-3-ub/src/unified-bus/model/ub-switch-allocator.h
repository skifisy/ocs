// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_UBSWITCH_ALLOCATOR_H
#define UB_UBSWITCH_ALLOCATOR_H

#include <vector>
#include <unordered_map>
#include "ns3/object.h"
#include "ns3/nstime.h"
#include "ns3/node.h"

namespace ns3 {

class UbSwitch;
class UbIngressQueue;
class UbPort;

// outport, priority, voq/TpChannel/ctrlq
typedef std::vector<std::vector<std::vector<Ptr<UbIngressQueue> > > > IngressSource_t;
typedef std::vector<bool> EgressStatus_t;

/**
 * @brief 交换机调度器基类
 */
class UbSwitchAllocator : public Object {
public:
    UbSwitchAllocator();
    virtual ~UbSwitchAllocator();
    static TypeId GetTypeId (void);
    virtual void TriggerAllocator(Ptr<UbPort> outPort);
    virtual void Init();
    void SetNodeId(uint32_t nodeId) {m_nodeId = nodeId;}
    void RegisterUbIngressQueue(Ptr<UbIngressQueue> ingressQueue, uint32_t outPort, uint32_t priority);
    void RegisterEgressStauts(uint32_t portsNum);
    void SetEgressStatus(uint32_t portId, bool status);
    bool GetEgressStatus(uint32_t portId);
    void DoDispose() override;

protected:
    Time m_allocationTime;
    uint32_t m_nodeId;
    IngressSource_t m_igsrc;
    EgressStatus_t m_egStatus;
};


/**
 * @brief 轮询算法的交换机调度器
 */
class UbRoundRobinAllocator : public UbSwitchAllocator {
public:
    UbRoundRobinAllocator() {}
    virtual ~UbRoundRobinAllocator() {}
    static TypeId GetTypeId(void);

    virtual void TriggerAllocator(Ptr<UbPort> outPort) override;
    virtual void Init() override;
    Ptr<UbIngressQueue> SelectNextIngressQueue(Ptr<UbPort> outPort);
    void AllocateNextPacket(Ptr<UbPort> outPort);

private:
    std::vector<std::vector<uint32_t> > m_rrIdx;
    std::vector<bool> m_isRunning;
    std::vector<bool> m_oneMoreRound;
};

} /* namespace ns3 */

#endif /* UB_UbSwitchAllocator_H */