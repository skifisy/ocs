// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/ub-utils.h"
#include <chrono>

using namespace utils;

void CheckExampleProcess()
{
    if (!UbTrafficGen::Get()->IsCompleted()) {
            Simulator::Schedule(MicroSeconds(100), &CheckExampleProcess);
            return;
    }
    Simulator::Stop();
    return;
}

// 根据配置文件路径执行用例
void RunCase(const string& configPath)
{
    RngSeedManager::SetSeed(10);
    string LoadConfigFilePath = configPath + "/network_attribute.txt";
    UbUtils::Get()->SetComponentsAttribute(LoadConfigFilePath);
    UbUtils::Get()->CreateTraceDir();
    string NodeConfigFile = configPath + "/node.csv";
    UbUtils::Get()->CreateNode(NodeConfigFile);
    string TopoConfigFile = configPath + "/topology.csv";
    UbUtils::Get()->CreateTopo(TopoConfigFile);
    string RouterConfigFile = configPath + "/routing_table.csv";
    UbUtils::Get()->AddRoutingTable(RouterConfigFile);
    string TpConfigFile = configPath + "/transport_channel.csv";
    TpConnectionManager retConnectionManager = UbUtils::Get()->CreateTp(TpConfigFile);
    UbUtils::Get()->TopoTraceConnect();
    string TrafficConfigFile = configPath + "/traffic.csv";
    auto trafficData = UbUtils::Get()->ReadTrafficCSV(TrafficConfigFile);

    BooleanValue gFaultEnable;
    UbUtils::Get()->g_fault_enable.GetValue(gFaultEnable);
    if (gFaultEnable.Get()) {
        string FaultConfigFile = configPath + "/fault.csv";
        UbUtils::Get()->InitFaultMoudle(FaultConfigFile);
    }
    // 遍历Traffic数据，并启动client
    UbUtils::Get()->PrintTimestamp ("Start Client.");
    for (auto& record : trafficData) {
        auto node = NodeList::GetNode (record.sourceNode);
        if (node->GetNApplications()==0) {
            Ptr<UbApp> client = CreateObject<UbApp>();
            node->AddApplication (client);
            UbUtils::Get()->ClientTraceConnect(record.sourceNode);
        }
        UbTrafficGen::Get()->AddTask(record);
        Ptr<ns3::UbApp> client = DynamicCast<ns3::UbApp>(node->GetApplication(0));
        client->GetTpnConn(retConnectionManager.GetConnectionManagerByNode(record.sourceNode));
    }
    UbTrafficGen::Get()->ScheduleNextTasks();
    CheckExampleProcess();
}

// 根据配置文件路径执行用例
int main(int argc, char* argv[])
{
    if (UbUtils::Get()->QueryAttributeInfor(argc, argv))
        return 0;
    // 开始计时
    auto start = std::chrono::high_resolution_clock::now();
    Time::SetResolution(Time::NS);

    // 日志中添加时间前缀
    //ns3::LogComponentEnableAll(LOG_PREFIX_TIME);

    // 示例：设置指定组件日志级别，设置指定组件打印时间前缀
    // LogComponentEnable("UbApp", LOG_LEVEL_INFO);
    // LogComponentEnable("UbApp", LOG_PREFIX_TIME);

    // 激活日志
    // LogComponentEnable("UbSwitchAllocator", LOG_LEVEL_ALL);
    // LogComponentEnable("UbQueueManager", LOG_LEVEL_ALL);
    // LogComponentEnable("UbCaqm", LOG_LEVEL_ALL);
    // LogComponentEnable("UbTrafficGen", LOG_LEVEL_ALL);
    // LogComponentEnable("UbApp", LOG_LEVEL_ALL);
    // LogComponentEnable("UbCongestionControl", LOG_LEVEL_ALL);
    // LogComponentEnable("UbController", LOG_LEVEL_ALL);
    // LogComponentEnable("UbDataLink", LOG_LEVEL_ALL);
    // LogComponentEnable("UbFlowControl", LOG_LEVEL_ALL);
    // LogComponentEnable("UbHeader", LOG_LEVEL_ALL);
    // LogComponentEnable("UbLink", LOG_LEVEL_ALL);
    // LogComponentEnable("UbApiLdstThread", LOG_LEVEL_ALL);
    // LogComponentEnable("UbApiLdst", LOG_LEVEL_ALL);
    // LogComponentEnable("UbPort", LOG_LEVEL_ALL);
    // LogComponentEnable("UbRoutingProcess", LOG_LEVEL_ALL);
    // LogComponentEnable("UbSwitch", LOG_LEVEL_ALL);
    // LogComponentEnable("UbFunction", LOG_LEVEL_ALL);
    // LogComponentEnable("UbTransportChannel", LOG_LEVEL_ALL);
    // LogComponentEnable("UbFault", LOG_LEVEL_ALL);
    // LogComponentEnable("UbTransaction", LOG_LEVEL_ALL);

    // 配置文件路径
    string configPath = "scratch/2nodes_single-tp";
    if (argc > 1)
    {
        configPath = string(argv[1]);
    }

    // 读取配置文件并执行用例
    string runCase = "Run case: " + configPath;
    UbUtils::Get()->PrintTimestamp(runCase);
    RunCase(configPath);

    Simulator::Run();
    UbUtils::Get()->Destroy();
    Simulator::Destroy();
    UbUtils::Get()->PrintTimestamp("Simulator finished!");
    UbUtils::Get()->ParseTrace();

    auto end = std::chrono::high_resolution_clock::now();
    // 计算持续时间
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
    UbUtils::Get()->PrintTimestamp("Program finished.");
    string runTime = "程序运行时间: " + to_string(duration.count()) + " 毫秒";
    UbUtils::Get()->PrintTimestamp(runTime);
    return 0;
}