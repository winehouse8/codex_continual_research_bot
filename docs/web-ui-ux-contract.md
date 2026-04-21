# Local Web UI UX Contract

Phase 16 fixes the localhost web dashboard contract before the web server and
HTML implementation are added. The dashboard is a read-only projection surface:
it helps an operator inspect research state, but it must not become the
authority for topic, graph, queue, or provenance state.

The canonical fixture is `fixtures/web_dashboard_view_models.json`, parsed as
`WebDashboardViewModelBundle`.

## Information Architecture

The dashboard has four first-class views:

- `overview`: current best hypothesis, unresolved conflict status, queue health,
  and the next visible research action.
- `hypothesis_board`: current-best, challenger, and retired-or-stale lanes.
- `graph_explorer`: a backend graph projection with provenance and conflict
  badges.
- `run_timeline`: run lifecycle events, backend state update status, and
  follow-up actions.

Each view carries the same authority notice:

`Dashboard cards and graph views are projections, not a source of truth; backend
state, graph, queue, and provenance ledgers remain authoritative.`

## View Model Rules

The overview must show at least one current best hypothesis and at least one
next research action when the topic is ready. Active conflicts must appear as a
warning indicator so the UI cannot imply the topic is simply healthy.

The hypothesis board must separate `current_best` from `challenger` cards.
Stale hypotheses may remain visible as context, but they must not appear in the
current-best lane.

The graph explorer must declare a projection source and must validate all edge
and focus-node references against its node list. The projection can guide visual
layout, filtering, and inspection, but backend graph and provenance ledgers
remain authoritative.

The run timeline must include the current lifecycle state among its displayed
events. Completion only means the dashboard projection saw an accepted backend
state update; it does not grant write authority to the web UI.

## State Snapshots

The fixture fixes empty, loading, error, dead-letter, and stale-claim states.
Dead-letter and stale-claim states must use warning or critical severity. They
must never render like normal queued or completed work.

The required state snapshot ids are:

- `state_loading_overview`
- `state_empty_hypothesis_board`
- `state_error_graph_explorer`
- `state_dead_letter_timeline`
- `state_stale_claim_overview`

## Web API Schemas

The fixture includes a schema catalog for the future local web API:

- `GET /api/web/topics/{topic_id}/dashboard`
- `GET /api/web/topics/{topic_id}/overview`
- `GET /api/web/topics/{topic_id}/graph`
- `GET /api/web/runs/{run_id}/timeline`

The response schema ids in the catalog must match the Pydantic models in
`codex_continual_research_bot.ux_contracts`. Phase 17 can serve these contracts
directly or map backend read models into the same shapes.
