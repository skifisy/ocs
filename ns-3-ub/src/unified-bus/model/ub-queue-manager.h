// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_QUEUE_MANAGER_H
#define UB_QUEUE_MANAGER_H

#include "ns3/object.h"
#include "ns3/packet.h"
#include "ub-network-address.h"

namespace ns3 {

enum class IngressQueueType {
    VOQ,
    TPCHANNEL,
    IGQ
};

/**
 * @brief port的收包缓存队列
 */
class UbIngressQueue : public Object {
public:
    UbIngressQueue();
    virtual ~UbIngressQueue();
    static TypeId GetTypeId(void);
    
    virtual IngressQueueType GetIqType()
    {
        return IngressQueueType::IGQ;
    }

    virtual bool IsEmpty();
    virtual Ptr<Packet> GetNextPacket();
    virtual uint32_t GetNextPacketSize();
    void SetInPortId(uint32_t inPortId) { m_inPortId = inPortId; }
    void SetIgqPriority(uint32_t priority) { m_igqPriority = priority; }
    void SetOutPortId(uint32_t outPortId) { m_outPortId = outPortId; }
    uint32_t GetInPortId() { return m_inPortId; }
    uint32_t GetIgqPriority() { return m_igqPriority; }
    uint32_t GetOutPortId() { return m_outPortId; }
private:
    uint32_t m_igqPriority;
    uint32_t m_inPortId;
    uint32_t m_outPortId;
};

/**
 * @brief 防入voq(Visual Output Queue)的队列
 */
class UbPacketQueue : public UbIngressQueue {
public:
    UbPacketQueue();
    ~UbPacketQueue() final;
    static TypeId GetTypeId(void);

    bool IsEmpty() override;
    Ptr<Packet> GetNextPacket() override;
    std::queue<Ptr<Packet>>& Get() {return m_queue;}
    Ptr<Packet> Front() {return m_queue.front();}
    void Pop() {m_queue.pop();}
    void Push(Ptr<Packet> p) {m_queue.push(p);}
    IngressQueueType GetIqType() override;
    uint32_t GetNextPacketSize() override;

private:
    std::queue<Ptr<Packet>> m_queue;
    IngressQueueType m_iqType = IngressQueueType::VOQ;
};

/**
 * @brief 缓存管理模块
 */
class UbQueueManager : public Object {
public:
    UbQueueManager();
    ~UbQueueManager() {}
    void Init();
    static TypeId GetTypeId(void);

    // Init
    void SetVLNum(uint32_t vlNum)
    {
        m_vlNum = vlNum;
    }
    void SetPortsNum(uint32_t portsNum)
    {
        m_portsNum = portsNum;
    }

    // 获得入口队列buffer使用情况
    uint64_t GetIngressUsed(uint32_t port, uint32_t priority);
    // 检查是否可以接收新的数据包到入口队列
    bool CheckIngress(uint32_t port, uint32_t priority, uint32_t pSize);
    // 更新入口队列状态
    void PushIngress(uint32_t port, uint32_t priority, uint32_t pSize);
    void PushEgress(uint32_t port, uint32_t priority, uint32_t pSize);
    uint64_t GetEgressUsed(uint32_t port, uint32_t priority);
    uint64_t GetAllEgressUsed(uint32_t port);
    // 从入口队列移除数据包
    void PopIngress(uint32_t port, uint32_t priority, uint32_t pSize);
    void PopEgress(uint32_t port, uint32_t priority, uint32_t pSize);
    void SetBufferSize(uint32_t size);

private:
    using DarrayU64 = std::vector<std::vector<uint64_t>>;
    uint32_t m_vlNum = 0;
    uint32_t m_portsNum = 0;
    uint32_t m_bufferSize;
    DarrayU64 m_ingressBuf;    // 入口缓存
    DarrayU64 m_egressBuf;    // 出口缓存
};
} // namespace ns3

#endif /* UB_BUFFER_MANAGE_H */