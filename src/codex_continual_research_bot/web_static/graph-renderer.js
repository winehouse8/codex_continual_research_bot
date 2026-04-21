(function (global) {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const WIDTH = 960;
  const HEIGHT = 540;
  const GROUP_COLUMNS = {
    topic: 0,
    support: 1,
    current_best: 2,
    challenger: 3,
    evidence: 4,
    conflict: 5,
    provenance: 6,
  };

  function svgEl(name, attributes = {}) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attributes)) {
      node.setAttribute(key, String(value));
    }
    return node;
  }

  function layout(nodes) {
    const visible = nodes.filter((node) => node.visible);
    const buckets = new Map();
    for (const node of visible) {
      const group = node.group || node.node_type || "support";
      if (!buckets.has(group)) {
        buckets.set(group, []);
      }
      buckets.get(group).push(node);
    }
    const positions = new Map();
    for (const [group, groupNodes] of buckets.entries()) {
      const column = GROUP_COLUMNS[group] ?? 1;
      const x = 72 + column * 132;
      const step = HEIGHT / (groupNodes.length + 1);
      groupNodes.forEach((node, index) => {
        positions.set(node.node_id, { x, y: Math.round(step * (index + 1)) });
      });
    }
    return positions;
  }

  function appendLabel(parent, node, position) {
    const label = svgEl("text", {
      x: position.x,
      y: position.y + 36,
      "text-anchor": "middle",
      "class": "graph-label",
    });
    const text = node.label.length > 26 ? `${node.label.slice(0, 23)}...` : node.label;
    label.textContent = text;
    parent.appendChild(label);
  }

  function renderGraph(target, graph, options = {}) {
    target.innerHTML = "";
    const visibleNodes = graph.nodes.filter((node) => node.visible);
    if (!visibleNodes.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No graph nodes match the active filters.";
      target.appendChild(empty);
      return;
    }

    const positions = layout(graph.nodes);
    const svg = svgEl("svg", {
      viewBox: `0 0 ${WIDTH} ${HEIGHT}`,
      role: "img",
      "aria-label": "Graph explorer projection",
    });
    const defs = svgEl("defs");
    const marker = svgEl("marker", {
      id: "arrow",
      viewBox: "0 0 10 10",
      refX: "8",
      refY: "5",
      markerWidth: "6",
      markerHeight: "6",
      orient: "auto-start-reverse",
    });
    marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "arrow-head" }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    for (const edge of graph.edges.filter((item) => item.visible)) {
      const source = positions.get(edge.source_node_id);
      const targetPosition = positions.get(edge.target_node_id);
      if (!source || !targetPosition) {
        continue;
      }
      const line = svgEl("line", {
        x1: source.x,
        y1: source.y,
        x2: targetPosition.x,
        y2: targetPosition.y,
        "marker-end": "url(#arrow)",
        "class": edge.style_classes.join(" "),
      });
      svg.appendChild(line);
    }

    for (const node of visibleNodes) {
      const position = positions.get(node.node_id);
      const group = svgEl("g", {
        tabindex: "0",
        role: "button",
        "data-node-id": node.node_id,
        "class": `${node.style_classes.join(" ")} ${
          node.node_id === graph.selected_node?.node_id ? "selected" : ""
        }`,
      });
      group.appendChild(svgEl("circle", { cx: position.x, cy: position.y, r: "22" }));
      const type = svgEl("text", {
        x: position.x,
        y: position.y + 5,
        "text-anchor": "middle",
        "class": "graph-node-type",
      });
      type.textContent = node.node_type.slice(0, 3).toUpperCase();
      group.appendChild(type);
      appendLabel(group, node, position);
      group.addEventListener("click", () => options.onSelect?.(node.node_id));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          options.onSelect?.(node.node_id);
        }
      });
      svg.appendChild(group);
    }

    target.appendChild(svg);
  }

  global.CRBGraphRenderer = { renderGraph };
})(window);
