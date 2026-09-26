// Pipeline execution view: draws dependency/compensation edges, fills the
// step inspector on demand, polls while the execution is active.
(function () {
  var view = document.querySelector("[data-pipeline-view]");
  if (!view) return;

  var svgNS = "http://www.w3.org/2000/svg";
  var svg = view.querySelector(".pipeline-edges");
  var layers = view.querySelector(".pipeline-layers");
  var inspector = view.querySelector("[data-inspector]");
  var nodes = {};
  view.querySelectorAll("[data-node]").forEach(function (b) {
    nodes[b.dataset.node] = b;
  });
  var graph = null;
  var replaying = false; // while true the live poll must not reload the page

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  // ---- edges ----------------------------------------------------------------
  function drawEdges() {
    if (!graph) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var box = view.querySelector(".pipeline-graph").getBoundingClientRect();
    var horizontal = getComputedStyle(layers).flexDirection === "row";
    svg.setAttribute("width", layers.scrollWidth);
    svg.setAttribute("height", layers.scrollHeight);
    var host = view.querySelector(".pipeline-graph");
    graph.edges.forEach(function (edge) {
      var a = nodes[edge.from], b = nodes[edge.to];
      if (!a || !b) return;
      var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      var ox = box.left - host.scrollLeft, oy = box.top - host.scrollTop;
      var x1, y1, x2, y2;
      if (edge.kind === "compensation") {
        // loop-backs go from the failing node's top edge to the target's bottom edge
        x1 = ra.left + ra.width / 2 - ox; y1 = ra.top - oy;
        x2 = rb.left + rb.width / 2 - ox; y2 = rb.bottom - oy;
      } else if (horizontal) {
        x1 = ra.right - ox; y1 = ra.top + ra.height / 2 - oy;
        x2 = rb.left - ox; y2 = rb.top + rb.height / 2 - oy;
      } else {
        x1 = ra.left + ra.width / 2 - ox; y1 = ra.bottom - oy;
        x2 = rb.left + rb.width / 2 - ox; y2 = rb.top - oy;
      }
      var path = document.createElementNS(svgNS, "path");
      var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      path.setAttribute("d", horizontal || edge.kind === "compensation"
        ? "M" + x1 + " " + y1 + " C " + mx + " " + y1 + ", " + mx + " " + y2 + ", " + x2 + " " + y2
        : "M" + x1 + " " + y1 + " C " + x1 + " " + my + ", " + x2 + " " + my + ", " + x2 + " " + y2);
      path.setAttribute("class", "edge edge-" + edge.kind);
      svg.appendChild(path);
    });
  }

  // ---- inspector -----------------------------------------------------------------
  function pre(text) {
    var p = el("pre", "run-log", text || "(empty)");
    return p;
  }

  function section(title, body) {
    var s = el("section", "inspector-section");
    s.appendChild(el("h3", "", title));
    s.appendChild(body);
    return s;
  }

  function fmt(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    return seconds < 60 ? seconds.toFixed(1) + "s" : Math.floor(seconds / 60) + "m " + Math.floor(seconds % 60) + "s";
  }

  function renderDetail(d) {
    inspector.textContent = "";
    var head = el("h2", "", d.name);
    inspector.appendChild(head);
    inspector.appendChild(el("p", "", d.type.replace(/_/g, " ") + " · " + d.category.toLowerCase() + " · " + d.phase.toLowerCase() + " · " + d.state_label));

    var last = d.attempts[d.attempts.length - 1];
    var summary = el("dl", "run-meta");
    function row(k, v) {
      summary.appendChild(el("dt", "", k));
      summary.appendChild(el("dd", "", v));
    }
    row("Attempts", String(d.attempts.length));
    if (last) {
      row("Duration", fmt(last.duration));
      row("Exit code", last.exit_code === null ? "–" : String(last.exit_code));
    }
    var comp = d.compensation.action ? d.compensation.action.replace(/_/g, " ").toLowerCase() : "stop";
    row("On failure", comp);
    if (d.depends_on.length) row("After", d.depends_on.join(", "));
    inspector.appendChild(section("Summary", summary));

    if (last && last.error) inspector.appendChild(section("Failure", pre(last.error)));
    if (last && last.input) inspector.appendChild(section(d.type === "AGENT" ? "Prompt (effective)" : "Input", pre(last.input)));
    if (last && (last.output || last.result)) inspector.appendChild(section(d.type === "AGENT" ? "Agent reply" : "Output", pre(last.output || last.result)));
    if (last && last.redacted) inspector.appendChild(el("p", "form-hint", "Secrets were redacted from this step."));

    var config = pre(JSON.stringify(d.configuration, null, 2));
    var details = el("details");
    details.appendChild(el("summary", "", "Configuration"));
    details.appendChild(config);
    inspector.appendChild(details);

    if (d.attempts.length > 1) {
      var list = el("ul");
      d.attempts.forEach(function (a) {
        list.appendChild(el("li", "", "Attempt " + a.attempt + ": " + a.status.toLowerCase() + " (" + fmt(a.duration) + ")"));
      });
      inspector.appendChild(section("Attempts", list));
    }
    if (d.events.length) {
      var ev = el("ul");
      d.events.forEach(function (e) {
        ev.appendChild(el("li", "", e.at.slice(11, 19) + " " + e.type + (e.data ? ": " + e.data : "")));
      });
      inspector.appendChild(section("Events", ev));
    }
    if (last && last.log_url) {
      var a = el("a", "", "Download full output");
      a.href = view.dataset.logUrl.replace("__STEP__", last.id);
      inspector.appendChild(a);
    }
    if (d.waiting_step_id) inspector.appendChild(decisionForm(d));
  }

  function decisionForm(d) {
    var form = el("form", "stacked inspector-decision");
    form.appendChild(el("h3", "", "Your decision"));
    function field(label, name, required) {
      var l = el("label", "", label);
      var i = el("input");
      i.name = name;
      if (required) i.required = true;
      l.appendChild(i);
      form.appendChild(l);
    }
    field("Your name", "by", true);
    field("Comment", "comment", false);
    if (d.type === "MANUAL_INPUT") field("Value", "value", false);
    var actions = el("div", "row-actions");
    [["APPROVED", "Approve"], ["REJECTED", d.type === "MANUAL_REVIEW" ? "Request changes" : "Reject"]].forEach(function (pair) {
      var b = el("button", pair[0] === "APPROVED" ? "btn-primary" : "", pair[1]);
      b.type = "submit";
      b.value = pair[0];
      b.name = "decision";
      actions.appendChild(b);
    });
    form.appendChild(actions);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var body = new FormData(form, event.submitter);
      agentflowPost(view.dataset.decisionUrl.replace("__STEP__", d.waiting_step_id), body)
        .then(function (data) {
          if (data.message) sessionStorage.setItem("agentflow:flash", data.message);
          window.location.reload();
        })
        .catch(function (err) {
          agentflowFlash(err.message || "Could not record the decision.", "error");
        });
    });
    return form;
  }

  function select(name, push) {
    Object.keys(nodes).forEach(function (n) {
      nodes[n].setAttribute("aria-pressed", n === name ? "true" : "false");
    });
    if (push) history.replaceState(null, "", "#" + encodeURIComponent(name));
    fetch(view.dataset.nodeUrl.replace("__NAME__", encodeURIComponent(name)), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Could not load the step");
        return r.json();
      })
      .then(renderDetail)
      .catch(function (err) {
        agentflowFlash(err.message, "error");
      });
  }

  view.addEventListener("click", function (event) {
    var button = event.target.closest("[data-node]");
    if (button) select(button.dataset.node, true);
  });

  // ---- live updates -----------------------------------------------------------------
  function signature(g) {
    return JSON.stringify([g.execution.status, g.nodes.map(function (n) { return [n.state, n.attempts]; })]);
  }

  fetch(view.dataset.graphUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then(function (r) { return r.json(); })
    .then(function (g) {
      graph = g;
      finalGraph = g;
      drawEdges();
      var hash = decodeURIComponent(location.hash.slice(1));
      if (hash && nodes[hash]) select(hash, false);
      if (view.dataset.active === "true") {
        var before = signature(g);
        var timer = setInterval(function () {
          fetch(view.dataset.graphUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (r) { return r.json(); })
            .then(function (next) {
              if (!replaying && signature(next) !== before) {
                clearInterval(timer);
                window.location.reload();
              }
            })
            .catch(function () {});
        }, 2000);
      }
    })
    .catch(function () {
      agentflowFlash("Could not load the pipeline graph.", "error");
    });


  // ---- historical replay (docs/PIPELINE_VISUALISATION.md §17-18) ------------------------
  // Nothing is re-run: every step asks the server for the recorded state after
  // event N and redraws the same node buttons and inspector from it.
  var panel = document.querySelector("[data-replay-panel]");
  var toggle = document.querySelector("[data-replay-toggle]");
  var finalGraph = null;

  function applyGraph(g) {
    g.nodes.forEach(function (n) {
      var b = nodes[n.id];
      if (!b) return;
      b.className = "pipeline-node node-" + n.state.toLowerCase() + " cat-" + n.category.toLowerCase() + (n.current ? " node-current" : "");
      b.querySelector(".node-icon").textContent = n.icon;
      b.querySelector(".node-state").textContent = n.state_label + (n.attempts > 1 ? " · attempt " + n.attempt : "");
      var sum = b.querySelector(".node-summary");
      if (n.summary) {
        if (!sum) { sum = el("span", "node-summary"); b.appendChild(sum); }
        sum.textContent = n.summary;
      } else if (sum) {
        sum.remove();
      }
    });
    graph = g;
    drawEdges();
  }

  if (panel && toggle) {
    var timelineEl = panel.querySelector("[data-replay-timeline]");
    var positionEl = panel.querySelector("[data-replay-position]");
    var currentEl = panel.querySelector("[data-replay-current]");
    var artifactsEl = panel.querySelector("[data-replay-artifacts]");
    var kindSel = panel.querySelector("[data-replay-kind]");
    var speedSel = panel.querySelector("[data-replay-speed]");
    var playBtn = panel.querySelector("[data-replay-play]");
    var events = [];
    var position = 0;
    var selected = null;
    var timer = null;
    var loaded = false;
    var seekToken = 0; // ignore a slow response that a newer click has overtaken

    function hhmmss(iso) { return iso ? iso.slice(11, 19) : ""; }

    function markCurrent() {
      timelineEl.querySelectorAll(".replay-event").forEach(function (b) {
        var p = Number(b.dataset.position);
        b.setAttribute("aria-current", p === position ? "true" : "false");
        b.classList.toggle("replay-future", p > position);
      });
      var now = timelineEl.querySelector('[aria-current="true"]');
      if (now && now.scrollIntoView) now.scrollIntoView({ block: "nearest", inline: "center" });
    }

    function renderTimeline(list) {
      timelineEl.textContent = "";
      list.forEach(function (e) {
        var li = el("li");
        var b = el("button", "replay-event");
        b.type = "button";
        b.dataset.position = e.position;
        var time = el("span", "replay-time", hhmmss(e.at) + " · " + e.kind);
        b.appendChild(time);
        b.appendChild(document.createTextNode(e.icon + " " + e.type.replace(/([a-z])([A-Z])/g, "$1 $2") + (e.node ? " · " + e.node : "")));
        b.title = e.data || "";
        li.appendChild(b);
        timelineEl.appendChild(li);
      });
      markCurrent();
    }

    function loadEvents(kind) {
      var url = view.dataset.replayEventsUrl + "?limit=1000" + (kind ? "&kind=" + encodeURIComponent(kind) : "");
      return fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          events = data.events;
          if (!loaded) {
            data.kinds.forEach(function (k) {
              var o = el("option", "", k.charAt(0).toUpperCase() + k.slice(1));
              o.value = k;
              kindSel.appendChild(o);
            });
            loaded = true;
          }
          renderTimeline(events);
        });
    }

    function show(data) {
      position = data.event;
      applyGraph(data.graph);
      positionEl.textContent = "Event " + data.event + " of " + data.total;
      var c = data.current;
      currentEl.textContent = c ? hhmmss(c.at) + " · " + c.type + (c.data ? ": " + c.data : "") : "Before the first event";
      artifactsEl.textContent = data.artifacts.length ? "Artifacts so far: " + data.artifacts.map(function (a) { return a.name; }).join(", ") : "";
      if (data.node && data.inspector) {
        selected = data.node;
        Object.keys(nodes).forEach(function (n) { nodes[n].setAttribute("aria-pressed", n === data.node ? "true" : "false"); });
        renderDetail(data.inspector);
      }
      markCurrent();
    }

    function seek(action, event) {
      var token = ++seekToken;
      var body = new FormData();
      body.set("action", action);
      body.set("current", String(position));
      if (event !== undefined) body.set("event", String(event));
      return fetch(view.dataset.replaySeekUrl, {
        method: "POST", body: body, headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) throw new Error(data.error);
          if (token !== seekToken) return data;
          show(data);
          return data;
        })
        .catch(function (err) { stop(); agentflowFlash(err.message || "Replay failed.", "error"); });
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      playBtn.setAttribute("aria-pressed", "false");
      playBtn.textContent = "▶ Play";
    }

    function play() {
      var speed = Number(speedSel.value) || 1;
      var atEnd = events.length && position >= events[events.length - 1].position;
      var start = atEnd ? seek("first") : Promise.resolve();
      playBtn.setAttribute("aria-pressed", "true");
      playBtn.textContent = "⏸ Pause";
      start.then(function () {
        timer = setInterval(function () {
          seek("next").then(function (data) { if (data && data.at_end) stop(); });
        }, 1000 / speed);
      });
    }

    function open() {
      replaying = true;
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      view.classList.add("replay-active");
      if (!loaded) loadEvents("").then(function () { seek("first"); });
    }

    function close() {
      stop();
      replaying = false;
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      view.classList.remove("replay-active");
      if (finalGraph) applyGraph(finalGraph);
      if (selected) select(selected, false);
    }

    toggle.addEventListener("click", function () { if (panel.hidden) open(); else close(); });
    panel.querySelector("[data-replay-exit]").addEventListener("click", close);
    playBtn.addEventListener("click", function () { if (timer) stop(); else play(); });
    speedSel.addEventListener("change", function () { if (timer) { stop(); play(); } });
    kindSel.addEventListener("change", function () { loadEvents(kindSel.value); });
    panel.querySelectorAll("[data-replay-action]").forEach(function (b) {
      b.addEventListener("click", function () { stop(); seek(b.dataset.replayAction); });
    });
    // One delegated handler covers events re-rendered by the filter.
    timelineEl.addEventListener("click", function (event) {
      var b = event.target.closest("[data-position]");
      if (b) { stop(); seek("seek", Number(b.dataset.position)); }
    });
    if (location.hash === "#replay" && !toggle.disabled) open();
  }

  window.addEventListener("resize", drawEdges);
  layers.addEventListener("scroll", drawEdges);
  var host = view.querySelector(".pipeline-graph");
  host.addEventListener("scroll", drawEdges);
})();
