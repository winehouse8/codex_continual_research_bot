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

function text(value, fallback = "없음") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

const COPY = {
  none: "없음",
  graphRelationFallback: "이 queue item과 연결된 graph 관계가 없습니다.",
  groupLabels: {
    running: "실행 중",
    queued: "대기",
    completed: "완료",
    dead_letter: "Dead-letter",
    stale: "Stale claim",
  },
  workTitles: {
    "No active or queued work": "활성 또는 대기 작업 없음",
    "Running now": "지금 실행 중",
    "Queued next": "다음 실행 대기",
    "Stale claimed work": "오래된 claim 작업",
    "Dead-lettered work": "Dead-letter 작업",
    "Recently completed work": "최근 완료 작업",
  },
  timingLabels: {
    requested_at: "요청",
    claimed_at: "Claim",
    started_at: "시작",
    completed_at: "완료",
    failed_at: "실패",
    latest_event_at: "최신 event",
    duration_label: "Duration",
  },
};

function translatedWorkTitle(title) {
  return COPY.workTitles[title] || title || "현재 작업";
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

function helpSection(section) {
  const node = document.createElement("article");
  node.className = "help-card";
  const title = document.createElement("h4");
  title.textContent = section.title;
  const summary = document.createElement("p");
  summary.textContent = section.summary;
  const list = document.createElement("ul");
  for (const checkpoint of section.checkpoints || []) {
    const item = document.createElement("li");
    item.textContent = checkpoint;
    list.appendChild(item);
  }
  node.append(title, summary, list);
  return node;
}

function renderDashboardHelp(payload) {
  const sections = payload.dashboard_help?.sections || [];
  for (const section of sections) {
    const target = el(`${section.view_id}Help`);
    if (!target) {
      continue;
    }
    target.innerHTML = "";
    target.appendChild(helpSection(section));
  }

  const graphLegend = el("graphLegend");
  graphLegend.innerHTML = "";
  for (const entry of [
    ...(payload.graph_legend?.node_badges || []),
    ...(payload.graph_legend?.edge_types || []),
  ]) {
    const node = document.createElement("article");
    node.className = "legend-item";
    node.innerHTML = "<strong></strong><span></span><p></p>";
    node.querySelector("strong").textContent = entry.short_label;
    node.querySelector("span").textContent = entry.korean_label;
    node.querySelector("p").textContent = entry.plain_explanation;
    graphLegend.appendChild(node);
  }

  const queueHelp = el("queueHelpList");
  queueHelp.innerHTML = "";
  for (const stateHelp of payload.queue_state_help?.states || []) {
    const node = document.createElement("article");
    node.className = `legend-item ${statusClass(stateHelp.state)}`;
    node.innerHTML = "<strong></strong><span></span><p></p><small></small>";
    node.querySelector("strong").textContent = stateHelp.label;
    node.querySelector("span").textContent = stateHelp.korean_label;
    node.querySelector("p").textContent = stateHelp.plain_explanation;
    node.querySelector("small").textContent = stateHelp.next_action;
    queueHelp.appendChild(node);
  }

  const panel = el("helpPanelContent");
  panel.innerHTML = "";
  const sectionGroup = document.createElement("div");
  sectionGroup.className = "help-panel-grid";
  for (const section of sections) {
    sectionGroup.appendChild(helpSection(section));
  }
  const glossaryGroup = document.createElement("div");
  glossaryGroup.className = "glossary-list";
  const glossaryTitle = document.createElement("h3");
  glossaryTitle.textContent = "용어집";
  glossaryGroup.appendChild(glossaryTitle);
  for (const entry of payload.glossary?.entries || []) {
    const item = document.createElement("article");
    item.innerHTML = "<strong></strong><p></p><small></small>";
    item.querySelector("strong").textContent = `${entry.short_label} · ${entry.korean_label}`;
    item.querySelector("p").textContent = entry.plain_explanation;
    item.querySelector("small").textContent = entry.why_it_matters;
    glossaryGroup.appendChild(item);
  }
  panel.append(sectionGroup, glossaryGroup);
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
  el("topicSummary").textContent = topic.topic_summary || "토픽 요약 projection이 없습니다.";
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
  el("runningNowTitle").textContent = translatedWorkTitle(runningNow.title);
  el("runningNowObjective").textContent = runningNow.objective || "표시할 objective가 없습니다.";
  el("runningNowRun").textContent = text(runningNow.run_id);
  el("runningNowQueue").textContent = text(runningNow.queue_item_id);
  el("runningNowEvent").textContent = `${text(runningNow.latest_event?.event_type)} · ${text(
    runningNow.latest_event?.detail
  )}`;
  el("runningNowGraph").textContent = runningNow.graph_context?.summary || COPY.graphRelationFallback;
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
    empty(hypotheses, "현재 최선 가설 projection이 없습니다.");
  } else {
    for (const hypothesis of currentBest) {
      hypotheses.appendChild(card(hypothesis.title, hypothesis.summary));
    }
  }

  const conflicts = el("conflicts");
  conflicts.innerHTML = "";
  const active = projected.active_conflicts || [];
  if (!active.length) {
    empty(conflicts, "활성 충돌 기록이 없습니다.");
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
    empty(list, "Run 기록이 없습니다. Queue 수가 0이 아니면 worker가 아직 claim하지 않은 작업이 대기 중입니다.");
    el("runCount").textContent = "0";
    return;
  }
  el("runCount").textContent = String(runs.length);
  for (const run of runs) {
    const timing = run.timing || {};
    list.appendChild(
      statusRow([
        { label: "Run", value: run.run_id },
        { label: "Status", value: text(run.status) },
        { label: "Objective", value: text(run.objective) },
        { label: "요청", value: text(timing.requested_at, timing.labels?.requested || "요청 시각 기록 없음") },
        { label: "Claim", value: text(timing.claimed_at, timing.labels?.claimed || "아직 worker claim 전") },
        { label: "시작", value: text(timing.started_at, timing.labels?.started || "아직 시작 전") },
        {
          label: "완료/실패",
          value: text(
            timing.completed_at || timing.failed_at || timing.stopped_at,
            timing.labels?.completed || timing.labels?.failed || "아직 완료 전"
          ),
        },
        { label: "Duration", value: text(timing.duration_label, "기록 없음") },
        { label: "최신 event", value: text(run.latest_event?.event_type || run.timeline_source, "run_ledger") },
        { label: "Graph 관계", value: text(run.graph_context?.summary) },
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
      section.querySelector("h4").textContent = `${COPY.groupLabels[group.group] || group.label} · ${group.count}`;
      const body = section.querySelector("div");
      if (!group.items.length) {
        const node = document.createElement("p");
        node.className = "empty compact";
        node.textContent = `${COPY.groupLabels[group.group] || group.label} 작업 없음.`;
        body.appendChild(node);
      } else {
        for (const item of group.items) {
          const failure = item.failure || {};
          const claim = item.claim || {};
          body.appendChild(
            statusRow([
              { label: "Queue item", value: item.queue_item_id },
              { label: "Run", value: text(item.run_id) },
              { label: "Objective", value: item.objective },
              { label: "Failure code", value: text(failure.failure_code, "실패 없음") },
              { label: "Retryable", value: failure.failure_code ? String(Boolean(failure.retryable)) : "해당 없음" },
              {
                label: "Human review",
                value: failure.failure_code ? String(Boolean(failure.human_review_required)) : "해당 없음",
              },
              {
                label: "다음 행동",
                value:
                  item.state === "dead_letter"
                    ? "failure detail 확인 후 repair/retry 여부 결정"
                    : item.state === "stale"
                      ? "worker 생존 여부 확인 후 stale recovery 판단"
                      : item.state === "queued"
                        ? "worker loop 실행 또는 우선순위 확인"
                        : "graph/memory projection 변화 확인",
              },
              { label: "Claimed at", value: text(claim.claimed_at, "claim 기록 없음") },
              {
                label: "Graph 관계",
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
    empty(list, "이 topic에는 queued, claimed, completed, dead-letter 작업이 없습니다.");
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
    ["가설", memory.hypothesis_count],
    ["근거", memory.evidence_count],
    ["충돌", memory.conflict_count],
    ["도전 후보", memory.challenge_candidate_count],
  ];
  for (const [title, value] of metrics) {
    list.appendChild(card(title, String(value)));
  }
  const currentBest = projected.current_best_hypotheses || [];
  const challengers = projected.challenger_targets || [];
  const conflicts = projected.active_conflicts || [];
  list.appendChild(
    card(
      "현재 최선 가설",
      currentBest.length
        ? currentBest.map((item) => item.title).join("; ")
        : "현재 최선 가설 projection이 없습니다.",
      "status-running"
    )
  );
  list.appendChild(
    card(
      "도전자 가설",
      challengers.length
        ? challengers.map((item) => item.title).join("; ")
        : "도전자 가설 projection이 없습니다.",
      "status-queued"
    )
  );
  list.appendChild(
    card(
      "충돌",
      conflicts.length
        ? conflicts.map((item) => item.summary).join("; ")
        : "활성 충돌 projection이 없습니다.",
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
  all.textContent = "전체 provenance";
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
  el("detailTitle").textContent = detail?.label || "없음";
  el("detailSummary").textContent = detail?.summary || "선택한 node가 없습니다.";
  const meta = el("detailMeta");
  meta.innerHTML = "";
  if (!detail) {
    el("detailRelations").innerHTML = "";
    return;
  }
  const rows = [
    ["Type", detail.node_type],
    ["역할", detail.group],
    ["Scope", detail.temporal_scope],
    ["Provenance", detail.provenance_ids.length ? detail.provenance_ids.join(", ") : "없음"],
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
    empty(relations, "이 node에 표시 중인 관계가 없습니다.");
    return;
  }
  for (const relation of allRelations) {
    relations.appendChild(
      row([
        { label: "관계", value: relation.relation },
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
      ? `${visibleCount}개 node 표시 중 · unresolved conflict ${graph.unresolved_conflict_count}개`
      : "필터 결과 graph가 비어 있습니다.";
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
  renderDashboardHelp(dashboard);
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

function setHelpPanel(open) {
  const panel = el("helpPanel");
  panel.classList.toggle("open", open);
  panel.setAttribute("aria-hidden", open ? "false" : "true");
}

el("topicSelect").addEventListener("change", async (event) => {
  state.selectedTopicId = event.target.value;
  state.selectedNodeId = null;
  state.provenanceFilter = "all";
  await loadSelectedTopic();
});

el("refreshButton").addEventListener("click", loadTopics);
el("helpOpenButton").addEventListener("click", () => setHelpPanel(true));
el("helpCloseButton").addEventListener("click", () => setHelpPanel(false));
for (const closer of document.querySelectorAll("[data-help-close]")) {
  closer.addEventListener("click", () => setHelpPanel(false));
}
el("latestGraphButton").addEventListener("click", () => loadGraph("latest"));
el("historyGraphButton").addEventListener("click", () => loadGraph("history"));
el("provenanceFilter").addEventListener("change", (event) => {
  state.provenanceFilter = event.target.value;
  renderGraphExplorer();
});

bindTabs();
loadTopics().catch((error) => {
  el("topicTitle").textContent = "대시보드를 불러올 수 없습니다";
  el("topicSummary").textContent = error.message;
  el("graphState").textContent = error.message;
});
