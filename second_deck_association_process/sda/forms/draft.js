"use strict";

function buildDraftFormItems_(form) {
  var draftResults = fetchJson("draft_results.json");
  var capState = fetchJson("cap_state.json");
  var teamNames = formTeamNames_();
  var teamPage = {};
  var teamItem = form.addListItem().setTitle("Team").setRequired(true);

  teamNames.forEach(function (teamName) {
    var teamId = _teamIdForName_(teamName, capState);
    var picks = (draftResults && draftResults[teamId]) || [];
    var section = form.addPageBreakItem().setTitle(teamName + " / draft picks");
    section.setGoToPage(FormApp.PageNavigationType.SUBMIT);
    teamPage[teamName] = section;
    picks.forEach(function (pick) {
      var pickLabel = pick.pick || pick.round || "?";
      var playerName = pick.player_name || pick.name || "Unknown player";
      form.addTextItem()
        .setTitle("Pick " + pickLabel + ": " + playerName + " — years")
        .setRequired(true)
        .setValidation(FormApp.createTextValidation().requireTextMatchesPattern("^[1-7]$").build());
    });
  });
  teamItem.setChoices(teamNames.map(function (teamName) {
    return teamItem.createChoice(teamName, teamPage[teamName]);
  }));
}

function generateDraftFormLinks() {
  var form = buildDraftForm();
  var capState = fetchJson("cap_state.json");
  var links = {};
  formTeamNames_().forEach(function (teamName) {
    var teamItem = form.getItems(FormApp.ItemType.LIST).map(function (item) { return item.asListItem(); })
      .filter(function (item) { return item.getTitle() === "Team"; })[0];
    var response = form.createResponse().withItemResponse(teamItem.createResponse(teamName));
    var teamId = _teamIdForName_(teamName, capState);
    links[teamId || teamName] = response.toPrefilledUrl();
  });
  return links;
}

function _teamIdForName_(teamName, capState) {
  var teams = (capState && capState.teams) || {};
  var normalized = normalizeName(teamName);
  return Object.keys(teams).filter(function (teamId) {
    return normalizeName(teams[teamId].team_name) === normalized;
  })[0] || null;
}