"use strict";

function buildSigningForm() {
  return _prepareForm_("signing", "SDA — Signing", "Submit a long-term player contract for commissioner review.", function (form) {
    form.setCollectEmail(true);
    form.addListItem().setTitle("Team").setChoiceValues(formTeamNames_()).setRequired(true);
    form.addTextItem().setTitle("Player name").setRequired(true);
    form.addTextItem()
      .setTitle("Contract years")
      .setRequired(true)
      .setValidation(FormApp.createTextValidation().requireTextMatchesPattern("^[1-7]$").build());
    form.addListItem()
      .setTitle("Acquisition type")
      .setChoiceValues(["drafted", "called_up", "waiver"])
      .setRequired(true);
    form.addParagraphTextItem().setTitle("Notes").setRequired(false);
  });
}

function buildCapTradeForm() {
  return _prepareForm_("cap_trade", "SDA — Salary cap trade", "Submit a cap-space transfer for commissioner approval.", function (form) {
    form.setCollectEmail(true);
    form.addListItem().setTitle("Sending team").setChoiceValues(formTeamNames_()).setRequired(true);
    form.addListItem().setTitle("Receiving team").setChoiceValues(formTeamNames_()).setRequired(true);
    form.addTextItem()
      .setTitle("Years")
      .setRequired(true)
      .setValidation(FormApp.createTextValidation().requireNumberGreaterThan(0).build());
    form.addParagraphTextItem().setTitle("Notes").setRequired(false);
  });
}

function buildDraftForm() {
  return _prepareForm_("draft", "SDA — Post-draft contracts", "Enter contract years for your team's draft picks.", function (form) {
    buildDraftFormItems_(form);
  });
}

function buildAllForms() {
  return {
    signing: buildSigningForm().getPublishedUrl(),
    cap_trade: buildCapTradeForm().getPublishedUrl(),
    draft: buildDraftForm().getPublishedUrl(),
  };
}

function _prepareForm_(kind, title, description, buildItems) {
  var properties = scriptProperties_();
  var formId = properties.getProperty(SDA_CONFIG.FORM_KEYS[kind]);
  var form = formId ? FormApp.openById(formId) : FormApp.create(title);
  if (!formId) properties.setProperty(SDA_CONFIG.FORM_KEYS[kind], form.getId());
  form.setTitle(title).setDescription(description).setConfirmationMessage("Your submission is recorded for validation.");
  var schemaKey = "SDA_FORM_SCHEMA_" + kind;
  if (properties.getProperty(schemaKey) !== SDA_CONFIG.FORM_SCHEMA_VERSION) {
    for (var itemIndex = form.getItems().length - 1; itemIndex >= 0; itemIndex -= 1) {
      form.deleteItem(itemIndex);
    }
    buildItems(form);
    properties.setProperty(schemaKey, SDA_CONFIG.FORM_SCHEMA_VERSION);
  }
  _ensureResponseDestination_(kind, form);
  return form;
}

function _ensureResponseDestination_(kind, form) {
  var properties = scriptProperties_();
  var key = "SDA_RESPONSE_SHEET_" + kind;
  var spreadsheetId = properties.getProperty(key);
  if (!spreadsheetId) {
    var spreadsheet = SpreadsheetApp.create("SDA Form Responses — " + kind);
    spreadsheetId = spreadsheet.getId();
    properties.setProperty(key, spreadsheetId);
    form.setDestination(FormApp.DestinationType.SPREADSHEET, spreadsheetId);
  }
  return spreadsheetId;
}