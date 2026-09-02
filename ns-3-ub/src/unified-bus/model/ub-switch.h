// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_SWITCH_H
#define UB_SWITCH_H

#include <vector>
#include <unordered_map>
#include <queue>
#include "ns3/ub-routing-process.h"
#include "ns3/ub-queue-manager.h"
#include "ns3/node.h"
#include "ns3/traced-callback.h"
#include "ns3/ub-header.h"
#include "ns3/ipv4-header.h"
#include "ns3/udp-header.h"

namespace ns3 {
class UbSwitchCaqm;
class UbPort;
class UbSwitchAllocator;
class UbCongestionControl;

typedef enum {
    UB_SWITCH,
    UB_DEVICE
} UbNodeType_t;

typedef enum {
    UB_CONTROL_FRAME = 1,
    UB_URMA_DATA_PACKET,
    UB_LDST_DATA_PACKET,
    UNKOWN_TYPE
} UbPacketType_t;

/**
 * @brief 交换机
 */
class UbSwitch : public Object {
public:
    UbSwitch();
    ~UbSwitch();
    void DoDispose() override;
    static TypeId GetTypeId (void);

    void SwitchHandlePacket(Ptr<UbPort> port, Ptr<Packet> packet);
    // 端口发送完毕，通知交换机出队列
    void NotifySwitchDequeue(uint16_t inPortId, uint32_t outPort, uint32_t priority, Ptr<Packet> p);

    void Init();
    void NodePortsFcInit();
    uint32_t GetVLNum()
    {
        return m_vlNum;
    }
    void SetVLNum(uint32_t vlNum)
    {
        m_vlNum = vlNum;
    }
    UbNodeType_t GetNodeType() {return m_nodeType;}
    void SetNodeType(UbNodeType_t type) {m_nodeType = type;}
    uint32_t GetPortsNum() {return m_portsNum;}
    void SetPortsNum(uint32_t portsNum) {m_portsNum = portsNum;}
    void AddTpIntoAlgroithm(Ptr<UbIngressQueue> tp, uint32_t outPort, uint32_t priority);
    void AddPktToVoq(Ptr<Packet> p, uint32_t outPort, uint32_t priority, uint32_t inPort);
    Ptr<UbSwitchAllocator> GetAllocator();
    Ipv4Address GetNodIpv4Addr(){return m_Ipv4Addr;}
    Ptr<UbRoutingProcess> GetRoutingProcess() {return m_routingProcess;}
    bool IsCBFCEnable();
    bool IsPFCEnable();

    void SetCongestionCtrl(Ptr<UbCongestionControl> congestionCtrl);
    Ptr<UbCongestionControl> GetCongestionCtrl();
    void SwitchSendFinish(uint32_t portId, uint32_t pri, Ptr<Packet> packet);
    Ptr<UbQueueManager> GetQueueManager();    // Queue Manage Unit

private:

    TracedCallback<uint32_t, UbTransportHeader> m_traceLastPacketTraversesNotify;

    void LastPacketTraversesNotify(uint32_t nodeId, UbTransportHeader ubTpHeader);

    void VoqInit();
    void AddVoqIntoAlgroithm();
    void SendPacket(Ptr<Packet> p, uint32_t inPort, uint32_t outPort, uint32_t priority);
    void ReceivePacket(Ptr<UbPort> port, Ptr<Packet> p);

    UbPacketType_t GetPacketType(Ptr<Packet> packet);
    void SinkControlFrame(Ptr<UbPort> port, Ptr<Packet> packet);
    void HandleURMADataPacket(Ptr<UbPort> port, Ptr<Packet> packet);
    void HandleLdstDataPacket(Ptr<UbPort> port, Ptr<Packet> packet);
    bool SinkTpDataPacket(Ptr<UbPort> port, Ptr<Packet> packet);
    bool SinkMemDataPacket(Ptr<UbPort> port, Ptr<Packet> packet);
    void ParseURMAPacketHeader(Ptr<Packet> packet);
    void ParseLdstPacketHeader(Ptr<Packet> packet);
    void GetURMARoutingKey(Ptr<Packet> packet, RoutingKey &rtKey);
    void GetLdstRoutingKey(Ptr<Packet> packet, RoutingKey &rtKey);
    void ForwardDataPacket(Ptr<UbPort> port, Ptr<Packet> packet);
    void ChangePakcetRoutingPolicy(Ptr<Packet> packet,  bool useShortestPath);

    Ptr<UbQueueManager> m_queueManager;   // Memory Management Unit
    Ptr<UbCongestionControl> m_congestionCtrl;
    UbNodeType_t m_nodeType;
    uint32_t m_portsNum = 1025;
    Ptr<UbSwitchAllocator> m_allocator;
    uint32_t m_vlNum = 16;
    VirtualOutputQueue_t m_voq; // virtualOutputQueue[outport][priority][inport] for DOD
    Ptr<UbRoutingProcess> m_routingProcess;   // Router Model

    Ipv4Address m_Ipv4Addr;
    bool m_isECNEnable;
    // cbfc
    bool m_isCBFCEnable;
    // pfc
    bool m_isPFCEnable;

    UbDatalinkPacketHeader m_datalinkHeader;
    // URMA Headers
    UbNetworkHeader m_networkHeader;
    Ipv4Header m_ipv4Header;
    UdpHeader m_udpHeader;
    UbTransportHeader m_ubTpHeader;
    // LDST Headers
    UbCna16NetworkHeader m_memHeader;
    UbDummyTransactionHeader m_dummyTaHeader;
};

} // namespace ns3

#endif /* UB_SWITCH_H */