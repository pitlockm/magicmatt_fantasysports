# Manual contract resolution review

Filled 2026-10-06 against the live Fantrax API (`getPlayerIds` + `getTeamRosters`) per the
commissioner's rulings. All 24 rows resolved: 19 matched to rostered players, 5 confirmed
dropped. The current dry-run matches 554/554 rows with zero exceptions and plans 179 events;
the production migration has not been committed. Review the event-level preview at
`data/migration_event_preview_2026-10-06.csv`.

## Commissioner rulings (2026-10-06)

1. **The SDA (Fantrax) roster is the ultimate source of truth.** If a player is on a roster,
   assign him to that team.
2. **Player on two sheets:** check drop/trade history; default to the team he is currently on.
   (Trade history is not exposed by the public Fantrax API — check via the Fantrax
   Transaction History page/CSV.)
3. **Player on no SDA roster:** confirm dropped (treat as dropped/expired contract).
4. **Missing contract years:** treat as signed for 2026, free agent in 2027 (1-year deal).
5. **The reviewed Fantrax-minors terms were placeholders, not official deals** (ruling 2026-10-06,
  including the Yesavage row). Those reviewed rows have no contract event or cap hit. If an
  official multi-year contract is later confirmed for a player demoted to minors, preserve the
  contract and count its cap years even while the player remains in a minors slot.
6. **Confirmed tab → team map:** Boe → Boe's Prospect Preschool; Maloun → Witt-ness Protection
   Program; Hoffman → Dinger Dave's Demolition Squad; Pecora → Army of the Potomac;
   C. Pelton → #SaveTheKeyhole; W. Pelton → Mighty Melonheads; Riggen → 10 day IL;
   A. Rolain → 2017 MLB All Stars; M. Rolain → Glorious Goggles; Pitlock → Magic Matt
   Fantasy Baseball.

## Open feature

Traded players can carry retained contract obligation (trading team keeps part of the cap hit).
This is not yet modeled — needs a design/build item (e.g. a retained-salary ledger entry or
contract split on trade).

## Status summary

- 24 exception rows reviewed: **19 resolved to a rostered player**, **5 flagged confirm-dropped**.
- The five current-minors players with apparent multi-year tracker terms were confirmed as placeholders; all are explicitly excluded from the migration.
- Current dry-run: **554/554 matched**, **0 exceptions**, **0 cap errors**, **179 planned events**, `can_commit=True`.
- No production ledger events have been written.

---

## 1) Resolved player identity and roster-owner rows

### Boe | Row 43 | Leodalis De Vries
- Correct Fantrax player: **De Vries, Leo**
- Current Fantrax team: **Boe's Prospect Preschool** (Boe)
- Fantrax player ID: **067yc**
- Roster status: MINORS, rostered
- Notes: The worksheet's pre-filled "Dropped" note is **wrong** — he is on Boe's roster. Match to 067yc.

### Maloun | Row 42 | Leonardo Bernal
- Correct Fantrax player: **Bernal, Leo**
- Current Fantrax team: **Witt-ness Protection Program** (Maloun)
- Fantrax player ID: **05rcw**
- Roster status: MINORS, rostered
- Notes: The other Fantrax "Bernal" (Jonatan Bernal, ARI RP, 060yf) is unrostered. Match to 05rcw.

### Hoffman | Row 12 | Ryan O'Hearn
- Correct Fantrax player: **OHearn, Ryan**
- Current Fantrax team: **Dinger Dave's Demolition Squad** (Hoffman)
- Fantrax player ID: **03dgq**
- Roster status: INJURED_RESERVE, rostered
- Notes: Per ruling 2, defaults to current roster team (Hoffman). Trade history vs Pecora
  could not be checked via API — verify on the Fantrax Transaction History page if it matters.

### Hoffman | Row 50 | Mike Soroka
- Correct Fantrax player: **Soroka, Michael**
- Current Fantrax team: — (none)
- Fantrax player ID: **03pjf**
- Roster status: **NOT ON ANY SDA ROSTER**
- Notes: **Commissioner-confirmed dropped 2026-10-06** — exclude from migration.

### Pecora | Row 21 | Ryan O'Hearn — DUPLICATE, resolves to Hoffman
- Correct Fantrax player: **OHearn, Ryan** (03dgq)
- Current Fantrax team: **Dinger Dave's Demolition Squad** (Hoffman)
- Roster status: rostered on Hoffman's team
- Notes: Same player as Hoffman Row 12; only one roster spot exists. Per ruling 2, assign to
  Hoffman. Pecora's Row 21 is stale (or O'Hearn was traded Pecora → Hoffman — check history).
  Remove Row 21 from Pecora's migration input.

### C. Pelton | Row 67 | Jared Jones
- Correct Fantrax player: **Jones, Jared** (SP)
- Current Fantrax team: — (none)
- Fantrax player ID: **05h2u**
- Roster status: **NOT ON ANY SDA ROSTER**
- Notes: **Commissioner-confirmed dropped 2026-10-06** — exclude from migration. (Fantrax also carries a duplicate "Jones, Jared 1B"
  entry 05u30; the SP 05h2u is the real player. Both unrostered.)

### W. Pelton | Row 18 | E. Duran
- Correct Fantrax player: **Duran, Ezequiel**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **04nqx**
- Roster status: ACTIVE, rostered
- Notes: "E." matches Ezequiel; Jhoan Duran is on Pecora's roster, Jarren Duran on Maloun's.

### W. Pelton | Row 29 | B. Baker
- Correct Fantrax player: **Baker, Bryan**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **041w4**
- Roster status: ACTIVE, rostered
- Notes: Only rostered Baker in the league.

### W. Pelton | Row 39 | R. Sasaki
- Correct Fantrax player: **Sasaki, Roki**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **05gdz**
- Roster status: MINORS, rostered

### W. Pelton | Row 42 | E. Ruiz
- Correct Fantrax player: **Ruiz, Esteury**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **0406v**
- Roster status: ACTIVE, rostered
- Notes: Only rostered E. Ruiz in the league.

### W. Pelton | Row 43 | E. Valencia
- Correct Fantrax player: **Valencia, Eduardo**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **06rkk**
- Roster status: MINORS, rostered
- Notes: Three Valencias in Fantrax (Esmil/MIA, Anthuan/CIN, Eduardo/DET) — Eduardo is the
  only one on an SDA roster.

### W. Pelton | Row 44 | B. Chandler
- Correct Fantrax player: **Chandler, Bubba**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **05rik**
- Roster status: MINORS, rostered

### W. Pelton | Row 45 | A. Painter
- Correct Fantrax player: **Painter, Andrew**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **05r3i**
- Roster status: MINORS, rostered

### W. Pelton | Row 53 | A. Fischer
- Correct Fantrax player: **Fischer, Andrew**
- Current Fantrax team: **Mighty Melonheads** (W. Pelton)
- Fantrax player ID: **06k7t**
- Roster status: MINORS, rostered
- Notes: Carson Fischer (WSH RP, 070db) is unrostered.

### Riggen | Row 30 | Wrobleski
- Correct Fantrax player: **Wrobleski, Justin**
- Current Fantrax team: — (none)
- Fantrax player ID: **05y9r**
- Roster status: **NOT ON ANY SDA ROSTER**
- Notes: **Commissioner-confirmed dropped 2026-10-06** — exclude from migration.

### Riggen | Row 33 | Herget
- Correct Fantrax player: **Herget, Jimmy**
- Current Fantrax team: **10 day IL** (Riggen)
- Fantrax player ID: **03qln**
- Roster status: ACTIVE, rostered
- Notes: Kevin Herget (NYM RP, 03at0) is unrostered.

### Riggen | Row 46 | Schmitt
- Correct Fantrax player: **Schmitt, Casey**
- Current Fantrax team: — (none)
- Fantrax player ID: **05jp4**
- Roster status: **NOT ON ANY SDA ROSTER**
- Notes: **Commissioner-confirmed dropped 2026-10-06** — exclude from migration.

### A. Rolain | Row 48 | Riley O'Brien
- Correct Fantrax player: **OBrien, Riley**
- Current Fantrax team: **2017 MLB All Stars** (A. Rolain)
- Fantrax player ID: **04ej3**
- Roster status: MINORS, rostered

### M. Rolain | Row 60 | Seth Lego → confirmed SETH LUGO, resolves to Pecora
- Correct Fantrax player: **Lugo, Seth** (commissioner-confirmed: "Seth Lego" is Seth Lugo)
- Current Fantrax team: **Army of the Potomac** (Pecora — NOT M. Rolain)
- Fantrax player ID: **03sef**
- Roster status: ACTIVE, rostered
- Notes: Per ruling 1, assign to Pecora. M. Rolain's Row 60 is stale, or Lugo was traded
  M. Rolain → Pecora (possibly with retained salary — see open feature above).

### M. Rolain | Row 64 | Robby Ray → resolves to Pecora
- Correct Fantrax player: **Ray, Robbie**
- Current Fantrax team: **Army of the Potomac** (Pecora — NOT M. Rolain)
- Fantrax player ID: **025j1**
- Roster status: ACTIVE, rostered
- Notes: Per ruling 1, assign to Pecora. Same stale-row-or-trade question as Lugo; if both
  moved together, check for one M. Rolain → Pecora trade.

---

## 2) Invalid or missing contract years

### C. Pelton | Row 79 | Didier Fuentes (2026/2027)
- Correct Fantrax player: **Fuentes, Didier**
- Current Fantrax team: **#SaveTheKeyhole** (C. Pelton)
- Fantrax player ID: **064zm**
- Roster status: MINORS, rostered
- Actual contract years: **1** (per ruling 4: signed 2026, FA 2027)
- Year added: 2026
- Correct FA year: 2027

### C. Pelton | Row 80 | Chase Dollander (2026/2027)
- Correct Fantrax player: **Dollander, Chase**
- Current Fantrax team: — (none)
- Fantrax player ID: **05yj0**
- Roster status: **NOT ON ANY SDA ROSTER**
- Notes: **Commissioner-confirmed dropped 2026-10-06** — exclude from migration.

---

## 3) Blank move type with multi-year contract above waiver/FA limit

### Pitlock | Row 61 | Trey Yesavage
- Correct Fantrax player: **Yesavage, Trey**
- Current Fantrax team: **Magic Matt Fantasy Baseball** (Pitlock)
- Fantrax player ID: **06apx**
- Roster status: MINORS, rostered
- Roster level: **minors** (commissioner-confirmed 2026-10-06 — drafted, never added to MLB roster)
- Correct move type: **drafted** (commissioner-confirmed 2026-10-06)
- Contract years: **N/A — commissioner ruling 2026-10-06: contract years do not apply to minor-league players**
- Notes: Row fully resolved. Consistent with the 2026-10-02 ruling that minors-sheet players
  carry no contract and consume no cap until moved to an active MLB slot with a multi-year
  announcement. Migration should store NULL/no contract term for this row.

---

## 4) Active player absent from current Fantrax roster beyond 3 years

### A. Rolain | Row 46 | Cade Smith — STALE EXCEPTION, player is rostered
- Correct Fantrax player: **Smith, Cade** (CLE RP)
- Current Fantrax team: **2017 MLB All Stars** (A. Rolain)
- Fantrax player ID: **04eor**
- Roster status: **ACTIVE, rostered** (checked 2026-10-06)
- Notes: This exception appears stale — Cade Smith is on A. Rolain's roster. Match to 04eor.
  (Fantrax also has a "Smith, Cade NYY SP" 06f15 entry; the CLE RP is the real player.)

## Duplicate rows resolved by multi-year scope

The migration report includes only contracts longer than one year. Rows with one year or less are omitted whether they are marked dropped or active; Fantrax ownership is used to choose the current-team row when a multi-year contract is duplicated. No source CSV has been changed.

| Fantrax player / ID | Sheet rows and contract fields | Migration handling |
| --- | --- | --- |
| Framber Valdez / `04auj` | Pecora row 36: Draft, 2 years, added 2026, not dropped. C. Pelton row 70: Waiver, 1 year, added 2026, dropped. | Keep Pecora row 36; omit the 1-year row. |
| MacKenzie Gore / `04cd5` | C. Pelton row 66 and M. Rolain row 61: both 1-year, marked dropped. | Omit both rows from the multi-year migration. |
| Aaron Nola / `02i3o` | C. Pelton row 72 and Pitlock row 58: both 1-year, marked dropped. | Omit both rows from the multi-year migration. |
| Brooks Lee / `04yig` | Pitlock row 24 and M. Rolain row 54: both 1-year, marked dropped. | Omit both rows from the multi-year migration. |
| Nick Martinez / `02c4q` | Pitlock rows 47 and 49 and M. Rolain row 59: all 1-year; row 59 is marked dropped. | Omit all three rows from the multi-year migration. |

---

## Manual resolution final checklist

- [x] All unresolved alias rows matched to a unique Fantrax player (19 rostered; 5 confirm-dropped)
- [x] All contract-year rows corrected or confirmed invalid (Fuentes → 1yr/FA2027; Dollander → confirm dropped)
- [x] Blank move-type row confirmed or corrected (Yesavage — drafted, minors, contract years N/A per 2026-10-06 ruling)
- [x] Any active contract over 3 years with no current roster match reviewed (Cade Smith — stale, rostered)
- [x] Commissioner confirmed the 5 dropped players (Soroka, Jared Jones, Wrobleski, Schmitt, Dollander — 2026-10-06)
- [x] O'Hearn duplicate resolved to Hoffman; omit stale Pecora Row 21 from migration
- [x] Omit 1-year duplicate rows and retain Framber Valdez's 2-year Pecora row
- [x] Lugo/Ray 1-year rows excluded from the multi-year migration scope
- [x] Dry-run rerun with zero exceptions and `can_commit = True`
