const state = {
  topics: [],
  selectedTopicId: null,
  graphScope: "latest",
  graph: null,
  graphFilters: new Map(),
  selectedNodeId: null,
  provenanceFilter: "all",
};

const el = (id) => document.getElementById(id);

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return payload;
}

function text(value, fallback = "None") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function card(title, summary, className = "") {
  const node = document.createElement("article");
  node.className = `card ${className}`.trim();
  node.innerHTML = `<h4></h4><p></p>`;
  node.querySelector("h4").textContent = title;
  node.querySelector("p").textContent = summary;
  return node;
}

function statusClass(stateName) {
  return `status-${String(stateName || "idle").replaceAll("_", "-")}`;
}

function row(columns) {
  const node = document.createElement("article");
  node.className = "row";
  for (const column of columns) {
    const part = document.createElement("div");
    part.innerHTML = "<strong></strong><p></p>";
    part.querySelector("strong").textContent = column.label;
    part.querySelector("p").textContent = column.value;
    node.appendChild(part);
  }
  return node;
}

function statusRow(columns, className = "") {
  const node = row(columns);
  node.className = `row status-row ${className}`.trim();
  return node;
}

function empty(target, message) {
  target.innerHTML = "";
  const node = document.createElement("p");
  node.className = "empty";
  node.textContent = message;
  target.appendChild(node);
}

function renderTopics() {
  const select = el("topicSelect");
  select.innerHTML = "";
  for (const topic of state.topics) {
    const option = document.createElement("option");
    option.value = topic.topic_id;
    option.textContent = `${topic.topic_id} · ${topic.title}`;
    select.appendChild(option);
  }
  if (state.selectedTopicId) {
    select.value = state.selectedTopicId;
  }
}

function renderOverview(topicPayload) {
  const topicView = topicPayload.topic || {};
  const topic = topicView.topic || {};
  const projected = topicView.projected_memory || {};
  const runState = topicPayload.run_state || {};
  const counts = runState.status_counts || {};
  const runningNow = runState.running_now || {};
  const workerLoop = runState.worker_loop || topicPayload.worker_loop || {};
  el("topicTitle").textContent = topic.title || topicPayload.topic_id;
  el("topicSummary").textContent = topic.topic_summary || "No topic summary projected.";
  el("snapshotVersion").textContent = `v${topic.snapshot_version || 0}`;
  el("runningCount").textContent = String(counts.running || 0);
  el("queuedCount").textContent = String(counts.queued || 0);
  el("completedCount").textContent = String(counts.completed || 0);
  el("deadLetterCount").textContent = String(counts.dead_letter || 0);
  el("staleCount").textContent = String(counts.stale || 0);
  el("runCount").textContent = String((runState.run_timeline_items || topicPayload.runs || []).length);
  el("queueCount").textContent = String(counts.total || 0);

  const current = el("runningNowCard");
  current.className = `current-work ${statusClass(runningNow.state)}`;
  el("runningNowTitle").textContent = runningNow.title || "Current execution state";
  el("runningNowObjective").textContent = runningNow.objective || "No objective projected.";
  el("runningNowRun").textContent = text(runningNow.run_id);
  el("runningNowQueue").textContent = text(runningNow.queue_item_id);
  el("runningNowEvent").textContent = `${text(runningNow.latest_event?.event_type)} · ${text(
    runningNow.latest_event?.detail
  )}`;
  el("runningNowGraph").textContent = runningNow.graph_context?.summary || "No graph relation projected.";
  el("workerLoopState").textContent = `${text(workerLoop.state, "idle")} · iterations=${text(
    workerLoop.iteration_count,
    "0"
  )} · executor=${text(workerLoop.executor_kind, "none")} · last-error=${text(
    workerLoop.last_error,
    "none"
  )} · no-yield=${text(workerLoop.consecutive_no_yield, "0")} · stop=${text(
    workerLoop.stop_reason,
    "none"
  )}`;

  const hypotheses = el("hypotheses");
  hypotheses.innerHTML = "";
  const currentBest = projected.current_best_hypotheses || [];
  if (!currentBest.length) {
    empty(hypotheses, "No current best hypothesis projected.");
  } else {
    for (const hypothesis of currentBest) {
      hypotheses.appendChild(card(hypothesis.title, hypothesis.summary));
    }
  }

  const conflicts = el("conflicts");
  conflicts.innerHTML = "";
  const active = projected.active_conflicts || [];
  if (!active.length) {
    empty(conflicts, "No active conflicts recorded.");
  } else {
    for (const conflict of active) {
      conflicts.appendChild(card(conflict.title || conflict.conflict_id, conflict.summary, "severity-warning"));
    }
  }
  el("conflictCount").textContent = String(active.length);
}

function renderRuns(payload) {
  const list = el("runsList");
  const runs = payload.run_state?.run_timeline_items || payload.timeline_items || payload.runs || [];
  list.innerHTML = "";
  if (!runs.length) {
    empty(list, "No runs recorded. If queue count is non-zero, work is waiting but no worker has claimed it.");
    el("runCount").textContent = "0";
    return;
  }
  el("runCount").textContent = String(runs.length);
  for (const run of runs) {
    list.appendChild(
      statusRow([
        { label: "Run", value: run.run_id },
        { label: "Status", value: text(run.status) },
        { label: "Objective", value: text(run.objective) },
        { label: "Latest event", value: text(run.latest_event?.event_type || run.timeline_source, "run_ledger") },
        { label: "Graph relation", value: text(run.graph_context?.summary) },
      ], statusClass(run.claim?.stale ? "stale" : run.status || run.queue_state))
    );
  }
}

function renderQueue(payload) {
  const list = el("queueList");
  const runState = payload.run_state || {};
  const groups = runState.queue_groups || [];
  const items = payload.queue?.items || payload.queue.items || [];
  list.innerHTML = "";
  el("queueCount").textContent = String(runState.status_counts?.total || items.length);
  if (groups.length) {
    for (const group of groups) {
      const section = document.createElement("section");
      section.className = `queue-group ${statusClass(group.group)}`;
      section.innerHTML = "<h4></h4><div></div>";
      section.querySelector("h4").textContent = `${group.label} · ${group.count}`;
      const body = section.querySelector("div");
      if (!group.items.length) {
        const node = document.createElement("p");
        node.className = "empty compact";
        node.textContent = `No ${group.label.toLowerCase()} work.`;
        body.appendChild(node);
      } else {
        for (const item of group.items) {
          body.appendChild(
            statusRow([
              { label: "Queue item", value: item.queue_item_id },
              { label: "Run", value: text(item.run_id) },
              { label: "Objective", value: item.objective },
              {
                label: "Graph relation",
                value: text(item.graph_context?.summary),
              },
            ], statusClass(item.state))
          );
        }
      }
      list.appendChild(section);
    }
    return;
  }
  if (!items.length) {
    empty(list, "No queued, claimed, completed, or dead-letter work for this topic.");
    return;
  }
  for (const item of items) {
    list.appendChild(
      statusRow([
        { label: "Queue item", value: item.queue_item_id },
        { label: "State", value: item.state },
        { label: "Objective", value: text(item.objective) },
      ], statusClass(item.claim?.stale ? "stale" : item.state))
    );
  }
}

function renderMemory(payload) {
  const list = el("memoryList");
  const memory = payload.memory;
  const projected = payload.topic?.projected_memory || {};
  list.innerHTML = "";
  el("graphDigest").textContent = text(memory.graph_digest, "none");
  const metrics = [
    ["Hypotheses", memory.hypothesis_count],
    ["Evidence", memory.evidence_count],
    ["Conflicts", memory.conflict_count],
    ["Challenge candidates", memory.challenge_candidate_count],
  ];
  for (const [title, value] of metrics) {
    list.appendChild(card(title, String(value)));
  }
  const currentBest = projected.current_best_hypotheses || [];
  const challengers = projected.challenger_targets || [];
  const conflicts = projected.active_conflicts || [];
  list.appendChild(
    card(
      "Current best",
      currentBest.length
        ? currentBest.map((item) => item.title).join("; ")
        : "No current best hypothesis projected.",
      "status-running"
    )
  );
  list.appendChild(
    card(
      "Challengers",
      challengers.length
        ? challengers.map((item) => item.title).join("; ")
        : "No challenger hypothesis projected.",
      "status-queued"
    )
  );
  list.appendChild(
    card(
      "Conflicts",
      conflicts.length
        ? conflicts.map((item) => item.summary).join("; ")
        : "No active conflict projected.",
      conflicts.length ? "status-dead-letter" : ""
    )
  );
}

function bindGraphFilterControls(graph) {
  const target = el("graphFilters");
  target.innerHTML = "";
  for (const filter of graph.filters) {
    if (!state.graphFilters.has(filter.filter_id)) {
      state.graphFilters.set(filter.filter_id, filter.enabled);
    }
    const label = document.createElement("label");
    label.className = "filter-toggle";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = state.graphFilters.get(filter.filter_id);
    input.addEventListener("change", () => {
      state.graphFilters.set(filter.filter_id, input.checked);
      renderGraphExplorer();
    });
    const span = document.createElement("span");
    span.textContent = `${filter.label} · ${filter.visible_count}/${filter.total_count}`;
    label.append(input, span);
    target.appendChild(label);
  }
}

function renderProvenanceOptions(graph) {
  const select = el("provenanceFilter");
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = "All provenance";
  select.appendChild(all);
  for (const option of graph.provenance_options) {
    const node = document.createElement("option");
    node.value = option.provenance_id;
    node.textContent = `${option.run_id} · ${option.label}`;
    select.appendChild(node);
  }
  select.value = state.provenanceFilter;
}

function provenanceMatches(item) {
  if (state.provenanceFilter === "all") {
    return true;
  }
  return (
    item.node_id === state.provenanceFilter ||
    item.source_node_id === state.provenanceFilter ||
    item.target_node_id === state.provenanceFilter ||
    (item.provenance_ids || []).includes(state.provenanceFilter)
  );
}

function selectedDetail(graph, selectedNodeId) {
  const nodes = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const node = nodes.get(selectedNodeId);
  if (!node) {
    return null;
  }
  const relation = (edge) => ({
    edge_id: edge.edge_id,
    relation: edge.edge_type,
    source_label: nodes.get(edge.source_node_id)?.label || edge.source_node_id,
    target_label: nodes.get(edge.target_node_id)?.label || edge.target_node_id,
    summary: edge.summary,
  });
  return {
    ...node,
    incoming_relations: graph.edges.filter((edge) => edge.target_node_id === node.node_id).map(relation),
    outgoing_relations: graph.edges.filter((edge) => edge.source_node_id === node.node_id).map(relation),
  };
}

function visibleGraph(baseGraph) {
  const nodes = baseGraph.nodes.map((node) => {
    const filterEnabled = !state.graphFilters.has(node.group) || state.graphFilters.get(node.group);
    return { ...node, visible: node.visible && filterEnabled && provenanceMatches(node) };
  });
  const visibleNodeIds = new Set(nodes.filter((node) => node.visible).map((node) => node.node_id));
  const edges = baseGraph.edges.map((edge) => ({
    ...edge,
    visible:
      edge.visible &&
      visibleNodeIds.has(edge.source_node_id) &&
      visibleNodeIds.has(edge.target_node_id) &&
      provenanceMatches(edge),
  }));
  const selectedNodeId =
    state.selectedNodeId && visibleNodeIds.has(state.selectedNodeId)
      ? state.selectedNodeId
      : nodes.find((node) => node.visible)?.node_id || null;
  return { ...baseGraph, nodes, edges, selected_node: selectedDetail({ ...baseGraph, edges }, selectedNodeId) };
}

function renderDetail(detail) {
  el("detailTitle").textContent = detail?.label || "None";
  el("detailSummary").textContent = detail?.summary || "No node selected.";
  const meta = el("detailMeta");
  meta.innerHTML = "";
  if (!detail) {
    el("detailRelations").innerHTML = "";
    return;
  }
  const rows = [
    ["Type", detail.node_type],
    ["Role", detail.group],
    ["Scope", detail.temporal_scope],
    ["Provenance", detail.provenance_ids.length ? detail.provenance_ids.join(", ") : "None"],
  ];
  for (const [label, value] of rows) {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    meta.append(term, description);
  }

  const relations = el("detailRelations");
  relations.innerHTML = "";
  const allRelations = [...detail.incoming_relations, ...detail.outgoing_relations];
  if (!allRelations.length) {
    empty(relations, "No visible relations for this node.");
    return;
  }
  for (const relation of allRelations) {
    relations.appendChild(
      row([
        { label: "Relation", value: relation.relation },
        { label: "From", value: relation.source_label },
        { label: "To", value: relation.target_label },
      ])
    );
  }
}

function renderGraphExplorer() {
  if (!state.graph) {
    empty(el("graphCanvas"), "No graph projection loaded.");
    return;
  }
  const graph = visibleGraph(state.graph);
  const visibleCount = graph.nodes.filter((node) => node.visible).length;
  el("graphScopeLabel").textContent = state.graphScope;
  el("graphState").textContent =
    visibleCount > 0
      ? `${visibleCount} visible nodes · ${graph.unresolved_conflict_count} unresolved conflicts`
      : "Filtered graph is empty.";
  renderDetail(graph.selected_node);
  window.CRBGraphRenderer.renderGraph(el("graphCanvas"), graph, {
    onSelect: (nodeId) => {
      state.selectedNodeId = nodeId;
      renderGraphExplorer();
    },
  });
}

async function loadGraph(scope = state.graphScope) {
  if (!state.selectedTopicId) {
    return;
  }
  state.graphScope = scope;
  const topicId = encodeURIComponent(state.selectedTopicId);
  const payload = await getJson(`/api/topics/${topicId}/graph/${scope}`);
  applyGraph(payload.graph, scope);
}

function applyGraph(graph, scope = state.graphScope) {
  state.graphScope = scope;
  state.graph = graph;
  state.selectedNodeId = state.graph.selected_node?.node_id || null;
  bindGraphFilterControls(state.graph);
  renderProvenanceOptions(state.graph);
  for (const button of document.querySelectorAll("[data-scope]")) {
    button.classList.toggle("active", button.dataset.scope === scope);
  }
  renderGraphExplorer();
}

async function loadSelectedTopic() {
  if (!state.selectedTopicId) {
    return;
  }
  const topicId = encodeURIComponent(state.selectedTopicId);
  const dashboard = await getJson(`/api/web/topics/${topicId}/dashboard`);
  el("authorityNotice").textContent = dashboard.authority_notice;
  renderOverview(dashboard);
  renderRuns(dashboard);
  renderQueue(dashboard);
  renderMemory(dashboard);
  applyGraph(dashboard.graph, "latest");
}

async function loadTopics() {
  const payload = await getJson("/api/topics");
  state.topics = payload.topics || [];
  state.selectedTopicId = state.selectedTopicId || state.topics[0]?.topic_id || null;
  renderTopics();
  if (!state.selectedTopicId) {
    return;
  }
  await loadSelectedTopic();
}

function bindTabs() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      for (const other of document.querySelectorAll(".tab")) {
        other.classList.toggle("active", other === tab);
      }
      for (const view of document.querySelectorAll(".view")) {
        view.classList.toggle("active", view.id === tab.dataset.view);
      }
    });
  }
}

el("topicSelect").addEventListener("change", async (event) => {
  state.selectedTopicId = event.target.value;
  state.selectedNodeId = null;
  state.provenanceFilter = "all";
  await loadSelectedTopic();
});

el("refreshButton").addEventListener("click", loadTopics);
el("latestGraphButton").addEventListener("click", () => loadGraph("latest"));
el("historyGraphButton").addEventListener("click", () => loadGraph("history"));
el("provenanceFilter").addEventListener("change", (event) => {
  state.provenanceFilter = event.target.value;
  renderGraphExplorer();
});

bindTabs();
loadTopics().catch((error) => {
  el("topicTitle").textContent = "Dashboard unavailable";
  el("topicSummary").textContent = error.message;
  el("graphState").textContent = error.message;
});
