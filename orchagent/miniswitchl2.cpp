#include "miniswitchl2.h"
#include <dbconnector.h>
#include <table.h>
#include <sstream>

namespace miniswitch
{
bool validateDatabase(swss::DBConnector *configDb, std::string &reason)
{
    Config config;
    try
    {
        for (const auto &key : configDb->keys("*"))
        {
            // SONiC's scalar completion markers are not configuration tables.
            if (key == "CONFIG_DB_INITIALIZED" || key == "CONFIG_DB_LOADED") continue;
            config.emplace(key, configDb->hgetall<Row>(key));
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
