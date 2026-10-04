"use strict";

function installSubmitTriggers() {
  var forms = {
    signing: FormApp.openById(_formId_("signing")),
    cap_trade: FormApp.openById(_formId_("cap_trade")),
    draft: FormApp.openById(_formId_("draft")),
  };
  var handlers = ["onSigningSubmit", "onCapTradeSubmit", "onDraftSubmit"];
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (handlers.indexOf(trigger.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger("onSigningSubmit").forForm(forms.signing).onFormSubmit().create();
  ScriptApp.newTrigger("onCapTradeSubmit").forForm(forms.cap_trade).onFormSubmit().create();
  ScriptApp.newTrigger("onDraftSubmit").forForm(forms.draft).onFormSubmit().create();
  return "Installed three form submit triggers";
}

function onSigningSubmit(event) {
  _processSingleSubmission_(event, "signing");
}

function onCapTradeSubmit(event) {
  _processSingleSubmission_(event, "cap_trade");
}

function onDraftSubmit(event) {
  var response = event && event.response;
  if (!response) return;
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var state = _responseState_("draft", response);
    if (state.relayedAt) return;
    var answers = _answers_(response);
    var team = answers.Team;
    if (_hasPriorDraftSubmission_(state.sheet, state.row, team, state.columns)) {
      _writeResponseState_("draft", response, "REJECTED", "duplicate-team-submission", "", "");
      _emailRejection_(response, "This team already submitted draft contracts.");
      return;
    }
    var submittedAt = response.getTimestamp();
    var playersDb = fetchJson("players.json");
    var capState = JSON.parse(JSON.stringify(fetchJson("cap_state.json")));
    var proposed = [];
    var errors = [];
    Object.keys(answers).forEach(function (question) {
      var pickMatch = /^Pick (\d+): (.+) — years$/.exec(question);
      if (!pickMatch || answers[question] === "") return;
      var player = matchPlayer(pickMatch[2], playersDb);
      var years = checkYears(answers[question], "drafted");
      if (!player.fantrax_id) errors.push("unknown-player:" + pickMatch[2]);
      if (!years.passed) errors.push(years.reason + ":" + question);
      if (player.fantrax_id && years.passed) {
        var cap = checkCapRoom(team, years.years, capState);
        if (!cap.passed) errors.push(cap.reason + ":" + question + ":" + cap.detail);
        else _deductCap_(team, years.years, capState);
        proposed.push({ player: player.name || pickMatch[2], years: years.years });
      }
    });
    if (!team || !proposed.length) errors.push("draft-picks-unresolved");
    if (errors.length) {
      _writeResponseState_("draft", response, "REJECTED", errors.join("; "), state.ref, "");
      _emailRejection_(response, errors.join("; "));
      return;
    }
    var refs = state.ref ? _parseRefs_(state.ref) : [];
    while (refs.length < proposed.length) refs.push(Utilities.getUuid());
    _writeResponseState_("draft", response, "ACCEPTED", "", JSON.stringify(refs), "");
    proposed.forEach(function (pick, index) {
      relayToDiscord(buildSigningRelay({
        team: team,
        player: pick.player,
        years: pick.years,
        acquisition_type: "drafted",
      }, submittedAt, refs[index]));
    });
    _writeResponseState_("draft", response, "ACCEPTED", "", JSON.stringify(refs), new Date());
  } catch (error) {
    _rejectWithError_("draft", response, error);
  } finally {
    lock.releaseLock();
  }
}

function _processSingleSubmission_(event, kind) {
  var response = event && event.response;
  if (!response) return;
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var state = _responseState_(kind, response);
    if (state.relayedAt) return;
    var answers = _answers_(response);
    var submittedAt = response.getTimestamp();
    var ref = state.ref || Utilities.getUuid();
    var rawRefValue = ref;
    var validationErrors = [];
    var relayMessage;
    if (kind === "signing") {
      var playersDb = fetchJson("players.json");
      var capState = fetchJson("cap_state.json");
      var player = matchPlayer(answers["Player name"], playersDb);
      var years = checkYears(answers["Contract years"], answers["Acquisition type"]);
      if (!player.fantrax_id) validationErrors.push("unknown-player");
      if (!years.passed) validationErrors.push(years.reason);
      if (player.fantrax_id && years.passed) {
        var cap = checkCapRoom(answers.Team, years.years, capState);
        if (!cap.passed) validationErrors.push(cap.reason + ":" + cap.detail);
      }
      if (!answers.Team) validationErrors.push("team-required");
      if (validationErrors.length) {
        _writeResponseState_(kind, response, "REJECTED", validationErrors.join("; "), ref, "");
        _emailRejection_(response, validationErrors.join("; "));
        return;
      }
      rawRefValue = ref;
      relayMessage = buildSigningRelay({
        team: answers.Team,
        player: player.name,
        years: years.years,
        acquisition_type: answers["Acquisition type"],
      }, submittedAt, ref);
    } else {
      var stateData = fetchJson("cap_state.json");
      var sender = answers["Sending team"];
      var receiver = answers["Receiving team"];
      var amount = Number(answers.Years);
      var senderEntry = findCapTeam(sender, stateData);
      var receiverEntry = findCapTeam(receiver, stateData);
      if (!senderEntry || !receiverEntry) validationErrors.push("unknown-team");
      if (normalizeName(sender) === normalizeName(receiver)) validationErrors.push("same-sender-and-receiver");
      if (!Number.isFinite(amount) || amount <= 0) validationErrors.push("invalid-trade-years");
      if (validationErrors.length) {
        _writeResponseState_(kind, response, "REJECTED", validationErrors.join("; "), ref, "");
        _emailRejection_(response, validationErrors.join("; "));
        return;
      }
      relayMessage = buildCapTradeRelay({ from_team: sender, to_team: receiver, years: amount }, submittedAt, ref);
    }
    _writeResponseState_(kind, response, "ACCEPTED", "", rawRefValue, "");
    relayToDiscord(relayMessage);
    _writeResponseState_(kind, response, "ACCEPTED", "", rawRefValue, new Date());
  } catch (error) {
    _rejectWithError_(kind, response, error);
  } finally {
    lock.releaseLock();
  }
}

function _answers_(response) {
  var result = {};
  response.getItemResponses().forEach(function (itemResponse) {
    result[itemResponse.getItem().getTitle()] = itemResponse.getResponse();
  });
  return result;
}

function _responseState_(kind, response) {
  var sheet = _responseSheet_(kind);
  var auditColumns = _ensureAuditColumns_(sheet);
  var row = _findResponseRow_(sheet, response);
  var values = sheet.getRange(row, 1, 1, sheet.getLastColumn()).getValues()[0];
  return {
    sheet: sheet,
    row: row,
    columns: auditColumns,
    ref: values[auditColumns.discord_ref - 1] || "",
    relayedAt: values[auditColumns.relayed_at - 1] || "",
  };
}

function _responseSheet_(kind) {

  function _hasPriorDraftSubmission_(sheet, currentRow, team, columns) {
    var lastRow = sheet.getLastRow();
    if (lastRow < 3) return false;
    var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    var teamColumn = headers.indexOf("Team") + 1;
    if (!teamColumn) return false;
    var rows = sheet.getRange(2, 1, lastRow - 1, sheet.getLastColumn()).getValues();
    return rows.some(function (row, index) {
      return index + 2 !== currentRow
        && normalizeName(row[teamColumn - 1]) === normalizeName(team)
        && row[columns.status - 1] === "ACCEPTED";
    });
  }
  var id = scriptProperties_().getProperty("SDA_RESPONSE_SHEET_" + kind);
  if (!id) throw new Error("response-sheet-unavailable");
  return SpreadsheetApp.openById(id).getSheets()[0];
}

function _ensureAuditColumns_(sheet) {
  var width = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(1, 1, 1, width).getValues()[0];
  ["status", "reject_reason", "relayed_at", "discord_ref"].forEach(function (name) {
    if (headers.indexOf(name) < 0) {
      headers.push(name);
      sheet.getRange(1, headers.length).setValue(name);
    }
  });
  return {
    status: headers.indexOf("status") + 1,
    reject_reason: headers.indexOf("reject_reason") + 1,
    relayed_at: headers.indexOf("relayed_at") + 1,
    discord_ref: headers.indexOf("discord_ref") + 1,
  };
}

function _findResponseRow_(sheet, response) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) throw new Error("response-row-unavailable");
  var target = response.getTimestamp().getTime();
  var timestamps = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  for (var index = timestamps.length - 1; index >= 0; index -= 1) {
    if (timestamps[index][0] instanceof Date && timestamps[index][0].getTime() === target) return index + 2;
  }
  return lastRow;
}

function _writeResponseState_(kind, response, status, reason, ref, relayedAt) {
  var state = _responseState_(kind, response);
  state.sheet.getRange(state.row, state.columns.status).setValue(status);
  state.sheet.getRange(state.row, state.columns.reject_reason).setValue(reason || "");
  state.sheet.getRange(state.row, state.columns.discord_ref).setValue(ref || "");
  if (relayedAt) state.sheet.getRange(state.row, state.columns.relayed_at).setValue(relayedAt);
}

function _emailRejection_(response, reason) {
  var email = response.getRespondentEmail();
  if (!email) return;
  try {
    MailApp.sendEmail(
      email,
      "Second Deck Association form needs correction",
      "Your submission was not relayed: " + reason + ". Please correct the form and submit again."
    );
  } catch (_error) {
    Logger.log("Could not deliver the form rejection email.");
  }
}

function _rejectWithError_(kind, response, error) {
  var reason = String(error && error.message || "validation-error");
  if (reason.indexOf("data-unavailable") >= 0) reason = "data-unavailable";
  _writeResponseState_(kind, response, "REJECTED", reason, "", "");
  _emailRejection_(response, reason);
}

function _deductCap_(team, years, capState) {
  var entry = findCapTeam(team, capState);
  if (entry) entry.remaining = Number(entry.remaining) - Number(years);
}

function _parseRefs_(value) {
  try {
    var parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [String(value)];
  } catch (_error) {
    return value ? [String(value)] : [];
  }
}