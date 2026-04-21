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
  const topic = topicPayload.topic.topic || {};
  const projected = topicPayload.topic.projected_memory || {};
  el("topicTitle").textContent = topic.title || topicPayload.topic_id;
  el("topicSummary").textContent = topic.topic_summary || "No topic summary projected.";
  el("snapshotVersion").textContent = `v${topic.snapshot_version || 0}`;

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
  list.innerHTML = "";
  if (!payload.runs.length) {
    empty(list, "No runs recorded for this topic.");
    el("runCount").textContent = "0";
    return;
  }
  el("runCount").textContent = String(payload.runs.length);
  for (const run of payload.runs) {
    list.appendChild(
      row([
        { label: "Run", value: run.run_id },
        { label: "Status", value: text(run.status) },
        { label: "Graph digest", value: text(run.graph_digest) },
      ])
    );
  }
}

function renderQueue(payload) {
  const list = el("queueList");
  const items = payload.queue.items || [];
  list.innerHTML = "";
  el("queueCount").textContent = String(items.length);
  if (!items.length) {
    empty(list, "No queued work for this topic.");
    return;
  }
  for (const item of items) {
    list.appendChild(
      row([
        { label: "Queue item", value: item.queue_item_id },
        { label: "State", value: item.state },
        { label: "Objective", value: text(item.objective) },
      ])
    );
  }
}

function renderMemory(payload) {
  const list = el("memoryList");
  const memory = payload.memory;
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
    span.textContent = `${filter.label} ${filter.total_count}`;
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
  state.graph = payload.graph;
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
  const [topic, runs, queue, memory] = await Promise.all([
    getJson(`/api/topics/${topicId}`),
    getJson(`/api/topics/${topicId}/runs`),
    getJson(`/api/topics/${topicId}/queue`),
    getJson(`/api/topics/${topicId}/memory`),
  ]);
  el("authorityNotice").textContent = topic.authority_notice;
  renderOverview(topic);
  renderRuns(runs);
  renderQueue(queue);
  renderMemory(memory);
  await loadGraph(state.graphScope);
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
