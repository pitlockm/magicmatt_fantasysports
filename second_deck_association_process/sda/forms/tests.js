"use strict";

function assertEq(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error((label || "assertEq") + ": expected " + JSON.stringify(expected) + ", got " + JSON.stringify(actual));
  }
}

function assertThrows(callback, expectedMessage) {
  try {
    callback();
  } catch (error) {
    if (expectedMessage && String(error.message).indexOf(expectedMessage) < 0) throw error;
    return;
  }
  throw new Error("Expected function to throw");
}

function runTests() {
  var playerDb = {
    players: { "Shohei Ohtani": "player-1" },
    aliases: { sho: "player-1" },
  };
  assertEq(matchPlayer("Shohei Ohtani", playerDb).confidence, "exact", "exact name");
  assertEq(matchPlayer("Sho", playerDb).fantrax_id, "player-1", "nickname alias");
  assertEq(matchPlayer("Unknown Player", playerDb).fantrax_id, null, "unknown player");
  ["drafted", "called_up"].forEach(function (type) {
    assertEq(checkYears(1, type).passed, true, type + " lower boundary");
    assertEq(checkYears(7, type).passed, true, type + " upper boundary");
    assertEq(checkYears(8, type).passed, false, type + " over upper boundary");
  });
  assertEq(checkYears(3, "waiver").passed, true, "waiver upper boundary");
  assertEq(checkYears(4, "waiver").passed, false, "waiver over upper boundary");
  assertEq(checkYears(0, "drafted").passed, false, "zero years");
  assertEq(checkYears(1.5, "drafted").passed, false, "fractional contract years");
  var capState = { teams: { "team-1": { team_name: "Owls", remaining: 3, effective_cap: 80, committed_years: 77 } } };
  assertEq(checkCapRoom("Owls", 3, capState).passed, true, "available cap");
  assertEq(checkCapRoom("Owls", 3.5, capState).passed, false, "over cap");
  assertEq(checkYears(2, "unknown").passed, false, "unknown acquisition type");
  assertEq(
    buildSigningRelay(
      { team: "Alpha Owls", player: "Shohei Ohtani", years: 3, acquisition_type: "called_up" },
      "2027-03-15T15:30:00Z",
      "123e4567-e89b-12d3-a456-426614174000"
    ),
    "📝 SIGNING: Shohei Ohtani — 3 years — Alpha Owls\n"
      + "type: called_up · submitted: 2027-03-15T15:30:00.000Z · by: Alpha Owls · ref: 123e4567-e89b-12d3-a456-426614174000",
    "signing relay format"
  );
  assertEq(
    buildCapTradeRelay(
      { from_team: "Alpha Owls", to_team: "Copperheads", years: 2.5 },
      "2027-03-15T15:30:00Z",
      "123e4567-e89b-12d3-a456-426614174001"
    ),
    "📝 CAP TRADE: Alpha Owls sends 2.5 years to Copperheads\n"
      + "submitted: 2027-03-15T15:30:00.000Z · by: Alpha Owls · ref: 123e4567-e89b-12d3-a456-426614174001",
    "cap-trade relay format"
  );
  return "Forms validation tests passed";
}