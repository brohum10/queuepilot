"use strict";
const $ = (id) => document.getElementById(id);
const esc = (text) =>
  String(text ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const human = (s) =>
  String(s)
    .replaceAll("_", " ")
    .replaceAll("?", "")
    .replace(/^\w/, (c) => c.toUpperCase());
const stateLabel = (s) =>
  ({
    auto: "Suggested route",
    review: "Ready for review",
    waiting: "Waiting for capacity",
    reviewed: "Human reviewed",
  })[s];
const reasonLabel = (s) =>
  ({
    sensitive_request: "Sensitive request rule",
    multiple_issue_rule: "Multiple-issue rule",
    no_known_features: "No known features",
    below_threshold: "Below confidence threshold",
    close_alternatives: "Close alternatives",
  })[s] || human(s);
let boot,
  inbox,
  filter = "all",
  page = 0,
  selected = null,
  showTruth = false,
  pending = false,
  thresholdDirty = false;
let importRead = Promise.resolve(),
  importFileError = "",
  fileGeneration = 0,
  auditGroup = "queue_disagreement";
const reviewDrafts = new Map();
const mutableIds = [
  "replay",
  "open-import",
  "apply",
  "reset-policy",
  "saved-inboxes",
  "reload-inbox",
  "budget",
  "threshold",
];
const confidencePct = (x) => (x >= 0.9995 && x < 1 ? "≥99.9%" : pct(x));
const effectiveQueue = (row) => row.review?.queue ?? row.suggested_queue;
const emptyInspector = $("inspector").innerHTML;
const PAGE_SIZE = 6;
function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  setTimeout(() => ($("toast").hidden = true), 6500);
}
async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-QueuePilot-Token": boot.token,
          },
          body: JSON.stringify(body),
        },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      result.error || "The local server could not complete this action.",
    );
  return result;
}
async function action(button, fn) {
  if (pending) return;
  pending = true;
  const previous = button.disabled;
  mutableIds.forEach((id) => ($(id).disabled = true));
  $("main").setAttribute("aria-busy", "true");
  button.disabled = true;
  $("error").hidden = true;
  try {
    await fn();
  } catch (error) {
    showError(error.message);
  } finally {
    pending = false;
    mutableIds.forEach((id) => ($(id).disabled = false));
    $("main").setAttribute("aria-busy", "false");
    button.disabled = previous;
  }
}
function render(syncControls = false) {
  const m = inbox.metrics;
  $("total").textContent = m.total;
  $("auto").textContent = m.auto;
  $("review").textContent = m.review;
  $("waiting").textContent = m.waiting;
  $("coverage").textContent = pct(m.coverage) + " suggested without review";
  $("slots").textContent =
    `${m.reviewed} completed · ${inbox.budget ?? 0} total slots`;
  $("reviewed").textContent = m.waiting
    ? "Held until review capacity is available."
    : "No uncertain request forced into a route.";
  $("source-summary").textContent = inbox.source?.startsWith("BANKING")
    ? "Public benchmark · not live traffic"
    : "Local import · no external calls";
  $("source-detail").textContent = inbox.source || "No inbox loaded.";
  $("inbox-count").textContent = m.total + " requests";
  if (syncControls) {
    $("budget").max = Math.max(120, m.total);
    $("budget").value = inbox.budget ?? 30;
    $("budget-value").textContent = inbox.budget ?? 30;
    $("threshold").value = Math.round(inbox.threshold * 100);
    $("threshold-value").textContent = pct(inbox.threshold);
    $("threshold").setAttribute("aria-valuetext", pct(inbox.threshold));
    thresholdDirty = false;
  }
  renderDraftStatus();
  const frozen = Math.abs(inbox.threshold - boot.threshold) < 1e-10;
  $("policy-mode").textContent = frozen
    ? "Frozen policy"
    : "Exploratory threshold";
  $("scope-text").textContent = frozen
    ? "Learned on separate policy rows. Sensitive and ambiguous requests still require review. A score is not a guarantee."
    : "Test-set what-if only. Neither the trained model nor its release check changes. Use fresh validation before adoption.";
  $("export").href =
    "/api/export.csv?session=" + encodeURIComponent(inbox.session);
  $("corrections").href =
    "/api/corrections.json?session=" + encodeURIComponent(inbox.session);
  $("audit-stat").hidden = !showTruth;
  $("audit-stat").textContent = m.labeled
    ? `Supplied labels: ${m.labeled}/${m.total} messages. ${m.labeled_auto} labeled automatic suggestions; ${m.wrong_auto ?? "no"} intent-label disagreements${m.auto_accuracy == null ? "" : ` (${pct(m.auto_accuracy)} exact-intent agreement among those suggestions)`}. Intent mismatch does not necessarily mean a different team. Review flags are not corrections. User-supplied labels are unverified.`
    : "No ground-truth labels were supplied. Error rates cannot be measured for this inbox.";
  document
    .querySelectorAll(".filter")
    .forEach((el) =>
      el.classList.toggle("active", el.dataset.filter === filter),
    );
  const query = $("inbox-search").value.trim().toLocaleLowerCase();
  const rows = inbox.rows.filter(
    (r) =>
      (filter === "all" || r.status === filter) &&
      [
        r.id,
        r.text,
        boot.queues[effectiveQueue(r)],
        human(r.candidate.label),
      ].some((value) => value.toLocaleLowerCase().includes(query)),
  );
  if (selected && !rows.some((r) => r.id === selected)) selected = null;
  page = Math.min(page, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1));
  $("messages").innerHTML =
    rows
      .slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
      .map(
        (row) =>
          `<button class="message ${selected === row.id ? "selected" : ""}" data-message="${esc(row.id)}"><span class="message-top"><span class="message-id">${esc(row.id)}</span><span class="state ${esc(row.status)}">${stateLabel(row.status)}</span></span><span class="message-text">${esc(row.text)}</span><span class="message-meta"><span>${row.review ? "Human route: " : ""}${esc(boot.queues[effectiveQueue(row)])}</span><span>·</span><span>Model: ${esc(human(row.candidate.label))}</span><span class="confidence">${confidencePct(row.candidate.confidence)}</span></span>${showTruth ? `<span class="truth">Supplied label: ${esc(row.label ? human(row.label) : "Not supplied")}</span>` : ""}</button>`,
      )
      .join("") ||
    '<p class="empty">No messages in this state. Try another filter or adjust review capacity.</p>';
  $("page-label").textContent = rows.length
    ? `${page * PAGE_SIZE + 1}–${Math.min(rows.length, (page + 1) * PAGE_SIZE)} of ${rows.length}`
    : "0 messages";
  $("prev").disabled = page === 0;
  $("next").disabled = (page + 1) * PAGE_SIZE >= rows.length;
  document.querySelectorAll("[data-message]").forEach((button) =>
    button.addEventListener("click", () => {
      selected = button.dataset.message;
      render();
      if (window.matchMedia("(max-width:800px)").matches)
        $("inspector").scrollIntoView({ block: "start", behavior: "instant" });
    }),
  );
  renderInspector();
}
function renderDraftStatus() {
  if (!inbox) return;
  const dirty = thresholdDirty || Number($("budget").value) !== inbox.budget;
  $("draft-status").hidden = !dirty;
  $("draft-status").textContent = dirty
    ? "Unapplied changes. Counts and decisions still use the saved plan; choose Apply to inbox."
    : "";
}
async function refreshSessions() {
  const result = await api("/api/sessions");
  const sessions = result.sessions;
  if (!sessions.some((s) => s.id === inbox.session))
    sessions.unshift({
      id: inbox.session,
      source: inbox.source,
      created: inbox.created,
      total: inbox.metrics.total,
      reviewed: inbox.metrics.reviewed,
    });
  $("saved-inboxes").innerHTML = sessions
    .map(
      (s) =>
        `<option value="${esc(s.id)}">${esc(new Date(s.created).toLocaleString())} · ${s.source.startsWith("BANKING") ? "Benchmark replay" : "CSV import"} · ${s.total} requests · ${s.reviewed} reviewed · ${esc(s.id.slice(0, 6))}</option>`,
    )
    .join("");
  $("saved-inboxes").value = inbox.session;
}
async function useInbox(state) {
  inbox = state;
  selected = null;
  filter = "all";
  page = 0;
  $("inbox-search").value = "";
  const url = new URL(location.href);
  url.searchParams.set("inbox", inbox.session);
  history.replaceState(null, "", url);
  render(true);
  await refreshSessions();
}
function renderInspector() {
  const row = inbox.rows.find((r) => r.id === selected);
  if (!row) {
    $("inspector").innerHTML = emptyInspector;
    return;
  }
  const review = row.review;
  $("inspector").innerHTML =
    `<div class="eyebrow">${esc(row.id)} / DECISION TRACE</div><h3>${esc(boot.queues[effectiveQueue(row)])}</h3><div class="quote">“${esc(row.text)}”</div><span class="state ${esc(row.status)}">${stateLabel(row.status)}</span><p><small>Original model-suggested team: ${esc(boot.queues[row.suggested_queue])}. No customer action was taken.</small></p><div class="model-row"><span><b>Word baseline</b><small>${esc(human(row.baseline.label))}</small></span><span>${confidencePct(row.baseline.confidence)}</span></div><div class="model-row"><span><b>Word + character candidate</b><small>${esc(human(row.candidate.label))}</small></span><span>${confidencePct(row.candidate.confidence)}</span></div><p><small>Candidate’s nearest alternative: ${esc(human(row.candidate.alternatives[1].label))} (${pct(row.candidate.alternatives[1].confidence)}). Confidence is not a correctness guarantee.</small></p>${row.reasons.map((r) => `<span class="reason">${esc(reasonLabel(r))}</span>`).join("")}${showTruth ? `<p class="truth">Supplied intent: ${esc(row.label ? human(row.label) : "None")}</p>` : ""}${
      review
        ? `<div class="review-log"><b>Saved human route: ${esc(boot.queues[review.queue])}</b><br>${esc(review.note)}<br><small>Persisted locally. Original model prediction and test labels are unchanged.</small></div>`
        : row.status === "review"
          ? `<form id="review-form"><label for="review-queue">Human-selected team queue</label><select id="review-queue">${Object.entries(
              boot.queues,
            )
              .map(
                ([id, name]) =>
                  `<option value="${id}" ${id === row.suggested_queue ? "selected" : ""}>${esc(name)}</option>`,
              )
              .join(
                "",
              )}</select><label for="review-note">Why this route?</label><textarea id="review-note" maxlength="500" rows="3" required placeholder="Explain your routing decision…"></textarea><button class="primary small" type="submit">Save human review →</button><small>Uses one review slot. Adds an audit event—not a training example or a customer action.</small></form>`
          : `<p><small>${row.status === "waiting" ? "Waiting for capacity. Add a review slot to handle this request; it will not be auto-routed to clear the backlog." : "No review rule was triggered at this threshold. This is a routing suggestion, not a resolved ticket."}</small></p>`
    }`;
  const form = $("review-form");
  if (form) {
    const key = inbox.session + ":" + row.id;
    const draft = reviewDrafts.get(key);
    if (draft) {
      $("review-note").value = draft.note;
      $("review-queue").value = draft.queue;
    }
    const saveDraft = () =>
      reviewDrafts.set(key, {
        note: $("review-note").value,
        queue: $("review-queue").value,
      });
    $("review-note").addEventListener("input", saveDraft);
    $("review-queue").addEventListener("change", saveDraft);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      action(form.querySelector("button"), async () => {
        inbox = await api("/api/review", {
          session: inbox.session,
          id: row.id,
          queue: $("review-queue").value,
          note: $("review-note").value,
          version: row.version,
          revision: inbox.revision,
        });
        reviewDrafts.delete(key);
        filter = "reviewed";
        $("inbox-search").value = "";
        render();
        await refreshSessions();
        toast(
          "Human review saved locally. The benchmark and model are unchanged.",
        );
      });
    });
  }
}
function renderReport() {
  const r = boot.report;
  $("comparison").innerHTML =
    `<div class="comparison-grid">${["baseline", "candidate"].map((kind) => `<article><h3>${kind === "baseline" ? "BASELINE / WORDS" : "CANDIDATE / WORDS + CHARACTERS"}</h3><strong>${pct(r.models[kind].accuracy)}</strong><p>Exact-intent accuracy on ${r.counts.test.toLocaleString()} clean official-test messages.<br>${r.models[kind].n_features.toLocaleString()} learned text features.</p></article>`).join("")}<article class="gate-card"><h3>CONFIGURED RELEASE CHECK</h3><strong>${esc(r.gate.decision)}</strong><p>${r.gate.blockers.length ? `${r.gate.blockers.length} slice(s) did not establish the configured requirement.` : "Overall and team-queue checks met the fixed requirements."}<br>Allowed loss: ${r.gate.config.margin * 100} percentage points.</p></article></div><div class="panel"><h2>Follow the changed predictions.</h2><p>${r.changes.improved ?? 0} became correct. ${r.changes.new_mistake ?? 0} became wrong. ${r.changes.both_wrong ?? 0} remained wrong. Averages never replace inspection.</p></div>`;
  $("slice-rows").innerHTML = r.gate.slices
    .map(
      (s) =>
        `<tr><td>${esc(s.slice === "overall" ? "All messages" : boot.queues[s.slice.replace("tag:", "")])}</td><td>${s.n}</td><td>${pct(s.baseline_mean)}</td><td>${pct(s.candidate_mean)}</td><td>${s.interval_95 ? `[${(s.interval_95[0] * 100).toFixed(2)}, ${(s.interval_95[1] * 100).toFixed(2)}] pp` : "Insufficient data"}</td><td><span class="state ${s.quality_status === "pass" ? "auto" : "review"}">${esc(human(s.quality_status))}</span></td></tr>`,
    )
    .join("");
  $("splits").innerHTML = [
    ["fit", "Fit weights"],
    ["calibration", "Calibrate confidence"],
    ["policy", "Choose policy"],
    ["test", "Official test"],
  ]
    .map(
      ([key, name]) =>
        `<div><b>${r.counts[key].toLocaleString()}</b><small>${name}</small></div>`,
    )
    .join("");
  $("dedup-note").textContent =
    `${r.counts.excluded_duplicates} duplicate/overlapping texts excluded before splitting. Protocol seed: 17. All models use the same rows. An identical-model control passes. Test inspection is retrospective; it is not new blind validation.`;
  $("limitations").innerHTML = r.limitations
    .map((text) => `<p>${esc(text)}</p>`)
    .join("");
  renderDiagnostics();
}
const auditGroups = {
  queue_disagreement: [
    "Different mapped team",
    "Automatic suggestion disagrees with the team obtained from the supplied label. That mapping is a project convention, not an operational outcome.",
  ],
  intent_only: [
    "Same mapped team",
    "The exact intent differs from the supplied label, but both map to the same team. Calling all of these wrong team routes would overstate the evidence.",
  ],
  flagged_match: [
    "Flagged, intent matches",
    "The intent matches the supplied label, but review rules still hold the request. A flag is not a corrected mistake; sensitive cases can need review despite a matching intent.",
  ],
  new_mistake: [
    "New intent mismatch",
    "The baseline matches the supplied intent; the candidate does not. This group includes both held and automatically suggested requests.",
  ],
};
function renderDiagnostics() {
  const d = boot.diagnostics;
  $("diagnostic-summary").innerHTML =
    `<div><strong>${d.intent_disagreements}</strong><span>intent-label disagreements</span><small>among ${d.automatic_suggestions.toLocaleString()} automatic suggestions</small></div><div><strong>${d.queue_disagreements}</strong><span>different mapped team</span><small>subset of the ${d.intent_disagreements} intent disagreements</small></div><div><strong>${d.intent_disagreements - d.queue_disagreements}</strong><span>same mapped team</span><small>intent differs; team mapping agrees</small></div>`;
  $("audit-groups").innerHTML = Object.entries(auditGroups)
    .map(
      ([key, [name]]) =>
        `<button class="secondary small ${key === auditGroup ? "active" : ""}" data-audit-group="${key}" aria-pressed="${key === auditGroup}">${esc(name)} <b>${d.groups[key].count.toLocaleString()}</b></button>`,
    )
    .join("");
  document.querySelectorAll("[data-audit-group]").forEach((button) =>
    button.addEventListener("click", () => {
      auditGroup = button.dataset.auditGroup;
      renderDiagnostics();
    }),
  );
  $("reference-case").innerHTML = d.groups[auditGroup].examples
    .map(
      (r) =>
        `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.text.slice(0, 95))}</option>`,
    )
    .join("");
  renderReferenceCase();
}
function renderReferenceCase() {
  const row = boot.diagnostics.groups[auditGroup].examples.find(
    (r) => r.id === $("reference-case").value,
  );
  if (!row) {
    $("reference-detail").textContent = "No cases in this group.";
    return;
  }
  $("reference-detail").innerHTML =
    `<div class="reference-case-heading"><span class="eyebrow">${esc(row.id)} / FROZEN OFFICIAL TEST</span><span class="state ${row.suggestion_without_review ? "auto" : "review"}">${row.suggestion_without_review ? "Suggestion without review" : "Held for review"}</span></div><blockquote>${esc(row.text)}</blockquote><div class="reference-predictions"><div><small>SUPPLIED DATASET LABEL</small><b>${esc(human(row.label))}</b><span>${esc(boot.queues[row.label_queue])}</span></div><div><small>CANDIDATE PREDICTION · ${confidencePct(row.candidate.confidence)}</small><b>${esc(human(row.candidate.label))}</b><span>${esc(boot.queues[row.suggested_queue])}</span></div></div><p class="reference-baseline">Baseline: ${esc(human(row.baseline.label))} · ${confidencePct(row.baseline.confidence)} confidence. ${row.reasons.length ? "Hold reasons: " + row.reasons.map(reasonLabel).map(esc).join(" · ") : "No hold rule triggered under the frozen policy."}</p><p>${esc(auditGroups[auditGroup][1])}</p>`;
}
$("reference-case").addEventListener("change", renderReferenceCase);
function view(name) {
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== name + "-view"));
  document
    .querySelectorAll(".nav")
    .forEach((el) => el.classList.toggle("active", el.dataset.view === name));
  $("breadcrumb").textContent = {
    inbox: "Inbox rehearsal",
    check: "Model check",
    evidence: "Evidence & method",
  }[name];
  history.replaceState(null, "", "#" + name);
  window.scrollTo({ top: 0, behavior: "instant" });
}
document
  .querySelectorAll(".nav")
  .forEach((button) =>
    button.addEventListener("click", () => view(button.dataset.view)),
  );
document.querySelector(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  view("inbox");
});
document.querySelectorAll(".filter").forEach((button) =>
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    page = 0;
    document
      .querySelectorAll(".filter")
      .forEach((el) => el.classList.toggle("active", el === button));
    render();
  }),
);
$("show-truth").addEventListener("change", () => {
  showTruth = $("show-truth").checked;
  render();
});
$("inbox-search").addEventListener("input", () => {
  page = 0;
  render();
});
$("reload-inbox").addEventListener("click", () =>
  action($("reload-inbox"), async () => {
    await useInbox(
      await api(
        "/api/workspace?session=" +
          encodeURIComponent($("saved-inboxes").value),
      ),
    );
    toast(
      "Saved inbox reopened. Unsaved review drafts remain only in this page's memory.",
    );
  }),
);
$("prev").addEventListener("click", () => {
  page--;
  render();
});
$("next").addEventListener("click", () => {
  page++;
  render();
});
$("budget").addEventListener("input", () => {
  $("budget-value").textContent = $("budget").value;
  renderDraftStatus();
});
$("threshold").addEventListener("input", () => {
  thresholdDirty = true;
  $("threshold-value").textContent = $("threshold").value + "%";
  $("threshold").setAttribute("aria-valuetext", $("threshold").value + "%");
  renderDraftStatus();
});
$("apply").addEventListener("click", () =>
  action($("apply"), async () => {
    inbox = await api("/api/plan", {
      session: inbox.session,
      threshold: thresholdDirty
        ? Number($("threshold").value) / 100
        : inbox.threshold,
      budget: Number($("budget").value),
      revision: inbox.revision,
    });
    render(true);
    await refreshSessions();
    toast(
      "Inbox plan updated. The frozen benchmark release check is unchanged.",
    );
  }),
);
$("reset-policy").addEventListener("click", () =>
  action($("reset-policy"), async () => {
    inbox = await api("/api/plan", {
      session: inbox.session,
      threshold: boot.threshold,
      budget: inbox.budget,
      revision: inbox.revision,
    });
    render(true);
    await refreshSessions();
    toast(
      "Frozen threshold restored. The saved review budget is unchanged; unapplied control edits were discarded.",
    );
  }),
);
$("replay").addEventListener("click", () =>
  action($("replay"), async () => {
    const state = await api("/api/replay", {
      limit: 120,
      budget: 30,
      threshold: boot.threshold,
    });
    await useInbox(state);
    toast(
      "New deterministic benchmark replay. Prior imports/reviews remain in the local database.",
    );
  }),
);
$("open-import").addEventListener("click", () =>
  $("import-dialog").showModal(),
);
$("close-import").addEventListener("click", () => $("import-dialog").close());
$("csv-file").addEventListener("change", () => {
  const generation = ++fileGeneration;
  const file = $("csv-file").files[0];
  if (!file) return;
  importFileError = "";
  if (file.size > 500000) {
    importFileError = "CSV is limited to 500 KB.";
    $("import-error").textContent = importFileError;
    return;
  }
  $("import-error").textContent = "Reading file…";
  importRead = file
    .text()
    .then((text) => {
      if (generation !== fileGeneration) return;
      $("csv-text").value = text;
      $("import-error").textContent = "";
    })
    .catch(() => {
      if (generation !== fileGeneration) return;
      importFileError = "Could not read this file.";
      $("import-error").textContent = importFileError;
    });
});
$("csv-text").addEventListener("input", () => {
  fileGeneration++;
  importRead = Promise.resolve();
  importFileError = "";
  $("import-error").textContent = "";
});
$("import-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    try {
      await importRead;
      if (importFileError) throw new Error(importFileError);
      const state = await api("/api/import", {
        csv: $("csv-text").value,
        threshold: boot.threshold,
        budget: 30,
      });
      await useInbox(state);
      $("import-dialog").close();
      toast("CSV processed locally. The reference benchmark is unchanged.");
    } catch (error) {
      $("import-error").textContent = error.message;
      throw error;
    }
  });
});
$("example").addEventListener("click", () => {
  $("try-text").value = "I was charged twice, and now I can't log in.";
  $("try-text").focus();
});
$("try-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const r = await api("/api/predict", { text: $("try-text").value });
    $("try-result").innerHTML =
      `<div class="try-card"><strong>${r.reasons.length ? "Hold for human review" : "Suggested route: " + esc(boot.queues[r.suggested_queue])}</strong><p>Candidate intent: ${esc(human(r.candidate.label))} · ${confidencePct(r.candidate.confidence)} confidence.</p><p>${r.reasons.length ? r.reasons.map((x) => esc(reasonLabel(x))).join(" · ") : "No review rule triggered at the frozen threshold. This can still be wrong."}</p><small>${r.elapsed_ms.toFixed(1)} ms observed local two-model request; not a benchmark p95. No action was taken.</small></div>`;
  });
});
async function init() {
  mutableIds.forEach((id) => ($(id).disabled = true));
  $("main").setAttribute("aria-busy", "true");
  try {
    boot = await api("/api/bootstrap");
    const requested = new URL(location.href).searchParams.get("inbox");
    let state;
    if (requested) {
      try {
        state = await api(
          "/api/workspace?session=" + encodeURIComponent(requested),
        );
      } catch {
        toast(
          "That inbox could not be reopened. Loaded the most recent saved inbox instead.",
        );
      }
    }
    await useInbox(state ?? (await api("/api/workspace")));
    renderReport();
    view(
      ["inbox", "check", "evidence"].includes(location.hash.slice(1))
        ? location.hash.slice(1)
        : "inbox",
    );
    mutableIds.forEach((id) => ($(id).disabled = false));
    $("main").setAttribute("aria-busy", "false");
  } catch (error) {
    showError(
      "Could not load QueuePilot: " +
        error.message +
        " Restart the local server and reload.",
    );
  }
}
init();
