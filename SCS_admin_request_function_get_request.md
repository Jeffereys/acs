# SCS / ReserveCloud config request — one Event Gateway Get Request for the Bookeo migration

**To:** SCS site administrator (access right: *Manage Gateway Agents and Settings*) / Infor SCS technical support
**From:** Jefferey Stephens
**Site:** Alley Cats Entertainment, Burleson
**Screen:** Settings > Events > Manage Event Gateway Get Requests
(`/web/settings/manageEventGatewayGets`) — currently "No records to view"
**Re:** Add a read-back Get Request so the Bookeo→SCS migration can be idempotent — **no schema/custom-field change needed**

---

## Why

We import Bookeo bookings into SCS as Events + Functions through the
existing Gateway Agent (already permitted for `EventFunctionImport` /
`EventUpdate`). The migration tool currently keeps a **local file** mapping
each Bookeo booking number to the SCS Event it created — that file is the
only thing stopping a re-run from creating duplicate Events, because there
is no Gateway *Get* Request configured to let the tool ask SCS *"has this
Bookeo booking already been imported?"*

Confirmed live (`mode="test"`, no data written):

- A client-supplied `function.event.uniqueId` does **not** work as an
  idempotency key — the gateway rejects it for new Events.
- `EventFunctionImport` returns `Created` (not `Merged`) on a re-import —
  no server-side dedupe.
- We can already write the Bookeo booking number onto the Event **with no
  config change**, into the existing field **`function.event.interfaceAccountId`**
  ("Interface Account ID", 128 chars — accepted by this account's
  `EventFunctionImport`). `function.event.billingNotes` also works as a
  fallback.

So the only thing we need is a way to **read that value back**.

## The ask

### 1. An Event Gateway Get Request

**Path:** Settings > Events > *Manage Event Gateway Get Requests* → New

- **Request Name:** `BookeoMigration_Lookup` (or your convention — letters
  / numbers / underscore only; tell us the final name).
- **Description:** "Read-back for the Bookeo migration — find imported
  Events by Interface Account ID or start-date range."
- **Selected Columns** (field references from the screen's *Event Gateway
  Get Fields* guide):
  - `interfaceAccountId`   — Interface Account ID  ← **holds the Bookeo booking number**
  - `uniqueId`             — Event Unique Identifier
  - `eventNumber`          — Event Number
  - `name`                 — Event Name
  - `startDate`            — Start Date
  - `startTime`            — Start Time
  - `lifecycleState.stateType` — Event Status (will be "Option Hold 5")
  - `functions.locations.name` — Function Locations
- **Filters we need:**
  - **`startDate`** with `GREATER_THAN` / `LESS_THAN` — required (date-range
    sweep of the migration window).
  - **`interfaceAccountId`** with `EQUAL_TO` — nice to have (exact
    single-booking lookup). If Interface Account ID isn't available as a
    filter parameter, the start-date filter alone is fine — we'll match
    locally.

### 2. Gateway Agent permission

Settings > Users > *Manage Gateway Agents* → (our agent) → Edit → check
`BookeoMigration_Lookup` → Save.

## Please confirm / send back

- Final **Request Name**.
- Whether **`interfaceAccountId`** and **`startDate`** are available as
  **filter parameter fields** (the "Event Gateway Get Filter Parameter
  Fields" list on that screen), and the exact reference strings to use.
- That **`interfaceAccountId` is not already consumed by another SCS
  interface / accounting integration** on this account — if it is, we'll
  use `billingNotes` instead.
- That gateway-created Events for this site use the **Standard Lifecycle**
  model and the Get Request will return Events in the **Option Hold 5**
  (`OPTION_HOLD_5`) state.

## One question

Can `EventFunctionImport` be configured to **match/merge** on
`interfaceAccountId` (so a re-import returns `Merged` and updates in
place)? That would remove the need for the read-back entirely. Assuming
no; asking for the Get Request regardless.

## What we'll do once it's in place

- Write the Bookeo booking number into `interfaceAccountId` on every
  import (prefixed, e.g. `BKO:1577603189305668`).
- Before each import, call `BookeoMigration_Lookup` for the window and skip
  any booking already present in SCS.
- The local mapping file becomes a rebuildable cache, not the system of
  record.

## Reference

- SCS Gateway – Overview: *admin_setup > manage_gateway_requests*,
  *manage_gateway_agents*; Get Request filter operators (`EQUAL_TO`,
  `GREATER_THAN`, `LESS_THAN`, …).
- Screen's *Event Gateway Get Fields* guide: `interfaceAccountId`
  (Length 0–128), `billingNotes` (Length 0–256), `startDate`, `startTime`,
  `lifecycleState.stateType`, `uniqueId`, `eventNumber`.

Thanks — happy to hop on a call to nail down the filter fields.
