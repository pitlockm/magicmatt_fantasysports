"use strict";

var SDA_CONFIG = {
  SITE_BASE_URL: "https://pitlockm.github.io/magicmatt_fantasysports",
  FORM_SCHEMA_VERSION: "2026-10-04-v1",
  TEAM_NAMES: [
    "Boe", "Maloun", "Hoffman", "Pecora", "C. Pelton",
    "W. Pelton", "Pitlock", "Riggen", "A. Rolain", "M. Rolain",
  ],
  SIGNING_FORMAT: "📝 SIGNING: {player} — {years} years — {team}\ntype: {type} · submitted: {submitted} · by: {by} · ref: {ref}",
  CAP_TRADE_FORMAT: "📝 CAP TRADE: {from} sends {years} years to {to}\nsubmitted: {submitted} · by: {by} · ref: {ref}",
  FORM_KEYS: {
    signing: "SDA_SIGNING_FORM_ID",
    cap_trade: "SDA_CAP_TRADE_FORM_ID",
    draft: "SDA_DRAFT_FORM_ID",
  },
};

function scriptProperties_() {
  return PropertiesService.getScriptProperties();
}

function _formId_(kind) {
  var key = SDA_CONFIG.FORM_KEYS[kind];
  var id = key && scriptProperties_().getProperty(key);
  if (!id) throw new Error("form-id-unavailable:" + kind);
  return id;
}

function formTeamNames_() {
  try {
    var state = fetchJson("cap_state.json");
    var names = Object.keys((state && state.teams) || {}).map(function (teamId) {
      return state.teams[teamId].team_name;
    }).filter(Boolean);
    if (names.length) return names;
  } catch (_error) {
    // The prompt's canonical manager labels keep initial form creation possible offline.
  }
  return SDA_CONFIG.TEAM_NAMES.slice();
}