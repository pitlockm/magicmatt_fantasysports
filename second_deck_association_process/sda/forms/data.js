"use strict";

function fetchJson(name) {
  if (["players.json", "cap_state.json", "draft_results.json", "recent_adds.json"].indexOf(name) < 0) {
    throw new Error("data-unavailable");
  }
  var cache = CacheService.getScriptCache();
  var cacheKey = "sda-api:" + name;
  var cached = cache.get(cacheKey);
  if (cached) {
    try {
      return JSON.parse(cached);
    } catch (_error) {
      cache.remove(cacheKey);
    }
  }
  try {
    var response = UrlFetchApp.fetch(SDA_CONFIG.SITE_BASE_URL + "/api/" + name, {
      muteHttpExceptions: true,
      followRedirects: true,
    });
    if (response.getResponseCode() !== 200) throw new Error("data-unavailable");
    var result = JSON.parse(response.getContentText());
    cache.put(cacheKey, JSON.stringify(result), 3600);
    return result;
  } catch (_error) {
    throw new Error("data-unavailable");
  }
}