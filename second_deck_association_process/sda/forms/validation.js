"use strict";

function normalizeName(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("en-US")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function matchPlayer(name, playersDb) {
  if (!playersDb || typeof playersDb !== "object") {
    return { fantrax_id: null, confidence: "none", name: null };
  }
  var normalized = normalizeName(name);
  var players = playersDb.players || {};
  var exactEntry = Object.keys(players).find(function (playerName) {
    return normalizeName(playerName) === normalized;
  });
  if (exactEntry) {
    return {
      fantrax_id: String(players[exactEntry]),
      confidence: "exact",
      name: exactEntry,
    };
  }
  var aliases = playersDb.aliases || {};
  var aliasEntry = Object.keys(aliases).find(function (alias) {
    return normalizeName(alias) === normalized;
  });
  if (!aliasEntry) {
    return { fantrax_id: null, confidence: "none", name: null };
  }
  var playerId = String(aliases[aliasEntry]);
  var canonicalName = Object.keys(players).find(function (playerName) {
    return String(players[playerName]) === playerId;
  });
  return { fantrax_id: playerId, confidence: "alias", name: canonicalName || aliasEntry };
}

function checkYears(years, acquisitionType) {
  var numericYears = Number(years);
  var type = String(acquisitionType || "").toLowerCase();
  if (!Number.isFinite(numericYears) || !Number.isInteger(numericYears)) {
    return { passed: false, reason: "years-must-be-a-finite-integer" };
  }
  if (["drafted", "called_up"].indexOf(type) < 0 && type !== "waiver") {
    return { passed: false, reason: "unknown-acquisition-type" };
  }
  var maximum = type === "waiver" ? 3 : 7;
  if (numericYears < 1 || numericYears > maximum) {
    return {
      passed: false,
      reason: "years-out-of-range",
      detail: type + " contracts must be 1-" + maximum + " years",
    };
  }
  return { passed: true, years: numericYears, acquisition_type: type };
}

function findCapTeam(team, capState) {
  if (!capState || !capState.teams) return null;
  var normalized = normalizeName(team);
  var teamId = Object.keys(capState.teams).find(function (id) {
    var entry = capState.teams[id];
    return String(id) === String(team) || normalizeName(entry.team_name) === normalized;
  });
  return teamId ? capState.teams[teamId] : null;
}

function checkCapRoom(team, years, capState) {
  var entry = findCapTeam(team, capState);
  var numericYears = Number(years);
  if (!entry || !Number.isFinite(numericYears)) {
    return { passed: false, reason: "team-or-cap-data-unavailable" };
  }
  var remaining = Number(entry.remaining);
  if (!Number.isFinite(remaining)) {
    return { passed: false, reason: "team-or-cap-data-unavailable" };
  }
  return {
    passed: numericYears <= remaining,
    reason: numericYears <= remaining ? "cap-room-available" : "insufficient-cap-room",
    detail: "Need " + numericYears + " years; " + remaining + " remain.",
    remaining: remaining,
    effective_cap: Number(entry.effective_cap),
    committed_years: Number(entry.committed_years),
  };
}

if (typeof module !== "undefined") {
  module.exports = { normalizeName, matchPlayer, checkYears, findCapTeam, checkCapRoom };
}
