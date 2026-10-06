# Manual contract resolution review

This file lists every remaining migration exception that still needs a human review before the dry-run can be considered ready to commit.

Instructions:
- For each row, fill in the missing Fantrax player identity and team ownership.
- If the player is still on a Fantrax roster, confirm the correct current team.
- If the player is no longer on the roster, confirm whether the row should remain an exception or be treated as a dropped/expired contract.
- For invalid contract years or blank move types, confirm the actual contract term and whether the row is valid.

## Status summary

- Remaining exception rows: 24
- Reason categories:
  - No unambiguous Fantrax player ID alias
  - Invalid or missing Player Contract Years
  - Move Type is blank and contract exceeds the 1-3 year waiver/FA limit
  - Sheet player is absent from the current Fantrax roster and has an active contract over 3 years

---

## 1) Unresolved alias rows

### Boe | Row 43 | Leodalis De Vries
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID, notes on whether still active/dropped.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: Dropped_______________________________________________

### Maloun | Row 42 | Leonardo Bernal
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### Hoffman | Row 12 | Ryan O'Hearn
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### Hoffman | Row 50 | Mike Soroka
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### Pecora | Row 21 | Ryan O'Hearn
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### C. Pelton | Row 67 | Jared Jones
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 18 | E. Duran
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 29 | B. Baker
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 39 | R. Sasaki
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 42 | E. Ruiz
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 43 | E. Valencia
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 44 | B. Chandler
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 45 | A. Painter
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### W. Pelton | Row 53 | A. Fischer
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### Riggen | Row 30 | Wrobleski
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### Riggen | Row 33 | Herget
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### Riggen | Row 46 | Schmitt
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### A. Rolain | Row 48 | Riley O'Brien
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### M. Rolain | Row 60 | Seth Lego
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

### M. Rolain | Row 64 | Robby Ray
- Missing data: Correct Fantrax player name, current Fantrax team, Fantrax player ID.
- Input:
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

---

## 2) Invalid or missing contract years

### C. Pelton | Row 79 | Didier Fuentes (2026/2027)
- Missing data: Actual contract years value, correct FA year or year-added value, and whether the entry is still active.
- Input:
  - Actual contract years: __________
  - Year added: __________
  - Correct FA year: __________
  - Current Fantrax team: __________
  - Notes: _______________________________________________

### C. Pelton | Row 80 | Chase Dollander (2026/2027)
- Missing data: Actual contract years value, correct FA year or year-added value, and whether the entry is still active.
- Input:
  - Actual contract years: __________
  - Year added: __________
  - Correct FA year: __________
  - Current Fantrax team: __________
  - Notes: _______________________________________________

---

## 3) Blank move type with multi-year contract above waiver/FA threshold

### Pitlock | Row 61 | Trey Yesavage
- Missing data: Correct move type, actual contract term, and whether this is a valid multi-year signing or should be excluded.
- Input:
  - Correct move type: __________
  - Contract years: __________
  - Current Fantrax team: __________
  - Resolving note: __________________________________________

---

## 4) Active player absent from current Fantrax roster beyond 3 years

### A. Rolain | Row 46 | Cade Smith
- Missing data: Confirm whether this player is still active on a roster, whether the contract is valid, and whether the roster absence should still be treated as a true exception.
- Input:
  - Current Fantrax roster status: [ ] Active on roster  [ ] Not on roster  [ ] Unknown
  - Correct Fantrax player: __________
  - Current Fantrax team: __________
  - Fantrax player ID: __________
  - Notes: _______________________________________________

---

## Quick fill-in template

Use this short form for any remaining row:

- Sheet tab: __________
- Sheet row: __________
- Sheet player: __________
- Correct Fantrax player: __________
- Correct Fantrax team: __________
- Fantrax player ID: __________
- Contract years: __________
- Move type: __________
- FA year: __________
- Clarifying note: __________________________________________

---

## Manual resolution final checklist

- [ ] All unresolved alias rows matched to a unique Fantrax player
- [ ] All contract-year rows corrected or confirmed invalid
- [ ] Blank move-type rows confirmed or corrected
- [ ] Any active contract over 3 years with no current roster match reviewed
- [ ] Dry-run rerun with zero exceptions and `can_commit = True`
