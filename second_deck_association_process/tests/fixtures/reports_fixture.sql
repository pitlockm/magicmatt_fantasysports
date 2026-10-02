INSERT INTO teams (team_id, team_name, manager) VALUES
    ('team-a', 'Alpha Owls', 'A. Manager'),
    ('team-b', 'Copperheads', 'B. Manager'),
    ('team-c', 'Third Deck', 'C. Manager');

INSERT INTO players (
    fantrax_id, name, positions, mlb_team, birthdate, career_ab, career_ip, real_life_il
) VALUES
    ('p-active', 'Mason Hitter', 'OF', 'SEA', DATE '2001-05-03', 410, 0, TRUE),
    ('p-dead', 'Elliot Slugger', '1B', 'BOS', DATE '1999-09-21', 800, 0, FALSE),
    ('p-minor', 'Nico Prospect', 'SS', 'CHC', DATE '2005-02-17', 15, NULL, NULL),
    ('p-beta', 'Riley Pitcher', 'SP', 'ATL', DATE '2000-11-11', 0, 132.2, TRUE);

INSERT INTO season_config (season, freeze_date, min_mlb_roster, cap_years)
VALUES (2027, DATE '2027-04-25', NULL, 78);

INSERT INTO contract_events (
    ts, team_id, fantrax_id, event_type, years, fa_year, source, note, roster_level
) VALUES
    (TIMESTAMP '2026-03-10 12:00:00', 'team-a', 'p-active', 'SIGNED', 4, 2030, 'migration', 'move_type=Draft', 'MLB'),
    (TIMESTAMP '2025-03-10 12:00:00', 'team-a', 'p-dead', 'SIGNED', 6, 2031, 'migration', 'move_type=Draft', 'MLB'),
    (TIMESTAMP '2026-06-10 12:00:00', 'team-a', 'p-dead', 'DROPPED', 2.5, 2031, 'manual', 'fixture dead cap', 'MLB'),
    (TIMESTAMP '2027-01-12 12:00:00', 'team-a', NULL, 'CAP_TRADE', 2, NULL, 'manual', 'received cap space', 'MLB'),
    (TIMESTAMP '2026-04-10 12:00:00', 'team-b', 'p-beta', 'SIGNED', 3, 2029, 'migration', 'move_type=Draft', 'MLB'),
    (TIMESTAMP '2027-01-15 12:00:00', 'team-b', NULL, 'CAP_TRADE', -1.5, NULL, 'manual', 'sent cap space', 'MLB');

INSERT INTO roster_snapshots (snapshot_date, team_id, fantrax_id, roster_level) VALUES
    (DATE '2026-09-30', 'team-a', 'p-active', 'IL'),
    (DATE '2026-09-30', 'team-a', 'p-minor', 'minors'),
    (DATE '2026-09-30', 'team-b', 'p-beta', 'IL');

INSERT INTO team_season_history (
    season, team_id, w, l, t, regular_season_rank, made_playoffs, playoff_finish
) VALUES
    (2025, 'team-a', 10, 4, 0, 1, TRUE, 'champion'),
    (2025, 'team-b', 9, 5, 0, 2, TRUE, 'runner_up'),
    (2026, 'team-a', 9, 5, 0, 2, TRUE, 'runner_up'),
    (2026, 'team-b', 11, 3, 0, 1, TRUE, 'champion'),
    (2025, 'team-c', 4, 10, 0, 3, FALSE, NULL),
    (2026, 'team-c', 5, 9, 0, 3, FALSE, NULL);

INSERT INTO league_history (
    season, champion_team_id, runner_up_team_id, regular_season_first_team_id,
    prize_champion, prize_runner_up, notes
) VALUES
    (2025, 'team-a', 'team-b', 'team-a', 100, 50, 'Fixture season one'),
    (2026, 'team-b', 'team-a', 'team-b', 100, 50, 'Fixture season two');