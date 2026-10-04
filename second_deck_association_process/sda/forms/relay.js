"use strict";

function buildSigningRelay(submission, submittedAt, formRef) {
  var player = String(submission.player || "").trim();
  var team = String(submission.team || "").trim();
  var years = Number(submission.years);
  var acquisitionType = String(submission.acquisition_type || "").trim();
  return SDA_CONFIG.SIGNING_FORMAT
    .replace("{player}", player)
    .replace("{years}", String(years))
    .replace("{team}", team)
    .replace("{type}", acquisitionType)
    .replace("{submitted}", new Date(submittedAt).toISOString())
    .replace("{by}", team)
    .replace("{ref}", formRef || Utilities.getUuid());
}

function buildCapTradeRelay(submission, submittedAt, formRef) {
  var fromTeam = String(submission.from_team || "").trim();
  var toTeam = String(submission.to_team || "").trim();
  var years = Number(submission.years);
  return SDA_CONFIG.CAP_TRADE_FORMAT
    .replace("{from}", fromTeam)
    .replace("{to}", toTeam)
    .replace("{years}", String(years))
    .replace("{submitted}", new Date(submittedAt).toISOString())
    .replace("{by}", fromTeam)
    .replace("{ref}", formRef || Utilities.getUuid());
}

function relayToDiscord(message) {
  var properties = scriptProperties_();
  var dryRun = String(properties.getProperty("DRY_RUN") || "").toLowerCase() === "true";
  if (dryRun) {
    Logger.log("DRY_RUN relay:\n" + message);
    return { relayed: false, dry_run: true };
  }
  var webhookUrl = properties.getProperty("DISCORD_WEBHOOK_URL");
  if (!webhookUrl) throw new Error("webhook-unavailable");
  try {
    var response = UrlFetchApp.fetch(webhookUrl, {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ content: message }),
      muteHttpExceptions: true,
    });
    if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) {
      throw new Error("webhook-delivery-failed");
    }
    return { relayed: true, dry_run: false };
  } catch (_error) {
    throw new Error("webhook-delivery-failed");
  }
}