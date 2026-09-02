// SPDX-License-Identifier: GPL-2.0-only
#include "ub-app.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/uinteger.h"
#include "ns3/callback.h"
#include "ns3/ub-datatype.h"
#include "ns3/ub-function.h"
#include "ub-traffic-gen.h"
#include "ns3/ub-routing-process.h"
#include "ns3/ub-port.h"
#include "ns3/ub-utils.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("UbApp");

NS_OBJECT_ENSURE_REGISTERED(UbApp);
TypeId UbApp::GetTypeId(void)
{
    static TypeId tid =
        TypeId("ns3::UbApp")
            .SetParent<Application>()
            .SetGroupName("UnifiedBus")
            .AddConstructor<UbApp>()
            .AddAttribute("EnableMultiPath",
                          "Enable Multipath feature",
                          BooleanValue(false),
                          MakeBooleanAccessor(&UbApp::m_multiPathEnable),
                          MakeBooleanChecker())
            .AddAttribute("UseShortestPaths",
                          "If true, only create TPs on source ports that belong to shortest paths.",
                          BooleanValue(false),
                          MakeBooleanAccessor(&UbApp::m_useShortestPaths),
                          MakeBooleanChecker())
            .AddTraceSource("MemTaskStartsNotify",
                            "MEM Task Starts, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceMemTaskStartsNotify),
                            "ns3::UbApp::MemTaskStartsNotify")
            .AddTraceSource("MemTaskCompletesNotify",
                            "MEM Task Completes, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceMemTaskCompletesNotify),
                            "ns3::UbApp::MemTaskCompletesNotify")
            .AddTraceSource("WqeTaskStartsNotify",
                            "WQE Task Starts, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceWqeTaskStartsNotify),
                            "ns3::UbApp::WqeTaskStartsNotify")
            .AddTraceSource("WqeTaskCompletesNotify",
                            "WQE Task Completes, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceWqeTaskCompletesNotify),
                            "ns3::UbApp::WqeTaskCompletesNotify");
    return tid;
}

UbApp::UbApp()
{
    m_random = CreateObject<UniformRandomVariable>();
    m_random->SetAttribute("Min", DoubleValue(0.0));
    m_random->SetAttribute("Max", DoubleValue(1.0));
}

UbApp::~UbApp()
{
}

void UbApp::SetFinishCallback(Callback<void, uint32_t, uint32_t> cb, Ptr<UbJetty> jetty)
{
    jetty->SetClientCallback(cb);
}

void UbApp::SetFinishCallback(Callback<void, uint32_t> cb, Ptr<UbLdstInstance> ubLdstInstance)
{
    ubLdstInstance->SetClientCallback(cb);
}

void UbApp::DoDispose(void)
{
    Application::DoDispose();
}

void UbApp::SendTraffic(TrafficRecord record)
{
    if (record.priority == 0) {
        NS_LOG_DEBUG("Task uses the highest priority, not recommended.");
    }

    if (record.opType == "MEM_STORE" || record.opType == "MEM_LOAD") {
        // 内存语义发送
        UbMemOperationType type = UbMemOperationType::STORE;
        if (record.opType == "MEM_STORE") {
            type = UbMemOperationType::STORE;
        } else if (record.opType == "MEM_LOAD") {
            type = UbMemOperationType::LOAD;
        }
        auto ldstInstance = GetNode()->GetObject<UbLdstInstance>();
        Ptr<UbFunction> ubFunc = GetNode()->GetObject<UbController>()->GetUbFunction();
        SetFinishCallback(MakeCallback(&UbApp::OnMemTaskCompleted, this), ldstInstance);
        NS_LOG_INFO("MEM Task Starts, taskId: " << record.taskId);
        MemTaskStartsNotify(GetNode()->GetId(), record.taskId);
        std::vector<uint32_t> threadIds = {0, 1};
        ldstInstance->HandleLdstTask(record.sourceNode, record.destNode, record.dataSize,
                                     record.taskId, type, static_cast<UbPriority>(record.priority),
                                     threadIds, 0);
    } else if (record.opType == "URMA_WRITE") {
        // URMA发送
        Ptr<UbFunction> ubFunc = GetNode()->GetObject<UbController>()->GetUbFunction();
        Ptr<UbTransaction> ubTa = GetNode()->GetObject<UbController>()->GetUbTransaction();
        bool jettyExist = ubFunc->IsJettyExists(m_jettyNum);
        if (jettyExist) {
            NS_LOG_ERROR("Jetty already exists");
            return;
        }
        ubFunc->CreateJetty(record.sourceNode, record.destNode, m_jettyNum);
        vector<uint32_t> tpns = GetNode()->GetObject<UbController>()->GetTpConnManager().GetTpns(
            m_getTpnRule, m_useShortestPaths, record.sourceNode,
            record.destNode, UINT32_MAX, UINT32_MAX, record.priority);
        // tpns为空表示无TP文件，亦或者TP文件中没有相关的TP。无论哪种，实时新建TP
        if (tpns.empty()) {
            CreateTPs(record.sourceNode, record.destNode, record.priority, tpns);
        }
        bool bindRst = ubTa->JettyBindTp(record.sourceNode, record.destNode, m_jettyNum, m_multiPathEnable, tpns);
        if (bindRst) {
            Ptr<UbJetty> curr_jetty = ubFunc->GetJetty(m_jettyNum);
            SetFinishCallback(MakeCallback(&UbApp::OnTaskCompleted, this), curr_jetty);
            NS_LOG_INFO("WQE Starts, jettyNum: " << m_jettyNum << " taskId: " << record.taskId);
            NS_LOG_INFO("Src: " << record.sourceNode << " Dst: " << record.destNode);
            WqeTaskStartsNotify(GetNode()->GetId(), m_jettyNum, record.taskId);
            NS_LOG_INFO("[APPLICATION INFO] taskId: " << record.taskId << ",start time:" <<
                Simulator::Now().GetNanoSeconds() << "ns");
            Ptr<UbWqe> wqe = ubFunc->CreateWqe(record.sourceNode, record.destNode, record.dataSize, record.taskId);
            ubFunc->PushWqeToJetty(wqe, m_jettyNum);
        }
        m_jettyNum++; // m_jettyNum 在client里是唯一的，不重复的
    } else {
            NS_ASSERT_MSG(0, "TaOpcode Not Exist");
    }
}

void UbApp::OnTaskCompleted(uint32_t taskId, uint32_t jettyNum)
{
    NS_LOG_FUNCTION(this << taskId);
    NS_LOG_INFO("WQE Completes, jettyNum: " << jettyNum << " taskId: " << taskId);
    WqeTaskCompletesNotify(GetNode()->GetId(), jettyNum, taskId);
    NS_LOG_INFO("[APPLICATION INFO] taskId: " << taskId << ",finish time:" << Simulator::Now().GetNanoSeconds() << "ns");
    UbTrafficGen::Get()->OnTaskCompleted(taskId);
}

void UbApp::OnTestTaskCompleted(uint32_t taskId, uint32_t jettyNum)
{
    NS_LOG_FUNCTION(this << taskId);
    NS_LOG_INFO("WQE Completes, jettyNum:" << jettyNum << " taskId:" << taskId);
    WqeTaskCompletesNotify(GetNode()->GetId(), jettyNum, taskId);
    NS_LOG_INFO("[APPLICATION INFO] taskId:" << taskId << ",finish time:" << Simulator::Now().GetNanoSeconds() << "ns");
    Ptr<UbFunction> ubFunc = GetNode()->GetObject<UbController>()->GetUbFunction();
    UbTrafficGen::Get()->OnTaskCompleted(taskId);
}

void UbApp::OnMemTaskCompleted(uint32_t taskId)
{
    NS_LOG_FUNCTION(this << taskId);
    NS_LOG_INFO("MEM Task Completes, taskId: " << taskId);
    MemTaskCompletesNotify(GetNode()->GetId(), taskId);
    NS_LOG_INFO("[APPLICATION INFO] taskId: " << taskId << ",finish time:" << Simulator::Now().GetNanoSeconds() << "ns");
    UbTrafficGen::Get()->OnTaskCompleted(taskId);
}

void UbApp::MemTaskStartsNotify(uint32_t nodeId, uint32_t taskId)
{
    m_traceMemTaskStartsNotify(nodeId, taskId);
}

void UbApp::MemTaskCompletesNotify(uint32_t nodeId, uint32_t taskId)
{
    m_traceMemTaskCompletesNotify(nodeId, taskId);
}

void UbApp::WqeTaskStartsNotify(uint32_t nodeId, uint32_t jettyNum, uint32_t taskId)
{
    m_traceWqeTaskStartsNotify(nodeId, jettyNum, taskId);
}

void UbApp::WqeTaskCompletesNotify(uint32_t nodeId, uint32_t jettyNum, uint32_t taskId)
{
    m_traceWqeTaskCompletesNotify(nodeId, jettyNum, taskId);
}

void UbApp::CreateTPs(uint32_t src, uint32_t dst, uint32_t priority, std::vector<uint32_t> &tpns)
{
    // 目标节点+端口的ip地址，出端口和跳数的pair的vector
    std::map<Ipv4Address, std::vector<std::pair<uint16_t, uint32_t>>> routingEntries;
    // 查找路由，寻找从本节点到目的节点的路径
    Ptr<UbRoutingProcess> rt = GetNode()->GetObject<UbSwitch>()->GetRoutingProcess();
    for (uint32_t dstPort = 0; dstPort < NodeList::GetNode(dst)->GetNDevices(); dstPort++) {
        // 查找去往该ip的出端口，若非空则记录
        Ipv4Address dstPortIp = NodeIdToIp(dst, dstPort);
        std::vector<uint16_t> shortestOutPortsVec = rt->GetShortestOutPorts(dstPortIp.Get());
        std::vector<uint16_t> otherOutPortsVec = rt->GetOtherOutPorts(dstPortIp.Get());
        if (!shortestOutPortsVec.empty()) {
            routingEntries[dstPortIp] = std::vector<std::pair<uint16_t, uint32_t>>();
            for (uint16_t outPort : shortestOutPortsVec) {
                routingEntries[dstPortIp].push_back(std::make_pair(outPort, 0));
            }
        }
        // 仅在开启非最短路模式的情况下加入非最短路由
        if (!otherOutPortsVec.empty() && !m_useShortestPaths) {
            routingEntries[dstPortIp] = std::vector<std::pair<uint16_t, uint32_t>>();
            for (uint16_t outPort : otherOutPortsVec) {
                routingEntries[dstPortIp].push_back(std::make_pair(outPort, 1));
            }
        }
    }
    // 如果为空，则判错，路由有问题。不考虑省略host路由的情况
    if (routingEntries.empty()) {
        NS_ASSERT_MSG(0, "Invalid routing! Traffic can't arrive!");
    }
    uint32_t pathNum = 0;
    for (auto it = routingEntries.begin(); it != routingEntries.end(); it++) {
        pathNum += it->second.size();
    }
    NS_LOG_DEBUG("Paths num:" << pathNum);
    // 根据随机数决定使用哪条TP
    uint32_t idx = (int32_t)(m_random->GetValue() * pathNum);
    uint32_t id = 0;
    bool finish = false;
    for (auto it = routingEntries.begin(); it != routingEntries.end(); it++) {
        if (finish) {
            break;
        }
        Ipv4Address dstIp = it->first;
        uint32_t dstPort = IpToPortId(dstIp);
        for (auto outPortIt = it->second.begin(); outPortIt != it->second.end(); outPortIt++) {
            if (m_multiPathEnable) { // 多路径模式下，创建全部TP
                CreateTP(src, dst, outPortIt->first, dstPort, priority,
                         TpConnectionManager::GetNextTpn(src),
                         TpConnectionManager::GetNextTpn(dst), outPortIt->second, tpns);
            } else if (id == idx) { // 单路径模式下，仅创建一个TP
                CreateTP(src, dst, outPortIt->first, dstPort, priority,
                         TpConnectionManager::GetNextTpn(src),
                         TpConnectionManager::GetNextTpn(dst), outPortIt->second, tpns);
                NS_LOG_DEBUG("random res:" << idx << " Create TP tpn:" << tpns.back());
                finish = true;
                break;
            }
            id++;
        }
    }
}

void UbApp::CreateTP(uint32_t src, uint32_t dst, uint8_t sport,
                     uint8_t dport, UbPriority priority, uint32_t srcTpn,
                     uint32_t dstTpn, uint32_t metric, std::vector<uint32_t> &tpns)
{
    Connection conn;
    conn.node1      = src;
    conn.port1      = sport;
    conn.tpn1       = srcTpn;
    conn.node2      = dst;
    conn.port2      = dport;
    conn.tpn2       = dstTpn;
    conn.priority   = priority;
    conn.metrics    = metric;

    Ptr<Node> sN = NodeList::GetNode(conn.node1);
    Ptr<Node> rN = NodeList::GetNode(conn.node2);

    Ptr<ns3::UbController> sendCtrl = sN->GetObject<ns3::UbController>();
    Ptr<ns3::UbController> receiveCtrl = rN->GetObject<ns3::UbController>();

    auto sendHostCaqm = UbCongestionControl::Create(UB_DEVICE);
    auto recvHostCaqm = UbCongestionControl::Create(UB_DEVICE);
    bool retSendCtrl = sendCtrl->CreateTp(
        conn.node1, conn.node2, conn.port1, conn.port2, conn.priority, conn.tpn1, conn.tpn2, sendHostCaqm);
    bool retReceiveCtrl = receiveCtrl->CreateTp(
        conn.node2, conn.node1, conn.port2, conn.port1, conn.priority, conn.tpn2, conn.tpn1, recvHostCaqm);
    if (!retSendCtrl || !retReceiveCtrl) {
        NS_ASSERT_MSG(0, "CreateTp failed!");
    }
    // 为两个tp绑定trace回调函数
    utils::UbUtils::Get()->SingleTpTraceConnect(conn.node1, conn.tpn1);
    utils::UbUtils::Get()->SingleTpTraceConnect(conn.node2, conn.tpn2);
    // connection添加tpnConn
    GetNode()->GetObject<UbController>()->GetTpConnManager().AddConnection(conn);
    NodeList::GetNode(dst)->GetObject<UbController>()->GetTpConnManager().AddConnection(conn);
    tpns.push_back(conn.tpn1);
}

} // namespace ns3
