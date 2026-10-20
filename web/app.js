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
    auto: "Auto-routed",
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
  importFileError = "";
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
  button.disabled = true;
  $("error").hidden = true;
  try {
    await fn();
  } catch (error) {
    showError(error.message);
  } finally {
    pending = false;
    button.disabled = previous;
  }
}
function render() {
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
  $("budget").max = Math.max(120, m.total);
  $("budget").value = inbox.budget ?? 30;
  $("budget-value").textContent = inbox.budget ?? 30;
  $("threshold").value = Math.round(inbox.threshold * 100);
  $("threshold-value").textContent = pct(inbox.threshold);
  $("threshold").setAttribute("aria-valuetext", pct(inbox.threshold));
  thresholdDirty = false;
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
    ? `Supplied labels: ${m.labeled}/${m.total} messages. ${m.labeled_auto} labeled auto-routes; ${m.wrong_auto ?? "no"} wrong auto-routes${m.auto_accuracy == null ? "" : ` (${pct(m.auto_accuracy)} accuracy among labeled auto-routes)`}. Review flags are not corrections. User-supplied labels are unverified.`
    : "No ground-truth labels were supplied. Error rates cannot be measured for this inbox.";
  const rows = inbox.rows.filter(
    (r) => filter === "all" || r.status === filter,
  );
  if (selected && !rows.some((r) => r.id === selected)) selected = null;
  page = Math.min(page, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1));
  $("messages").innerHTML =
    rows
      .slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
      .map(
        (row) =>
          `<button class="message ${selected === row.id ? "selected" : ""}" data-message="${esc(row.id)}"><span class="message-top"><span class="message-id">${esc(row.id)}</span><span class="state ${esc(row.status)}">${stateLabel(row.status)}</span></span><span class="message-text">${esc(row.text)}</span><span class="message-meta"><span>${esc(boot.queues[row.suggested_queue])}</span><span>·</span><span>${esc(human(row.candidate.label))}</span><span class="confidence">${pct(row.candidate.confidence)}</span></span>${showTruth ? `<span class="truth">Supplied label: ${esc(row.label ? human(row.label) : "Not supplied")}</span>` : ""}</button>`,
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
function renderInspector() {
  const row = inbox.rows.find((r) => r.id === selected);
  if (!row) {
    $("inspector").innerHTML = emptyInspector;
    return;
  }
  const review = row.review;
  $("inspector").innerHTML =
    `<div class="eyebrow">${esc(row.id)} / DECISION TRACE</div><h3>${esc(boot.queues[row.suggested_queue])}</h3><div class="quote">“${esc(row.text)}”</div><span class="state ${esc(row.status)}">${stateLabel(row.status)}</span><div class="model-row"><span><b>Word baseline</b><small>${esc(human(row.baseline.label))}</small></span><span>${pct(row.baseline.confidence)}</span></div><div class="model-row"><span><b>Word + character candidate</b><small>${esc(human(row.candidate.label))}</small></span><span>${pct(row.candidate.confidence)}</span></div><p><small>Candidate’s nearest alternative: ${esc(human(row.candidate.alternatives[1].label))} (${pct(row.candidate.alternatives[1].confidence)}). Confidence is not a correctness guarantee.</small></p>${row.reasons.map((r) => `<span class="reason">${esc(reasonLabel(r))}</span>`).join("")}${showTruth ? `<p class="truth">Supplied intent: ${esc(row.label ? human(row.label) : "None")}</p>` : ""}${
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
  if (form)
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      action(form.querySelector("button"), async () => {
        inbox = await api("/api/review", {
          session: inbox.session,
          id: row.id,
          queue: $("review-queue").value,
          note: $("review-note").value,
          version: row.version,
        });
        render();
        toast(
          "Human review saved locally. The benchmark and model are unchanged.",
        );
      });
    });
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
}
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
$("prev").addEventListener("click", () => {
  page--;
  render();
});
$("next").addEventListener("click", () => {
  page++;
  render();
});
$("budget").addEventListener(
  "input",
  () => ($("budget-value").textContent = $("budget").value),
);
$("threshold").addEventListener("input", () => {
  thresholdDirty = true;
  $("threshold-value").textContent = $("threshold").value + "%";
  $("threshold").setAttribute("aria-valuetext", $("threshold").value + "%");
});
$("apply").addEventListener("click", () =>
  action($("apply"), async () => {
    inbox = await api("/api/plan", {
      session: inbox.session,
      threshold: thresholdDirty
        ? Number($("threshold").value) / 100
        : inbox.threshold,
      budget: Number($("budget").value),
    });
    render();
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
      budget: Number($("budget").value),
    });
    render();
  }),
);
$("replay").addEventListener("click", () =>
  action($("replay"), async () => {
    inbox = await api("/api/replay", {
      limit: 120,
      budget: 30,
      threshold: boot.threshold,
    });
    selected = null;
    filter = "all";
    page = 0;
    document
      .querySelectorAll(".filter")
      .forEach((el) =>
        el.classList.toggle("active", el.dataset.filter === "all"),
      );
    render();
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
      $("csv-text").value = text;
      $("import-error").textContent = "";
    })
    .catch(() => {
      importFileError = "Could not read this file.";
      $("import-error").textContent = importFileError;
    });
});
$("csv-text").addEventListener("input", () => {
  importFileError = "";
});
$("import-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    try {
      await importRead;
      if (importFileError) throw new Error(importFileError);
      inbox = await api("/api/import", {
        csv: $("csv-text").value,
        threshold: boot.threshold,
        budget: 30,
      });
      selected = null;
      page = 0;
      filter = "all";
      document
        .querySelectorAll(".filter")
        .forEach((el) =>
          el.classList.toggle("active", el.dataset.filter === "all"),
        );
      render();
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
      `<div class="try-card"><strong>${r.reasons.length ? "Hold for human review" : "Suggested route: " + esc(boot.queues[r.suggested_queue])}</strong><p>Candidate intent: ${esc(human(r.candidate.label))} · ${pct(r.candidate.confidence)} confidence.</p><p>${r.reasons.length ? r.reasons.map((x) => esc(reasonLabel(x))).join(" · ") : "No review rule triggered at the frozen threshold. This can still be wrong."}</p><small>${r.elapsed_ms.toFixed(1)} ms observed local two-model request; not a benchmark p95. No action was taken.</small></div>`;
  });
});
async function init() {
  try {
    boot = await api("/api/bootstrap");
    inbox = await api("/api/workspace");
    render();
    renderReport();
    view(
      ["inbox", "check", "evidence"].includes(location.hash.slice(1))
        ? location.hash.slice(1)
        : "inbox",
    );
  } catch (error) {
    showError(
      "Could not load QueuePilot: " +
        error.message +
        " Restart the local server and reload.",
    );
  }
}
init();
