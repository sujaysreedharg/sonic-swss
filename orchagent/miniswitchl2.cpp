#include "miniswitchl2.h"
#include <dbconnector.h>
#include <logger.h>
#include <table.h>
#include <sstream>
extern "C" {
#include <saimetadata.h>
}

namespace miniswitch
{
namespace
{
bool enumName(const sai_enum_metadata_t &metadata, const std::string &name, uint32_t first = 0)
{
    for (uint32_t i = first; i < metadata.valuescount; ++i)
        if (name == metadata.valuesnames[i]) return true;
    return false;
}

bool validateLoggerRow(const std::string &key, const Row &row, std::string &reason)
{
    // swss-common Logger::linkToDbWithOutput creates these operational hashes.
    // syncd also registers every API name after the first enum entry through
    // Syncd::setSaiApiLogLevel, even when the vendor does not implement that API.
    const auto component = key.substr(7); // exact LOGGER| prefix checked by caller
    const std::set<std::string> daemons = {"orchagent", "portsyncd", "portmgrd", "vlanmgrd", "syncd"};
    const bool daemon = daemons.count(component);
    const bool saiApi = enumName(sai_metadata_enum_sai_api_t, component, 1);
    if (!daemon && !saiApi)
        reason = "Unsupported operational LOGGER component: " + component;
    else if (row.size() != 2 || !row.count(swss::DAEMON_LOGLEVEL) || !row.count(swss::DAEMON_LOGOUTPUT))
        reason = "LOGGER requires exactly LOGLEVEL and LOGOUTPUT: " + component;
    else
    {
        const auto level = value(row, swss::DAEMON_LOGLEVEL);
        const auto output = value(row, swss::DAEMON_LOGOUTPUT);
        const bool levelValid = daemon ? swss::Logger::priorityStringMap.count(level) != 0 :
                                        enumName(sai_metadata_enum_sai_log_level_t, level);
        if (!levelValid) reason = "Invalid operational LOGGER LOGLEVEL: " + component;
        else if (!swss::Logger::outputStringMap.count(output))
            reason = "Invalid operational LOGGER LOGOUTPUT: " + component;
        else return true;
    }
    return false;
}
}

bool validateDatabase(swss::DBConnector *configDb, std::string &reason)
{
    Config config;
    try
    {
        for (const auto &key : configDb->keys("*"))
        {
            // SONiC's scalar completion markers are not configuration tables.
            if (key == "CONFIG_DB_INITIALIZED" || key == "CONFIG_DB_LOADED") continue;
            const auto row = configDb->hgetall<Row>(key);
            if (key.compare(0, 7, "LOGGER|") == 0)
            {
                if (!validateLoggerRow(key, row, reason)) return false;
                continue;
            }
            config.emplace(key, row);
        }
        const auto errors = validate(config);
        std::ostringstream text;
        for (size_t i = 0; i < errors.size(); ++i) text << (i ? "; " : "") << errors[i];
        reason = text.str();
        return errors.empty();
    }
    catch (const std::exception &error)
    {
        reason = std::string("CONFIG_DB validation failed: ") + error.what();
        return false;
    }
}

void publishStatus(swss::DBConnector *stateDb, bool accepted, const std::string &reason)
{
    swss::Table status(stateDb, "MINISWITCH_PROFILE");
    status.set("global", {
        {"profile", "fixed-l2"}, {"config_status", accepted ? "accepted" : "rejected"},
        {"reason", reason}, {"recovery", "cold-only"}
    });
}
}
