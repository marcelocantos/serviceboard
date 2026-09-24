const $ = (selector) => document.querySelector(selector);
const state = {
  services: [],
  errors: {},
  selected: null,
  filter: "all",
  tab: "overview",
  token: null,
  busy: false,
  lastTab: null,
  lastSelected: null,
  refreshing: false,
  searchActive: false,
  selectionBeforeSearch: null,
};

function statusClass(status) {
  if (["running", "started"].includes(status)) return "running";
  if (["starting", "stopping", "backoff", "scheduled"].includes(status))
    return "attention";
  if (["fatal", "error", "exited"].includes(status)) return "failed";
  return "stopped";
}

function statusBucket(status) {
  const kind = statusClass(status);
  if (kind === "running") return 0;
  if (kind === "stopped") return 2;
  return 1;
}

function compareServices(left, right) {
  return (
    statusBucket(left.status) - statusBucket(right.status) ||
    left.name.localeCompare(right.name) ||
    left.source.localeCompare(right.source)
  );
}

function visibleServices() {
  const needle = $("#search").value.trim().toLowerCase();
  return state.services
    .filter(
      (item) =>
        (state.filter === "all" || state.filter === item.source) &&
        item.name.toLowerCase().includes(needle),
    )
    .sort(compareServices);
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    element.hidden = true;
  }, 4400);
}

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const body = await response.json();
  if (!response.ok)
    throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function refreshStats() {
  const services = state.services;
  const running = services.filter(
    (item) => statusClass(item.status) === "running",
  ).length;
  const attention = services.filter((item) =>
    ["attention", "failed"].includes(statusClass(item.status)),
  ).length;
  $("#total-count").textContent = services.length;
  $("#running-count").textContent = running;
  $("#attention-count").textContent = attention;
  $("#all-count").textContent = services.length;
  $("#supervisor-count").textContent = services.filter(
    (item) => item.source === "Supervisor",
  ).length;
  $("#homebrew-count").textContent = services.filter(
    (item) => item.source === "Homebrew",
  ).length;
  $("#system-state").textContent = Object.keys(state.errors).length
    ? "Partial"
    : "Connected";
  $("#system-detail").textContent = Object.keys(state.errors).length
    ? "A service source is unavailable"
    : "All sources responding";
  const errors = $("#errors");
  errors.replaceChildren();
  for (const [source, message] of Object.entries(state.errors)) {
    const line = document.createElement("div");
    line.textContent = `${source}: ${message}`;
    errors.append(line);
  }
  errors.hidden = !Object.keys(state.errors).length;
}

function renderList() {
  const services = visibleServices();
  $("#list-count").textContent = `(${services.length})`;
  const list = $("#service-list");
  list.replaceChildren();
  for (const [bucket, title] of [
    [0, "RUNNING"],
    [1, "NEEDS ATTENTION"],
    [2, "STOPPED"],
  ]) {
    const group = services.filter(
      (item) => statusBucket(item.status) === bucket,
    );
    if (!group.length) continue;
    const heading = document.createElement("div");
    heading.className = "service-group";
    heading.textContent = `${title}  /  ${group.length}`;
    list.append(heading);
    for (const item of group) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `service-item${item.id === state.selected ? " selected" : ""}`;
      row.dataset.serviceId = item.id;
      if (item.id === state.selected) row.setAttribute("aria-current", "true");
      row.setAttribute(
        "aria-label",
        `${item.name}, ${item.status}, ${item.source}`,
      );
      const icon = document.createElement("span");
      icon.className = `service-icon${item.source === "Homebrew" ? " homebrew" : ""}`;
      const logo = document.createElement("img");
      logo.src =
        item.source === "Homebrew"
          ? "/assets/homebrew.svg"
          : "/assets/supervisor.png";
      logo.alt = "";
      icon.append(logo);
      const main = document.createElement("span");
      main.className = "service-main";
      const name = document.createElement("span");
      name.className = "service-name";
      name.textContent = item.name;
      const sub = document.createElement("span");
      sub.className = "service-sub";
      sub.textContent = `${item.source}${item.detail ? ` · ${item.detail}` : ""}`;
      main.append(name, sub);
      const label = document.createElement("span");
      label.className = "service-state";
      const dot = document.createElement("span");
      dot.className = `state-dot ${statusClass(item.status)}`;
      label.append(dot, document.createTextNode(item.status.toUpperCase()));
      const chevron = document.createElement("span");
      chevron.className = "service-chevron";
      chevron.textContent = "›";
      row.append(icon, main, label, chevron);
      row.addEventListener("click", () => select(item.id));
      list.append(row);
    }
  }
  if (!services.length) {
    const empty = document.createElement("div");
    empty.className = "service-group";
    empty.textContent = "NO MATCHING SERVICES";
    list.append(empty);
  }
}

function scrollSelectedIntoView() {
  const row = Array.from(document.querySelectorAll(".service-item")).find(
    (item) => item.dataset.serviceId === state.selected,
  );
  row?.scrollIntoView({ block: "nearest" });
}

function cell(label, value) {
  const item = document.createElement("div");
  item.className = "overview-cell";
  const small = document.createElement("small");
  small.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value;
  item.append(small, strong);
  return item;
}

async function renderTab() {
  const item = state.services.find((service) => service.id === state.selected);
  if (!item) return;
  const selected = item.id;
  const body = $("#tab-body");
  body.replaceChildren();
  if (state.tab === "overview") {
    const heading = document.createElement("div");
    heading.className = "overview-label";
    heading.textContent = "PROCESS INFORMATION";
    const grid = document.createElement("div");
    grid.className = "overview-grid";
    grid.append(
      cell("STATUS", item.status.toUpperCase()),
      cell("MANAGED BY", item.source),
      cell("SERVICE NAME", item.name),
      cell("DETAIL", item.detail || "—"),
    );
    body.append(heading, grid);
    return;
  }
  if (state.tab === "stdout" || state.tab === "stderr") {
    const toolbar = document.createElement("div");
    toolbar.className = "log-toolbar";
    toolbar.textContent = `${state.tab.toUpperCase()} · LAST 32 KB`;
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.textContent = "↻ Refresh log";
    refresh.addEventListener("click", renderTab);
    toolbar.append(refresh);
    const log = document.createElement("pre");
    log.className = "log-output";
    log.textContent = "Loading log…";
    body.append(toolbar, log);
    try {
      const result = await api(
        `/api/log?id=${encodeURIComponent(selected)}&stream=${state.tab}`,
      );
      if (selected === state.selected && body.contains(log))
        log.textContent = result.text || "No output yet.";
    } catch (error) {
      if (selected === state.selected && body.contains(log))
        log.textContent = error.message;
    }
    return;
  }
  const intro = document.createElement("p");
  intro.className = "dashboard-intro";
  intro.textContent = "Looking for a dashboard advertised by this service…";
  body.append(intro);
  try {
    const { dashboard } = await api(
      `/api/dashboard?id=${encodeURIComponent(selected)}`,
    );
    if (selected !== state.selected || !body.contains(intro)) return;
    if (!dashboard || !dashboard.views.length) {
      intro.textContent =
        "No dashboard is registered for this service. Add its local origin to ~/.config/serviceboard/dashboards.json when it supports the Serviceboard manifest.";
      return;
    }
    intro.textContent = `${dashboard.title} provides ${dashboard.views.length} view${dashboard.views.length === 1 ? "" : "s"}.`;
    const links = document.createElement("div");
    links.className = "dashboard-links";
    const frame = document.createElement("iframe");
    frame.className = "dashboard-frame";
    frame.title = dashboard.views[0].title;
    frame.setAttribute(
      "sandbox",
      "allow-scripts allow-forms allow-same-origin",
    );
    for (const view of dashboard.views) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = view.title;
      button.addEventListener("click", () => {
        frame.src = view.url;
        frame.title = view.title;
        open.href = view.url;
      });
      links.append(button);
    }
    const open = document.createElement("a");
    open.href = dashboard.views[0].url;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.textContent = "Open separately ↗";
    links.append(open);
    frame.src = dashboard.views[0].url;
    body.append(links, frame);
  } catch (error) {
    if (selected === state.selected && body.contains(intro))
      intro.textContent = error.message;
  }
}

function renderDetail() {
  const item = state.services.find((service) => service.id === state.selected);
  $("#detail-empty").hidden = Boolean(item);
  $("#detail-content").hidden = !item;
  if (!item) return;
  $("#detail-source").textContent = item.source;
  $("#detail-name").textContent = item.name;
  $("#detail-description").textContent =
    item.detail || `Managed by ${item.source}`;
  const pill = $("#detail-state");
  pill.textContent = item.status;
  pill.className = `state-pill ${statusClass(item.status)}`;
  const running = statusClass(item.status) === "running";
  $("#action-start").disabled = state.busy || running;
  $("#action-stop").disabled = state.busy || !running;
  $("#action-restart").disabled = state.busy || !running;
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.tab === state.tab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  $("#updated-at").textContent = `UPDATED ${new Date().toLocaleTimeString()}`;
  const keepDashboard =
    state.tab === "dashboard" &&
    state.lastTab === "dashboard" &&
    state.lastSelected === state.selected;
  state.lastTab = state.tab;
  state.lastSelected = state.selected;
  if (!keepDashboard) renderTab();
}

function select(id, scroll = false) {
  state.selected = id;
  if (!state.searchActive) state.selectionBeforeSearch = id;
  state.tab = "overview";
  renderList();
  renderDetail();
  if (scroll) scrollSelectedIntoView();
}

function dismissSearch(restorePrevious) {
  const search = $("#search");
  if (state.searchActive) {
    if (
      restorePrevious &&
      state.services.some((item) => item.id === state.selectionBeforeSearch)
    ) {
      state.selected = state.selectionBeforeSearch;
      state.tab = "overview";
      renderDetail();
    }
    search.value = "";
    state.searchActive = false;
    state.selectionBeforeSearch = state.selected;
    renderList();
    scrollSelectedIntoView();
  }
}

async function refreshServices(quiet = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const result = await api("/api/services");
    state.services = result.services;
    state.errors = result.errors;
    refreshStats();
    if (
      !state.selected ||
      !state.services.some((item) => item.id === state.selected)
    )
      state.selected = [...state.services].sort(compareServices)[0]?.id || null;
    if (!state.searchActive) state.selectionBeforeSearch = state.selected;
    renderList();
    renderDetail();
    if (!quiet) toast("Service inventory refreshed");
  } catch (error) {
    $("#system-state").textContent = "Offline";
    $("#system-detail").textContent = error.message;
    if (!quiet) toast(error.message, true);
  } finally {
    state.refreshing = false;
  }
}

async function act(action) {
  if (!state.selected || state.busy) return;
  const item = state.services.find((service) => service.id === state.selected);
  if (!item) return;
  state.busy = true;
  renderDetail();
  try {
    const result = await api("/api/action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Serviceboard-Token": state.token,
      },
      body: JSON.stringify({ id: item.id, action }),
    });
    toast(result.output || `${action} requested for ${item.name}`);
    await refreshServices(true);
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.busy = false;
    renderDetail();
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const search = $("#search");
  search.focus();
  $("#clock").textContent = new Date().toLocaleTimeString();
  setInterval(() => {
    $("#clock").textContent = new Date().toLocaleTimeString();
  }, 1000);
  $("#refresh").addEventListener("click", () => refreshServices());
  search.addEventListener("input", () => {
    const active = Boolean(search.value.trim());
    if (active && !state.searchActive)
      state.selectionBeforeSearch = state.selected;
    state.searchActive = active;
    if (!active) state.selectionBeforeSearch = state.selected;
    if (active) {
      const first = visibleServices()[0];
      if (first) {
        select(first.id, true);
        return;
      }
    }
    renderList();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      search.focus();
      return;
    }
    if (event.target === search && event.key === "Enter") {
      event.preventDefault();
      dismissSearch(false);
      return;
    }
    if (event.target === search && event.key === "Escape") {
      event.preventDefault();
      dismissSearch(true);
      return;
    }
    if (
      (event.key === "ArrowDown" || event.key === "ArrowUp") &&
      !event.altKey &&
      !event.ctrlKey &&
      !event.metaKey &&
      (event.target === search ||
        event.target === document.body ||
        event.target.classList?.contains("service-item"))
    ) {
      const services = visibleServices();
      if (!services.length) return;
      event.preventDefault();
      const current = services.findIndex((item) => item.id === state.selected);
      const next =
        current < 0
          ? event.key === "ArrowDown"
            ? 0
            : services.length - 1
          : Math.max(
              0,
              Math.min(
                services.length - 1,
                current + (event.key === "ArrowDown" ? 1 : -1),
              ),
            );
      select(services[next].id, true);
    }
  });
  for (const filter of document.querySelectorAll(".filter"))
    filter.addEventListener("click", () => {
      state.filter = filter.dataset.filter;
      for (const button of document.querySelectorAll(".filter"))
        button.classList.toggle("active", button === filter);
      renderList();
    });
  for (const tab of document.querySelectorAll(".tab"))
    tab.addEventListener("click", () => {
      state.tab = tab.dataset.tab;
      renderDetail();
    });
  for (const button of document.querySelectorAll(".action-row button"))
    button.addEventListener("click", () => act(button.dataset.action));
  try {
    state.token = (await api("/api/session")).token;
    await refreshServices(true);
    setInterval(() => refreshServices(true), 15000);
  } catch (error) {
    toast(error.message, true);
  }
});
