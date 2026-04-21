const state = {
  topics: [],
  selectedTopicId: null,
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
      conflicts.appendChild(card(conflict.conflict_id, conflict.summary, "severity-warning"));
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
  await loadSelectedTopic();
});

el("refreshButton").addEventListener("click", loadTopics);
bindTabs();
loadTopics().catch((error) => {
  el("topicTitle").textContent = "Dashboard unavailable";
  el("topicSummary").textContent = error.message;
});
