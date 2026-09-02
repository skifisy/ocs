// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/ub-utils.h"
#include <chrono>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <ctime>
#include <string>

#ifdef NS3_MTP
#include "ns3/mtp-interface.h"
#endif

using namespace utils;

void CheckExampleProcess()
{
    double sim_time_us = Simulator::Now().GetMicroSeconds();
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm_buf{};
    localtime_r(&t, &tm_buf);
    double val = sim_time_us;
    const char* unit = " us";
    int precision = 0;
    if (sim_time_us >= 1e6) {
        val = sim_time_us / 1e6;
        unit = " s";
        precision = 6;
    } else if (sim_time_us >= 1e3) {
        val = sim_time_us / 1e3;
        unit = " ms";
        precision = 3;
    }
    std::ostringstream oss;
    oss.setf(std::ios::fixed);
    oss << "[" << std::put_time(&tm_buf, "%H:%M:%S") << "] "
        << "Simulation time progress: " << std::setprecision(precision) << val << unit;
    std::cout << "\r" << oss.str() << std::flush;
    if (!UbTrafficGen::Get()->IsCompleted()) {
            Simulator::Schedule(MicroSeconds(100), &CheckExampleProcess);
            return;
    }
    std::cout << std::endl;
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
        Ptr<UbController> ctrl = node->GetObject<ns3::UbController>();
        ctrl->SetTpConnManager(retConnectionManager.GetConnectionManagerByNode(record.sourceNode));
    }
    UbTrafficGen::Get()->ScheduleNextTasks();
    CheckExampleProcess();
}

// 根据配置文件路径执行用例
int main(int argc, char* argv[])
{
    if (UbUtils::Get()->QueryAttributeInfor(argc, argv)) 
        return 0;

    // UNISON示例：多线程加速需要使能mtp ./ns3 configure --enable-mtp
    // 使用如下代码使能UNISON
//     uint32_t mtpThreads = 8;
// #ifdef NS3_MTP
//     MtpInterface::Enable(mtpThreads);
// #endif

    // 开始计时
    auto start = std::chrono::high_resolution_clock::now();
    Time::SetResolution(Time::PS);

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
    // LogComponentEnable("UbLdstInstance", LOG_LEVEL_ALL);
    // LogComponentEnable("UbLdstThread", LOG_LEVEL_ALL);
    // LogComponentEnable("UbLdstApi", LOG_LEVEL_ALL);
    // LogComponentEnable("UbPort", LOG_LEVEL_ALL);
    // LogComponentEnable("UbRoutingProcess", LOG_LEVEL_ALL);
    // LogComponentEnable("UbSwitch", LOG_LEVEL_ALL);
    // LogComponentEnable("UbFunction", LOG_LEVEL_ALL);
    // LogComponentEnable("UbTransportChannel", LOG_LEVEL_ALL);
    // LogComponentEnable("UbFault", LOG_LEVEL_ALL);
    // LogComponentEnable("UbTransaction", LOG_LEVEL_ALL);

    // 配置文件路径
    string configPath = "scratch/2nodes_single-tp";
    if (argc > 1) {
        configPath = string(argv[1]);
    }

    // 读取配置文件并执行用例
    string runCase = "Run case: " + configPath;
    UbUtils::Get()->PrintTimestamp(runCase);
    RunCase(configPath);
    auto sim_wall_start = std::chrono::high_resolution_clock::now();
    Simulator::Run();
    auto sim_wall_end = std::chrono::high_resolution_clock::now();
    UbUtils::Get()->Destroy();
    Simulator::Destroy();
    UbUtils::Get()->PrintTimestamp("Simulator finished!");
    auto trace_wall_start = std::chrono::high_resolution_clock::now();
    UbUtils::Get()->ParseTrace();

    auto end = std::chrono::high_resolution_clock::now();
    UbUtils::Get()->PrintTimestamp("Program finished.");
    // 阶段性挂钟时间统计（单位：秒）
    double config_wall_s = std::chrono::duration_cast<std::chrono::microseconds>(sim_wall_start - start).count() / 1e6;
    double run_wall_s    = std::chrono::duration_cast<std::chrono::microseconds>(sim_wall_end - sim_wall_start).count() / 1e6;
    double trace_wall_s  = std::chrono::duration_cast<std::chrono::microseconds>(end - trace_wall_start).count() / 1e6;
    double total_wall_s  = std::chrono::duration_cast<std::chrono::microseconds>(end - start).count() / 1e6;
    UbUtils::Get()->PrintTimestamp("Wall-clock (config phase): " + std::to_string(config_wall_s) + " s");
    UbUtils::Get()->PrintTimestamp("Wall-clock (run phase): " + std::to_string(run_wall_s) + " s");
    UbUtils::Get()->PrintTimestamp("Wall-clock (trace phase): " + std::to_string(trace_wall_s) + " s");
    UbUtils::Get()->PrintTimestamp("Wall-clock (total): " + std::to_string(total_wall_s) + " s");
    return 0;
}

