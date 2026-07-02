/* SPATAIL Developer Log — render + interaction
 * Reads window.SPATAIL_LOG (from data/entries.js). No dependencies. */
(function () {
  "use strict";

  var LOG = Array.isArray(window.SPATAIL_LOG) ? window.SPATAIL_LOG.slice() : [];

  var CATEGORY = {
    feature:   { label: "Feature",   color: "#6ea8ff" },
    fix:       { label: "Fix",       color: "#f06969" },
    refactor:  { label: "Refactor",  color: "#b56bff" },
    tooling:   { label: "Tooling",   color: "#57d19c" },
    docs:      { label: "Docs",      color: "#8c91a1" },
    direction: { label: "Direction", color: "#f5ba42" }
  };
  var STATUS = {
    shipped:       { label: "Shipped",     color: "#57d19c" },
    "in-progress": { label: "In progress", color: "#f5ba42" },
    draft:         { label: "Draft",       color: "#8c91a1" }
  };

  var state = { query: "", category: "all", tag: null };

  // ---- helpers -------------------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function parseDate(s) {
    var p = String(s || "").split("-");
    return new Date(+p[0], (+p[1] || 1) - 1, +p[2] || 1);
  }
  function fmtDate(s) {
    try {
      return parseDate(s).toLocaleDateString("en-US",
        { year: "numeric", month: "short", day: "numeric" });
    } catch (e) { return s; }
  }
  function monthLabel(s) {
    try {
      return parseDate(s).toLocaleDateString("en-US", { year: "numeric", month: "long" });
    } catch (e) { return s; }
  }
  function cat(c) { return CATEGORY[c] || { label: c || "Note", color: "#8c91a1" }; }
  function stat(s) { return STATUS[s] || { label: s || "—", color: "#8c91a1" }; }

  // ---- filtering -----------------------------------------------------------
  function matches(entry) {
    if (state.category !== "all" && entry.category !== state.category) return false;
    if (state.tag && (!entry.tags || entry.tags.indexOf(state.tag) === -1)) return false;
    if (state.query) {
      var hay = [
        entry.title, entry.summary, entry.area, entry.why,
        (entry.details || []).join(" "), (entry.tags || []).join(" "),
        (entry.files || []).join(" ")
      ].join(" ").toLowerCase();
      if (hay.indexOf(state.query.toLowerCase()) === -1) return false;
    }
    return true;
  }

  function sorted(list) {
    return list.slice().sort(function (a, b) {
      return parseDate(b.date) - parseDate(a.date);
    });
  }

  // ---- rendering -----------------------------------------------------------
  function renderStats() {
    var total = LOG.length;
    var shipped = LOG.filter(function (e) { return e.status === "shipped"; }).length;
    var progress = LOG.filter(function (e) {
      return e.status === "in-progress" || e.status === "draft";
    }).length;
    var last = LOG.reduce(function (m, e) {
      return parseDate(e.date) > parseDate(m) ? e.date : m;
    }, "1970-01-01");

    var cells = [
      { num: total, label: "Entries" },
      { num: shipped, label: "Shipped" },
      { num: progress, label: "Open" },
      { num: fmtDate(last), label: "Last update" }
    ];
    document.getElementById("stats").innerHTML = cells.map(function (c) {
      return '<div class="stat"><div class="num">' + esc(c.num) +
        '</div><div class="label">' + esc(c.label) + "</div></div>";
    }).join("");
  }

  function renderFilters() {
    var counts = {};
    LOG.forEach(function (e) { counts[e.category] = (counts[e.category] || 0) + 1; });

    var chips = ['<button class="chip' + (state.category === "all" ? " active" : "") +
      '" data-cat="all">All <span style="opacity:.6">' + LOG.length + "</span></button>"];

    Object.keys(CATEGORY).forEach(function (key) {
      if (!counts[key]) return;
      var c = CATEGORY[key];
      chips.push('<button class="chip' + (state.category === key ? " active" : "") +
        '" data-cat="' + key + '" style="--c:' + c.color + '">' +
        '<span class="dot"></span>' + esc(c.label) +
        ' <span style="opacity:.6">' + counts[key] + "</span></button>");
    });

    if (state.tag) {
      chips.push('<button class="chip active" data-cleartag="1" style="--c:#b56bff">' +
        "#" + esc(state.tag) + " ✕</button>");
    }
    document.getElementById("filters").innerHTML = chips.join("");
  }

  function cardHTML(entry) {
    var c = cat(entry.category), st = stat(entry.status);
    var meta = ['<span>' + esc(fmtDate(entry.date)) + "</span>"];
    if (entry.area) meta.push("<span>" + esc(entry.area) + "</span>");

    var body = [];
    if (entry.details && entry.details.length) {
      body.push('<div class="section-label">What changed</div><ul class="details">' +
        entry.details.map(function (d) { return "<li>" + esc(d) + "</li>"; }).join("") + "</ul>");
    }
    if (entry.why) {
      body.push('<div class="section-label">Why</div><div class="why">' + esc(entry.why) + "</div>");
    }
    if (entry.files && entry.files.length) {
      body.push('<div class="section-label">Files</div><div class="files">' +
        entry.files.map(function (f) { return '<span class="file">' + esc(f) + "</span>"; }).join("") + "</div>");
    }
    if (entry.commits && entry.commits.length) {
      body.push('<div class="section-label">Commits</div><div class="commits">' +
        entry.commits.map(function (h) { return '<span class="commit">' + esc(h) + "</span>"; }).join("") + "</div>");
    }
    if (entry.tags && entry.tags.length) {
      body.push('<div class="tags">' +
        entry.tags.map(function (t) { return '<span class="tag" data-tag="' + esc(t) + '">' + esc(t) + "</span>"; }).join("") + "</div>");
    }

    var draftFlag = entry.status === "draft"
      ? '<span class="draft-flag">draft</span>' : "";

    return '<article class="card" style="--c:' + c.color + '" data-id="' + esc(entry.id) + '">' +
      '<div class="card-head" data-toggle="1">' +
        '<div class="grow">' +
          '<div class="card-title-row">' +
            '<h3 class="card-title">' + esc(entry.title) + "</h3>" +
            '<span class="badge" style="--c:' + c.color + '">' + esc(c.label) + "</span>" +
            '<span class="status" style="--sc:' + st.color + '"><span class="sdot"></span>' + esc(st.label) + "</span>" +
            draftFlag +
          "</div>" +
          '<div class="card-meta">' + meta.join("") + "</div>" +
          '<p class="card-summary">' + esc(entry.summary) + "</p>" +
        "</div>" +
        '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      "</div>" +
      '<div class="card-body">' + body.join("") + "</div>" +
    "</article>";
  }

  function renderTimeline() {
    var list = sorted(LOG.filter(matches));
    var root = document.getElementById("timeline");
    if (!list.length) {
      root.innerHTML = '<div class="empty">No entries match your filters.</div>';
      return;
    }
    var html = "", currentMonth = null;
    list.forEach(function (entry) {
      var m = monthLabel(entry.date);
      if (m !== currentMonth) {
        currentMonth = m;
        html += '<div class="month">' + esc(m) + "</div>";
      }
      html += cardHTML(entry);
    });
    root.innerHTML = html;
  }

  function renderFooter() {
    document.getElementById("footer-meta").textContent =
      LOG.length + " entries · log lives in data/entries.js";
  }

  function rerender() { renderFilters(); renderTimeline(); }

  // ---- events --------------------------------------------------------------
  function onClick(e) {
    var chip = e.target.closest(".chip");
    if (chip) {
      if (chip.getAttribute("data-cleartag")) { state.tag = null; }
      else { state.category = chip.getAttribute("data-cat"); }
      rerender();
      return;
    }
    var tag = e.target.closest(".tag");
    if (tag) {
      state.tag = tag.getAttribute("data-tag");
      window.scrollTo({ top: 0, behavior: "smooth" });
      rerender();
      return;
    }
    var head = e.target.closest(".card-head");
    if (head) { head.parentNode.classList.toggle("expanded"); }
  }

  function init() {
    renderStats();
    renderFooter();
    rerender();
    document.body.addEventListener("click", onClick);
    document.getElementById("search").addEventListener("input", function (e) {
      state.query = e.target.value.trim();
      renderTimeline();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
