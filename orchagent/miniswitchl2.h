#pragma once

// Explicit, bounded development profile. This does not emulate missing hardware.
#include <cstdlib>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace swss { class DBConnector; }

namespace miniswitch
{
using Row = std::map<std::string, std::string>;
using Config = std::map<std::string, Row>;

inline bool enabled()
{
    const char *value = std::getenv("SONIC_MINISWITCH_L2");
    if (!value || std::string(value) == "0") return false;
    if (std::string(value) == "1") return true;
    throw std::invalid_argument("SONIC_MINISWITCH_L2 must be exactly 0 or 1");
}

inline bool physicalPort(const std::string &name)
{
    return name == "Ethernet0" || name == "Ethernet4" || name == "Ethernet8";
}

inline std::string value(const Row &row, const std::string &name)
{
    const auto it = row.find(name);
    return it == row.end() ? std::string() : it->second;
}

inline bool vlanName(const std::string &name)
{
    if (name.size() < 5 || name.substr(0, 4) != "Vlan") return false;
    const auto digits = name.substr(4);
    if (digits.front() == '0' || digits.find_first_not_of("0123456789") != std::string::npos) return false;
    try
    {
        const auto vid = std::stoul(digits);
        return vid >= 2 && vid <= 4094 && std::to_string(vid) == digits;
    }
    catch (const std::exception &) { return false; }
}

inline bool macAddress(const std::string &mac)
{
    if (mac.size() != 17) return false;
    for (size_t i = 0; i < mac.size(); ++i)
    {
        if (i % 3 == 2) { if (mac[i] != ':') return false; }
        else if (std::string("0123456789abcdefABCDEF").find(mac[i]) == std::string::npos) return false;
    }
    // This hardware's FDB is unicast only.
    return std::string("02468aAcCeE").find(mac[1]) != std::string::npos;
}

inline std::string portUpdateError(const std::string &key, const std::string &op, const Row &row)
{
    if (op != "SET") return "Fixed physical ports and initialization markers cannot be removed";
    if (key == "PortConfigDone")
        return row.size() == 1 && value(row, "count") == "3" ? "" : "PortConfigDone requires count=3";
    if (key == "PortInitDone")
        return row.size() == 1 && value(row, "lanes") == "0" ? "" : "Invalid portsyncd PortInitDone marker";
    if (!physicalPort(key)) return "Unsupported physical port: " + key;
    const std::map<std::string, std::string> fixed = {
        {"speed", "100"}, {"mtu", "1500"}, {"fec", "none"}, {"autoneg", "off"},
        {"lanes", key == "Ethernet0" ? "0" : key == "Ethernet4" ? "1" : "2"}
    };
    for (const auto &field : row)
    {
        const auto expected = fixed.find(field.first);
        if (expected != fixed.end() && field.second != expected->second) return "Unsupported fixed port setting: " + field.first;
        if (expected == fixed.end() && field.first != "admin_status" && field.first != "alias" &&
            field.first != "index" && field.first != "description") return "Unsupported port field: " + field.first;
        if (field.first == "admin_status" && field.second != "up" && field.second != "down") return "Invalid admin_status";
    }
    return "";
}

inline std::vector<std::string> validate(const Config &config)
{
    std::vector<std::string> errors;
    const std::set<std::string> passive = {
        "MGMT_INTERFACE", "MGMT_PORT", "MGMT_ROUTE", "MGMT_VRF_CONFIG",
        "DNS_NAMESERVER", "NTP_SERVER", "NTP", "SYSLOG_SERVER", "VERSIONS", "CONSOLE_SWITCH"
    };
    const std::map<std::string, std::string> lanes = {
        {"Ethernet0", "0"}, {"Ethernet4", "1"}, {"Ethernet8", "2"}
    };
    const std::set<std::string> portFields = {
        "lanes", "alias", "index", "speed", "mtu", "fec", "autoneg", "admin_status", "description"
    };
    const auto metadata = config.find("DEVICE_METADATA|localhost");
    if (metadata == config.end() || value(metadata->second, "hwsku") != "MiniSwitch3x100" ||
        value(metadata->second, "switch_type") != "npu" || value(metadata->second, "platform") != "miniswitch")
        errors.push_back("DEVICE_METADATA: require MiniSwitch3x100/miniswitch/npu");
    size_t ports = 0, vlans = 0, fdb = 0;
    std::set<std::string> untagged;
    for (const auto &entry : config)
    {
        const auto sep = entry.first.find('|');
        if (sep == std::string::npos) { errors.push_back("Unsupported CONFIG_DB key: " + entry.first); continue; }
        const auto table = entry.first.substr(0, sep);
        const auto key = entry.first.substr(sep + 1);
        const auto &row = entry.second;
        if (passive.count(table)) continue;
        if (table == "DEVICE_METADATA")
        {
            if (key != "localhost") errors.push_back("Only local DEVICE_METADATA is supported");
            const std::set<std::string> fields = {"hostname", "hwsku", "platform", "type", "switch_type", "mac"};
            for (const auto &field : row)
                if (!fields.count(field.first)) errors.push_back("Unsupported metadata field: " + field.first);
        }
        else if (table == "PORT")
        {
            ++ports;
            const auto lane = lanes.find(key);
            if (lane == lanes.end()) { errors.push_back("Unsupported fixed port: " + key); continue; }
            if (value(row, "lanes") != lane->second || value(row, "speed") != "100" ||
                value(row, "mtu") != "1500" || value(row, "fec") != "none" || value(row, "autoneg") != "off")
                errors.push_back("Unsupported fixed lane/speed/MTU/FEC/autoneg for " + key);
            if (value(row, "admin_status") != "up" && value(row, "admin_status") != "down")
                errors.push_back("Explicit up/down admin_status required for " + key);
            for (const auto &field : row)
                if (!portFields.count(field.first)) errors.push_back("Unsupported port field: " + field.first);
        }
        else if (table == "VLAN")
        {
            ++vlans;
            if (!vlanName(key) || value(row, "vlanid") != key.substr(4)) errors.push_back("Unsupported VLAN: " + key);
            const std::set<std::string> fields = {"vlanid", "members", "members@", "description", "mtu"};
            for (const auto &field : row)
                if (!fields.count(field.first)) errors.push_back("Unsupported VLAN field: " + field.first);
            if (row.count("mtu") && value(row, "mtu") != "1500") errors.push_back("VLAN MTU must be 1500");
        }
        else if (table == "VLAN_MEMBER")
        {
            const auto memberSep = key.find('|');
            const auto vlan = key.substr(0, memberSep);
            const auto port = memberSep == std::string::npos ? std::string() : key.substr(memberSep + 1);
            if (!config.count("VLAN|" + vlan) || !physicalPort(port)) errors.push_back("Unknown VLAN/member: " + key);
            const auto mode = value(row, "tagging_mode");
            if (row.size() != 1 || (mode != "tagged" && mode != "untagged")) errors.push_back("Unsupported VLAN member attributes: " + key);
            if (mode == "untagged" && !untagged.insert(port).second) errors.push_back("Multiple PVIDs for " + port);
        }
        else if (table == "FDB")
        {
            ++fdb;
            const auto fdbSep = key.find('|');
            const auto vlan = key.substr(0, fdbSep);
            const auto mac = fdbSep == std::string::npos ? std::string() : key.substr(fdbSep + 1);
            if (!config.count("VLAN|" + vlan) || !macAddress(mac) || !physicalPort(value(row, "port")) || value(row, "type") != "static")
                errors.push_back("Unsupported static FDB entry: " + key);
            for (const auto &field : row)
                if (field.first != "port" && field.first != "type") errors.push_back("Unsupported FDB field: " + field.first);
        }
        else if (table == "FEATURE")
        {
            const auto state = value(row, "state");
            const bool retained = key == "swss" || key == "syncd" || key == "lldp";
            if (state != "disabled" && !(retained && state == "enabled")) errors.push_back("Unsupported enabled service: " + key);
            for (const auto &field : row)
                if (field.first != "state") errors.push_back("Unsupported FEATURE field: " + field.first);
        }
        else errors.push_back("Unsupported CONFIG_DB table: " + table);
    }
    if (ports != 3) errors.push_back("Exactly three fixed PORT entries required");
    for (const auto &lane : lanes)
        if (!config.count("PORT|" + lane.first)) errors.push_back("Missing fixed port: " + lane.first);
    if (vlans > 3) errors.push_back("Only three user VLANs; VLAN1 consumes the fourth slot");
    if (fdb > 4) errors.push_back("Only four total hardware FDB rows shared with learning");
    return errors;
}

bool validateDatabase(swss::DBConnector *configDb, std::string &reason);
void publishStatus(swss::DBConnector *stateDb, bool accepted, const std::string &reason);
}
