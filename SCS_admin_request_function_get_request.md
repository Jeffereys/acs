# SCS / ReserveCloud config request — one Function/Event Gateway Get Request for the Bookeo migration

**To:** SCS site administrator (access right: *Manage Gateway Agents and Settings*) / Infor SCS technical support
**From:** Jefferey Stephens
**Site:** Alley Cats Entertainment, Burleson
**Re:** Add a read-back Gateway Get Request so the Bookeo→SCS migration can be idempotent — **no schema/custom-field change needed**

---

## Why

We import Bookeo bookings into SCS as Events + Functions through the
existing Gateway Agent (already permitted for `EventFunctionImport` and
`EventUpdate`). Right now the migration tool keeps a **local file** mapping
each Bookeo booking number to the SCS Event it created — that file is the
only thing stopping a re-run from creating duplicate Events, because there
is no Gateway *Get* Request configured that would let the tool ask SCS
*"has this Bookeo booking already been imported?"*

We confirmed (live, in `mode="test"`):

- Supplying our own `function.event.uniqueId` as an idempotency key does
  **not** work — the gateway rejects it for new Events (*"No event found
  for the specified uniqueId"*).
- `EventFunctionImport` on this account returns `Created` (not `Merged`)
  when re-importing a booking that already exists — so there is no
  server-side dedupe to lean on.
- We can already store the Bookeo booking number on the Event **without
  any config change**, in the existing free-text field
  `function.event.billingNotes` (accepted by this account's
  `EventFunctionImport`; `function.event.notes` also works but staff edit
  that one).

So the **only** thing we need from you is a way to *read that value back*.

## The ask — one Get Request + agent permission

### 1. A Function (or Event) Gateway Get Request

**Path:** Settings > Events > *Manage Function Gateway Get Requests*  →  New
(*Manage Event Gateway Get Requests* is fine too — whichever exposes the
fields below.)

- **Request Name:** `BookeoMigration_Lookup` (or your naming convention —
  letters / numbers / underscore only; please tell us the final name).
- **Description:** "Read-back for the Bookeo migration — find existing
  Events/Functions by billing-notes marker or date range."
- **Selected Columns** — drag from Available Columns; please include at least:
  - Function `uniqueId`, Function Number
  - Event `uniqueId`, Event Number
  - Event Name
  - **Event Billing Notes**  ← the field we store the Bookeo booking number in
  - Function Start Date, Function Start Time
  - Event Primary Contact — email address
  - Event Status (lifecycle state)
- **Filter fields we need:**
  - `function.startDate` with `GREATER_THAN` / `LESS_THAN` (date-range
    sweep of the migration window) — **required**.
  - If possible, make **Billing Notes** filterable too (an `EQUAL_TO`
    lookup on one booking). If billing notes can't be a filter, the
    date-range filter alone is enough — we'll match locally.

### 2. Gateway Agent permission

**Path:** Settings > Users > *Manage Gateway Agents* → (our agent) → Edit
→ check the box for `BookeoMigration_Lookup` → Save.

## Please send back

- The final **Request Name**.
- The exact **filter field name(s)** to use in the `filters=[...]`
  parameter for start date, and for billing notes if you made it
  filterable (we need the literal strings for the code).
- Confirmation that gateway-created Events for this site use the **Standard
  Lifecycle** model and that the Get Request will return Events in the
  `OPTION_HOLD_5` ("Option Hold 5") state — that's what the migration
  writes.

## One question

Can `EventFunctionImport` be configured to **match/merge** on
`function.event.billingNotes` (so a re-import returns `Merged` and updates
in place)? If yes, that would remove the need for the read-back entirely.
We're assuming no and asking for the Get Request regardless.

## What we'll do once it's in place

- Write the Bookeo booking number into `function.event.billingNotes` on
  every import (prefixed, e.g. `BKO:1577603189305668`).
- Before each import, call `BookeoMigration_Lookup` for the migration
  window and skip any booking whose marker is already present in SCS.
- The local mapping file becomes a rebuildable cache instead of the
  system of record.

## Reference

- SCS Gateway – Overview: *admin_setup > manage_gateway_requests*,
  *manage_gateway_agents*.
- SCS Gateway – Events and Functions: `Function` Get Request
  (`date_range_filter` example on `startDate`); `event.billingNotes`
  appears in the `EventUpdate` example header.

Thanks — happy to hop on a call to nail down the field names.
