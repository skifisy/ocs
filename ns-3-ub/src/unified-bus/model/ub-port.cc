// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/ub-port.h"
#include "ns3/ub-link.h"
#include "ns3/log.h"
#include "ns3/ub-network-address.h"
#include "ns3/ub-caqm.h"
#include "ns3/ub-tag.h"
using namespace utils;

namespace ns3 {
NS_OBJECT_ENSURE_REGISTERED(UbPort);
NS_LOG_COMPONENT_DEFINE("UbPort");

constexpr long DEFAULT_PFC_UP_THLD = 1677721;
constexpr long DEFAULT_PFC_LOW_THLD = 1342176;

/*********************
 * UbEgressQueue
 ********************/

// UbEgressQueue
TypeId UbEgressQueue::GetTypeId(void)
{
    static TypeId tid =
        TypeId("ns3::UbEgressQueue")
            .SetParent<Object>()
            .AddTraceSource(
            "UbEnqueue",
            "Enqueue a packet in the UbEgressQueue.",
            MakeTraceSourceAccessor(&UbEgressQueue::m_traceUbEnqueue),
            "ns3::UbEgressQueue::UbEnqueue")
            .AddTraceSource(
            "UbDequeue",
            "Dequeue a packet in the UbEgressQueue.",
            MakeTraceSourceAccessor(&UbEgressQueue::m_traceUbDequeue),
            "ns3::UbEgressQueue::UbDequeue")
            .AddAttribute(
            "m_maxIngressQueues",
            "The maximum number of packets accepted by this eq.",
            UintegerValue(100),
            MakeUintegerAccessor(&UbEgressQueue::m_maxIngressQueues),
            MakeUintegerChecker<uint32_t>());
    return tid;
}

UbEgressQueue::UbEgressQueue()
{
}

bool UbEgressQueue::DoEnqueue(PacketEntry packetEntry)
{
    NS_LOG_FUNCTION (this);

    if (m_egressQ.size () >= m_maxIngressQueues) {
        NS_LOG_LOGIC ("Queue full (at max packets) -- droppping pkt");
        return false;
    }
    m_egressQ.push(packetEntry);

    NS_LOG_LOGIC ("[UbEgressQueue DoEnqueue] Egress Queue size: " << m_egressQ.size ());

    return true;
}

PacketEntry UbEgressQueue::Peekqueue()
{
    NS_LOG_FUNCTION (this);
    if (m_egressQ.empty()) {
        NS_LOG_LOGIC ("Queue empty");
        return std::make_tuple(0, 0, nullptr);
    }
    auto [inPortId, priority, pkt] = m_egressQ.front();
    return std::make_tuple(inPortId, priority, pkt);
}

PacketEntry UbEgressQueue::DoDequeue()
{
    NS_LOG_FUNCTION (this);

    if (m_egressQ.empty()) {
        NS_LOG_LOGIC ("Queue empty");
        return std::make_tuple(0, 0, nullptr);
    }

    auto packetEntry = m_egressQ.front ();
    m_egressQ.pop();

    NS_LOG_LOGIC ("[UbEgressQueue DoDequeue] Egress Queue size: " << m_egressQ.size ());

    return packetEntry;
}

bool UbEgressQueue::IsEmpty()
{
    return m_egressQ.empty();
}

/******************
 * UbPort
 *****************/
std::unordered_map<UbNodeType_t, std::string>
UbPort::g_node_type_map = {{UB_SWITCH, "SWITCH"},
                           {UB_DEVICE, "HOST"}};

TypeId UbPort::GetTypeId(void)
{
    static TypeId tid =
        TypeId("ns3::UbPort")
            .SetParent<PointToPointNetDevice>()
            .AddConstructor<UbPort>()
            .AddAttribute("UbDataRate",
                          "The default data rate for ub link",
                          DataRateValue(DataRate ("400Gbps")),
                          MakeDataRateAccessor(&UbPort::m_bps),
                          MakeDataRateChecker())
            .AddAttribute("UbInterframeGap",
                          "The time to wait between packet transmissions",
                          TimeValue(Seconds (0.0)),
                          MakeTimeAccessor(&UbPort::m_tInterframeGap),
                          MakeTimeChecker())
            .AddAttribute("CbfcFlitLenByte",
                          "Cbfc flit len in Byte",
                          UintegerValue(20),
                          MakeUintegerAccessor(&UbPort::m_cbfcFlitLen),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("CbfcFlitsPerCell",
                          "Cbfc flits per cell",
                          UintegerValue(4),
                          MakeUintegerAccessor(&UbPort::m_cbfcFlitsPerCell),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("CbfcRetCellGrainDataPacket",
                          "Cbfc return cell grain data packet",
                          UintegerValue(2),
                          MakeUintegerAccessor(&UbPort::m_cbfcRetCellGrainDataPacket),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("CbfcRetCellGrainControlPacket",
                          "Cbfc return cell grain control packet",
                          UintegerValue(2),
                          MakeUintegerAccessor(&UbPort::m_cbfcRetCellGrainControlPacket),
                          MakeUintegerChecker<uint8_t>())
            .AddAttribute("CbfcInitCreditCell",
                          "According to the configuration of the receive buffer at the connected node port, "
                          "the unit is cell",
                          UintegerValue(1024),
                          MakeIntegerAccessor(&UbPort::m_cbfcPortTxfree),
                          MakeIntegerChecker<int32_t>())
            .AddAttribute("PfcUpThld",
                          "Pfc up thld",
                          IntegerValue(DEFAULT_PFC_UP_THLD),
                          MakeIntegerAccessor(&UbPort::m_pfcUpThld),
                          MakeIntegerChecker<int32_t>())
            .AddAttribute("PfcLowThld",
                          "Pfc low thld",
                          IntegerValue(DEFAULT_PFC_LOW_THLD),
                          MakeIntegerAccessor(&UbPort::m_pfcLowThld),
                          MakeIntegerChecker<int32_t>())
            .AddTraceSource("PortTxNotify",
                            "Port Tx",
                            MakeTraceSourceAccessor(&UbPort::m_tracePortTxNotify),
                            "ns3::UbPort::PortTxNotify")
            .AddTraceSource("PortRxNotify",
                            "Port Rx",
                            MakeTraceSourceAccessor(&UbPort::m_tracePortRxNotify),
                            "ns3::UbPort::PortRxNotify")
            .AddTraceSource("L1PairNotify",
                            "One end-to-end packet delivered from its ingress L1 to its egress L1",
                            MakeTraceSourceAccessor(&UbPort::m_traceL1PairNotify),
                            "ns3::UbPort::L1PairNotify")
            .AddTraceSource("PktRcvNotify",
                            "Notify after receiving the data packet.",
                            MakeTraceSourceAccessor(&UbPort::m_tracePktRcvNotify),
                            "ns3::UbPort::PktRcvNotify")
            .AddTraceSource("TraComEventNotify",
                            "Transmit complete event.",
                            MakeTraceSourceAccessor(&UbPort::m_traceTraComEventNotify),
                            "ns3::UbPort::TraComEventNotify");
    return tid;
}

UbPort::UbPort()
{
    NS_LOG_FUNCTION(this);
    m_ubEQ = CreateObject<UbEgressQueue>();
    m_ubSendState = SendState::READY;
    m_txBytes = 0;
    BooleanValue val;
    if (GlobalValue::GetValueByNameFailSafe("UB_RECORD_PKT_TRACE", val)) {
        GlobalValue::GetValueByName("UB_RECORD_PKT_TRACE", val);
        m_pktTraceEnabled = val.Get();
    } else {
        m_pktTraceEnabled = false;
    }
}

UbPort::~UbPort()
{
    NS_LOG_FUNCTION(this);
}

void UbPort::SetIfIndex(const uint32_t portId)
{
    m_portId = portId;
}

uint32_t UbPort::GetIfIndex() const
{
    return m_portId;
}

void UbPort::SetSendState(SendState state)
{
    m_ubSendState = state;
}

void UbPort::CreateAndInitFc(const std::string& type)
{
    if (type == "CBFC") {
        m_flowControl = CreateObject<UbCbfc>();
        if (m_flowControl == nullptr) {
            NS_LOG_DEBUG("[UbPort CreateAndInitFc] create UbFlowControl fail");
            NS_LOG_WARN(this);
        }
        auto flowControl = DynamicCast<UbCbfc>(m_flowControl);
        flowControl->Init(m_cbfcFlitLen, m_cbfcFlitsPerCell, m_cbfcRetCellGrainDataPacket,
            m_cbfcRetCellGrainControlPacket, m_cbfcPortTxfree, GetNode()->GetId(), m_portId);
        NS_LOG_DEBUG("[UbPort CreateAndInitFc] flowControl Cbfc Init");
    } else if (type == "PFC") {
        m_flowControl = CreateObject<UbPfc>();
        if (m_flowControl == nullptr) {
            NS_LOG_DEBUG("[UbPort CreateAndInitFc] create UbFlowControl fail");
            NS_LOG_WARN(this);
        }
        auto flowControl = DynamicCast<UbPfc>(m_flowControl);
        flowControl->Init(m_pfcUpThld, m_pfcLowThld, GetNode()->GetId(), m_portId);
        IntegerValue val;
        g_ub_vl_num.GetValue(val);
        int ubVlNum = val.Get();
        m_revQueueSize.resize(ubVlNum, 0);
        NS_LOG_DEBUG("[UbPort CreateAndInitFc] flowControl Pfc Init");
    } else {
        m_flowControl = CreateObject<UbFlowControl>();
        if (m_flowControl == nullptr) {
            NS_LOG_DEBUG("[UbPort CreateAndInitFc] create UbFlowControl fail");
            NS_LOG_WARN(this);
        }
    }
}

void UbPort::TransmitComplete()
{
    NS_LOG_DEBUG("[UbPort TransmitComplete] complete at: "
        << " NodeId: " << GetNode()->GetId()
        << " PortId: " << GetIfIndex()
        << " PacketUid: " << m_currentPkt->GetUid());
    NS_LOG_FUNCTION(this);

    m_ubSendState = SendState::READY;
    NS_ASSERT_MSG(
        m_currentPkt != nullptr, "UbPort::TransmitComplete(): m_currentPkt zero");

    // 转发时通知switch发送完成
    if (m_currentInPortId != m_portId) {
        GetNode()->GetObject<UbSwitch>()->SwitchSendFinish(m_portId, m_currentPriority, m_currentPkt);
    }
    m_flowControl->HandleReleaseOccupiedFlowControl(m_currentPkt, m_currentInPortId, m_portId);

    m_currentPkt = nullptr;
    m_currentInPortId = 0;
    m_currentPriority = 0;
    Simulator::ScheduleNow(&UbPort::TriggerTransmit, this);
}

void UbPort::DequeuePacket(void)
{
    NS_ASSERT_MSG(!m_ubEQ->IsEmpty(), "No packets can be sent! NodeId: "<< GetNode()->GetId()
        << " PortId: " << m_portId);
    m_ubSendState = SendState::BUSY;

    auto [inPortId, priority, packet] = m_ubEQ->DoDequeue();

    m_currentPkt = packet;
    m_currentInPortId = inPortId;
    m_currentPriority = priority;
    if (m_ubEQ->IsEmpty()) {
        // Switch allocation when port sendding packet.
        auto allocator = GetNode()->GetObject<UbSwitch>()->GetAllocator();
        Simulator::ScheduleNow(&UbSwitchAllocator::TriggerAllocator, allocator, this);
    }

    // switch节点, 通知switch发送了packet
    if (inPortId != m_portId) { // 转发的报文
        GetNode()->GetObject<UbSwitch>()->NotifySwitchDequeue(inPortId, m_portId, priority, packet);
    }

    if (!m_faultCallBack.IsNull()) {
        m_faultCallBack(packet, GetNode()->GetId(), m_portId, this);
        return;
    }
    TransmitPacket(packet, Time(0));
    return;
}

void UbPort::TransmitPacket(Ptr<Packet> packet, Time delay)
{
    UbFlowTag flowTag;
    bool hasTaskId = packet->PeekPacketTag(flowTag);
    PortTxNotify(GetNode()->GetId(), m_portId, packet->GetSize(),
                 hasTaskId ? flowTag.GetFlowId() : 0, hasTaskId);
    Ptr<UbPort> peerPort = m_channel->GetDestination(this);
    Ptr<UbSwitch> localSwitch = GetNode()->GetObject<UbSwitch>();
    Ptr<UbSwitch> peerSwitch = peerPort->GetNode()->GetObject<UbSwitch>();
    if (localSwitch != nullptr && peerSwitch != nullptr
        && localSwitch->GetNodeType() == UB_SWITCH
        && peerSwitch->GetNodeType() == UB_DEVICE) {
        UbL1TraceTag l1Tag;
        if (packet->PeekPacketTag(l1Tag)) {
            L1PairNotify(l1Tag.GetSrcL1(), GetNode()->GetId(), packet->GetUid(),
                         packet->GetSize(), hasTaskId ? flowTag.GetFlowId() : 0,
                         hasTaskId);
        }
    }
    NS_LOG_DEBUG("[UbPort send] nodetype: " << g_node_type_map[GetNode()->GetObject<UbSwitch>()->GetNodeType()]
        << " NodeId: " << GetNode()->GetId() << " PortId: " << m_portId << " send to:"
        << " NodeId: " << m_channel->GetDestination(this)->GetNode()->GetId()
        << " PortId: " << m_channel->GetDestination(this)->GetIfIndex()
        << " PacketUid: " << packet->GetUid());
    if (m_pktTraceEnabled) {
        UbPacketTraceTag tag;
        packet->RemovePacketTag(tag);
        tag.AddPortSendTrace(GetNode()->GetId(), m_portId, Simulator::Now().GetNanoSeconds());
        packet->AddPacketTag(tag);
    }

    Time txTime = m_bps.CalculateBytesTxTime(packet->GetSize()) + delay;
    Time txCompleteTime = txTime + m_tInterframeGap;
    TraComEventNotify(packet, txCompleteTime);

    Simulator::Schedule(txCompleteTime, &UbPort::TransmitComplete, this);
    bool result = m_channel->TransmitStart(packet, this, txTime);
    if (result == false) {
        NS_LOG_DEBUG("[DequeueAndTransmit]: send fail");
    }
    // 记录本端口发送的数据量
    NS_LOG_DEBUG("[UbFc DequeueAndTransmit] will send pkt size: " << packet->GetSize());
    UpdateTxBytes(packet->GetSize());

    return;
}

void UbPort::Receive(Ptr<Packet> packet)
{
    NS_LOG_DEBUG("[UbPort recv] nodetype: " << g_node_type_map[GetNode()->GetObject<UbSwitch>()->GetNodeType()]
        << " NodeId: " << GetNode()->GetId()
        << " PortId: " << GetIfIndex()
        << " recv from:"
        << " NodeId: " << m_channel->GetDestination(this)->GetNode()->GetId()
        << " PortId: " << m_channel->GetDestination(this)->GetIfIndex()
        << " PacketUid: "<< packet->GetUid());
    Ptr<UbPort> peerPort = m_channel->GetDestination(this);
    Ptr<UbSwitch> localSwitch = GetNode()->GetObject<UbSwitch>();
    Ptr<UbSwitch> peerSwitch = peerPort->GetNode()->GetObject<UbSwitch>();
    if (localSwitch != nullptr && peerSwitch != nullptr
        && localSwitch->GetNodeType() == UB_SWITCH
        && peerSwitch->GetNodeType() == UB_DEVICE) {
        UbL1TraceTag oldTag;
        packet->RemovePacketTag(oldTag);
        packet->AddPacketTag(UbL1TraceTag(GetNode()->GetId()));
    }
    if (m_pktTraceEnabled) {
        UbPacketTraceTag tag;
        packet->RemovePacketTag(tag);
        tag.AddPortRecvTrace(GetNode()->GetId(), m_portId, Simulator::Now().GetNanoSeconds());
        packet->AddPacketTag(tag);
    }
    PortRxNotify(GetNode()->GetId(), m_portId, packet->GetSize());
    GetNode()->GetObject<UbSwitch>()->SwitchHandlePacket(this, packet);

    return;
}

bool UbPort::Attach(Ptr<UbLink> ch)
{
    NS_LOG_FUNCTION(this << &ch);
    m_channel = ch;
    m_channel->Attach(this);
    NotifyLinkUp();
    return true;
}

void UbPort::NotifyLinkUp (void)
{
    NS_LOG_FUNCTION(this);
    m_linkUp = true;
}


uint32_t UbPort::ParseHeader(Ptr<Packet> p, Header& h)
{
    return p->RemoveHeader(h);
}

void UbPort::AddUdpHeader(Ptr<Packet> p, Ptr<UbTransportChannel> tp)
{
    UdpHeader udpHeader;
    udpHeader.SetDestinationPort(tp->GetDport());
    udpHeader.SetSourcePort(tp->GetUdpSport());
    p->AddHeader(udpHeader);
}

void UbPort::AddUdpHeader(Ptr<Packet> p, uint16_t sPort, uint16_t dPort)
{
    UdpHeader udpHeader;
    udpHeader.SetDestinationPort(dPort);
    udpHeader.SetSourcePort(sPort);
    p->AddHeader(udpHeader);
}

void UbPort::AddIpv4Header(Ptr<Packet> p, Ptr<UbTransportChannel> tp)
{
    Ipv4Header ipHeader;
    ipHeader.SetSource(tp->GetSip());
    ipHeader.SetDestination(tp->GetDip());
    ipHeader.SetProtocol(0x11);
    ipHeader.SetPayloadSize(p->GetSize());
    ipHeader.SetTtl(TIME_TO_LIVE);
    ipHeader.SetTos(0);
    p->AddHeader(ipHeader);
}

void UbPort::AddIpv4Header(Ptr<Packet> p, Ipv4Address sIp, Ipv4Address dIp)
{
    Ipv4Header ipHeader;
    ipHeader.SetSource(sIp);
    ipHeader.SetDestination(dIp);
    ipHeader.SetProtocol(0x11);
    ipHeader.SetPayloadSize(p->GetSize());
    ipHeader.SetTtl(TIME_TO_LIVE);
    ipHeader.SetTos(0);
    p->AddHeader(ipHeader);
}

void UbPort::AddNetHeader(Ptr<Packet> p)
{
    UbNetworkHeader netHeader;
    p->AddHeader(netHeader);
}

Ptr<Channel> UbPort::GetChannel(void) const
{
    return m_channel;
}

bool UbPort::IsUb(void) const
{
    return true;
}

void UbPort::TriggerTransmit()
{
    NS_LOG_DEBUG("[UbPort TriggerTransmit] nodeId: " << GetNode()->GetId()
        << " portId: " << GetIfIndex() <<" TriggerTransmit...");
    if (!m_linkUp) {
        NS_LOG_DEBUG("[UbPort TriggerTransmit] m_linkUp");
        return;
    } // if link is down, return
    if (m_ubSendState == SendState::BUSY) {
        NS_LOG_DEBUG("[UbPort TriggerTransmit] SendState::BUSY");
        return; // Quit if channel busy
    }
    if (m_ubEQ->IsEmpty()) {
        NS_LOG_DEBUG("[UbPort TriggerTransmit] trigger Allocator");
        auto allocator = GetNode()->GetObject<UbSwitch>()->GetAllocator();
        Simulator::ScheduleNow(&UbSwitchAllocator::TriggerAllocator, allocator, this);
        return;
    }
    DequeuePacket();
}

void UbPort::NotifyAllocationFinish()
{
    if (m_ubSendState == SendState::BUSY) {
        return;
    }
    if (m_ubEQ->IsEmpty()) {
        // No Packet to send
        return;
    }
    DequeuePacket();
}

Ptr<UbEgressQueue> UbPort::GetUbQueue()
{
    return m_ubEQ;
}

void UbPort::PktRcvNotify(Ptr<Packet> p)
{
    m_tracePktRcvNotify(p);
}

void UbPort::TraComEventNotify(Ptr<Packet> p, Time t)
{
    m_traceTraComEventNotify(p, t);
}

DataRate UbPort::GetDataRate()
{
    return m_bps;
}

Time UbPort::GetInterframeGap()
{
    return m_tInterframeGap;
}

void UbPort::SetCredits(int index, uint8_t value)
{ // 设置用于恢复的信用证值
    m_credits[index] = value;
}

void UbPort::ResetCredits()
{ // 设置用于恢复的信用证值
    for (uint8_t index = 0; index < qCnt; index++) {
        m_credits[index] = 0;
    }
}

uint8_t UbPort::GetCredits(int index)
{ // 获取用于恢复的信用证值
    return m_credits[index];
}

void UbPort::UpdateTxBytes(uint64_t bytes)
{
    m_txBytes += bytes;
}

uint64_t UbPort::GetTxBytes()
{
    return m_txBytes;
}

bool UbPort::IsReady()
{
    return m_ubSendState == SendState::READY;
}

bool UbPort::IsBusy()
{
    return m_ubSendState == SendState::BUSY;
}

void UbPort::SetDataRate(DataRate bps)
{
    NS_LOG_DEBUG("port set data rate");
    m_bps = bps;
    Ptr<UbCongestionControl> congestionCtrl = GetNode()->GetObject<UbSwitch>()->GetCongestionCtrl();
    if (congestionCtrl->GetCongestionAlgo() == CAQM) {
        Ptr<UbSwitchCaqm> caqmSw = DynamicCast<UbSwitchCaqm>(congestionCtrl);
        caqmSw->SetDataRate(m_portId, bps);
    }
}

void UbPort::IncreaseRcvQueueSize(Ptr<Packet> p, Ptr<UbPort> port)
{
    uint32_t pktSize = p->GetSize();
    NS_LOG_DEBUG("[UbFc IncreaseRcvQueueSize] pktSize: " << pktSize << " PortId: " << port->GetIfIndex());
    UbDatalinkPacketHeader pktHeader;
    p->PeekHeader(pktHeader);
    uint8_t vlId = pktHeader.GetPacketVL();
    NS_LOG_DEBUG("[UbFc IncreaseRcvQueueSize] before m_revQueueSize[ "
        << (uint32_t)vlId << " ]: " << (uint32_t)m_revQueueSize[vlId]);
    m_revQueueSize[vlId] += pktSize;
    NS_LOG_DEBUG("[UbFc IncreaseRcvQueueSize] after m_revQueueSize[ "
        << (uint32_t)vlId << " ]: " << (uint32_t)m_revQueueSize[vlId]);
}

void UbPort::DecreaseRcvQueueSize(Ptr<Packet> p, uint32_t portId)
{
    Ptr<UbPort> port = DynamicCast<UbPort>(GetNode()->GetDevice(portId));
    uint32_t pktSize = p->GetSize();
    NS_LOG_DEBUG("[UbFc DecreaseRcvQueueSize] pktSize: " << pktSize << " PortId: " << port->GetIfIndex());
    UbDatalinkPacketHeader pktHeader;
    p->PeekHeader(pktHeader);
    uint8_t vlId = pktHeader.GetPacketVL();
    NS_LOG_DEBUG("[UbFc DecreaseRcvQueueSize] before m_revQueueSize[ " << (uint32_t)vlId << " ]: "
        << (uint32_t)m_revQueueSize[vlId]);
    port->m_revQueueSize[vlId] -= pktSize;
    NS_LOG_DEBUG("[UbFc DecreaseRcvQueueSize] after m_revQueueSize[ " << (uint32_t)vlId << " ]: "
        << (uint32_t)m_revQueueSize[vlId]);
}

uint32_t UbPort::GetRcvVlQueueSize(uint8_t vlId)
{
    return m_revQueueSize[vlId];
}

std::vector<uint32_t> UbPort::GetRcvQueueSize()
{
    return m_revQueueSize;
}

void UbPort::PortTxNotify(uint32_t nodeId, uint32_t mPortId, uint32_t size,
                          uint32_t taskId, bool hasTaskId)
{
    m_tracePortTxNotify(nodeId, mPortId, size, taskId, hasTaskId);
}

void UbPort::PortRxNotify(uint32_t nodeId, uint32_t mPortId, uint32_t size)
{
    m_tracePortRxNotify(nodeId, mPortId, size);
}

void UbPort::L1PairNotify(uint32_t srcL1, uint32_t dstL1, uint32_t packetUid,
                          uint32_t size, uint32_t taskId, bool hasTaskId)
{
    m_traceL1PairNotify(srcL1, dstL1, packetUid, size, taskId, hasTaskId);
}

void UbPort::DoDispose()
{
    NS_LOG_FUNCTION(this);
    m_ubEQ = nullptr;
    m_channel = nullptr;
    m_currentPkt = nullptr;
    m_datalink = nullptr;
    m_flowControl = nullptr;
    m_revQueueSize.clear();
    NetDevice::DoDispose();
}

} // namespace ns3
