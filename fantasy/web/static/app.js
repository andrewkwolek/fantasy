// Progressive enhancement for the league site. Everything here is additive:
// the page is fully readable with JS off, this layer adds the tooltip, the
// avatar fallback, column sorting, and the theme toggle.
(function () {
  "use strict";

  /* ------------------------------------------------------------- tooltip */
  const tip = document.createElement("div");
  tip.id = "tip";
  document.body.appendChild(tip);

  function place(e) {
    const pad = 14;
    let x = e.clientX + pad, y = e.clientY + pad;
    const r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  document.addEventListener("mouseover", (e) => {
    const mark = e.target.closest("[data-tip]");
    if (!mark) return;
    tip.textContent = mark.getAttribute("data-tip");
    tip.classList.add("on");
    place(e);
  });
  document.addEventListener("mousemove", (e) => {
    if (tip.classList.contains("on")) place(e);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest("[data-tip]")) tip.classList.remove("on");
  });

  // Touch has no hover, so every heatmap cell and head-to-head record would be
  // unreadable on a phone. Tap a mark to pin its tooltip, tap away to dismiss.
  document.addEventListener("touchstart", (e) => {
    const mark = e.target.closest("[data-tip]");
    if (!mark) { tip.classList.remove("on"); return; }
    const t = e.touches[0];
    tip.textContent = mark.getAttribute("data-tip");
    tip.classList.add("on");
    place({ clientX: t.clientX, clientY: t.clientY });
  }, { passive: true });
  addEventListener("scroll", () => tip.classList.remove("on"), { passive: true });

  /* ------------------------------------------- line charts: one series up */
  function setSeries(root, key, on) {
    root.querySelectorAll('[data-serie="' + CSS.escape(key) + '"]')
      .forEach((el) => el.classList.toggle("on", on));
  }
  document.querySelectorAll(".legend.wrap").forEach((legend) => {
    const root = legend.parentElement;
    legend.querySelectorAll("button.toggle").forEach((btn) => {
      const key = btn.dataset.serie;
      btn.addEventListener("mouseenter", () => setSeries(root, key, true));
      btn.addEventListener("mouseleave", () => {
        if (!btn.classList.contains("on")) setSeries(root, key, false);
      });
      btn.addEventListener("click", () => setSeries(root, key, !btn.classList.contains("on")));
    });
  });
  document.querySelectorAll(".chart.lines .serie").forEach((g) => {
    g.addEventListener("mouseenter", () => g.classList.add("on"));
    g.addEventListener("mouseleave", () => {
      const btn = document.querySelector(
        'button.toggle.on[data-serie="' + CSS.escape(g.dataset.serie) + '"]');
      if (!btn) g.classList.remove("on");
    });
  });

  /* ------------------------------------------------------------- avatars */
  // A dead ESPN logo URL used to render as a broken-image glyph. Drop the
  // <img> instead and the monogram already sitting underneath shows through.
  document.querySelectorAll("img.logo").forEach((img) => {
    const drop = () => img.remove();
    img.addEventListener("error", drop);
    if (img.complete && img.naturalWidth === 0) drop();
  });

  // Tint the monogram from the team name so the fallbacks are not a wall of
  // identical grey chips. Six on-palette tints, picked deterministically.
  document.querySelectorAll(".avatar").forEach((av) => {
    const cell = av.closest(".team-cell");
    const nm = cell && cell.querySelector(".nm");
    const seed = (nm ? nm.textContent : av.dataset.mono || "").trim();
    let h = 0;
    for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
    av.classList.add("av-" + (h % 6));
  });

  /* -------------------------------------------------------- header state */
  const header = document.querySelector("header.top");
  if (header) {
    const onScroll = () => header.classList.toggle("is-stuck", scrollY > 4);
    addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ------------------------------------------ horizontal scroll affordance */
  // Fade the edge only where there is actually more table to reach.
  document.querySelectorAll(".tbl-wrap").forEach((wrap) => {
    const sync = () => {
      const max = wrap.scrollWidth - wrap.clientWidth;
      wrap.classList.toggle("can-l", wrap.scrollLeft > 2);
      wrap.classList.toggle("can-r", wrap.scrollLeft < max - 2);
    };
    wrap.addEventListener("scroll", sync, { passive: true });
    addEventListener("resize", sync);
    sync();
  });

  /* ------------------------------------------------------ sortable tables */
  const RECORD = /^(\d+)-(\d+)(?:-(\d+))?$/;

  // Sort key for one cell. `data-sort` wins; otherwise the team name alone
  // (never the owner line or the monogram) and then the visible text.
  function keyOf(td) {
    if (td.dataset.sort !== undefined) {
      const n = parseFloat(td.dataset.sort);
      return Number.isNaN(n) ? td.dataset.sort.toLowerCase() : n;
    }
    const nm = td.querySelector(".nm");
    const raw = (nm ? nm.textContent : td.textContent).trim();
    if (raw === "" || raw === "—" || raw === "-") return null;

    const rec = raw.match(RECORD);
    if (rec) {                       // "10-4" / "10-4-1" -> win percentage
      const w = +rec[1], l = +rec[2], t = +(rec[3] || 0);
      const games = w + l + t;
      return games ? (w + t / 2) / games : 0;
    }
    // Strip thousands separators and trailing units (%, " W", " pts", stars).
    const num = raw.replace(/,/g, "").match(/^[+-]?\d*\.?\d+/);
    if (num && /^[+-]?[\d.,]+\s*(%|W|pts|★+)?$/i.test(raw)) return parseFloat(num[0]);
    return raw.toLowerCase();
  }

  function compare(a, b) {
    if (a === null) return 1;        // blanks always sink, either direction
    if (b === null) return -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  }

  document.querySelectorAll("table.sortable").forEach((table) => {
    const head = table.tHead && table.tHead.rows[0];
    const body = table.tBodies[0];
    if (!head || !body) return;
    const original = Array.from(body.rows);

    Array.from(head.cells).forEach((th, col) => {
      if (th.dataset.nosort !== undefined) return;
      th.classList.add("sort");
      th.tabIndex = 0;
      th.setAttribute("role", "button");

      // Numbers are most useful biggest-first; names A-Z. Third click puts the
      // table back in document order, which for standings is the real placing.
      const sort = () => {
        const was = th.classList.contains("asc") ? "asc"
                  : th.classList.contains("desc") ? "desc" : "";
        const first = typeof keyOf(body.rows[0].cells[col]) === "number" ? "desc" : "asc";
        const next = was === "" ? first : (was === first ? (first === "asc" ? "desc" : "asc") : "");

        Array.from(head.cells).forEach((h) => {
          h.classList.remove("asc", "desc");
          h.removeAttribute("aria-sort");
        });

        let rows = original.slice();
        if (next) {
          const dir = next === "asc" ? 1 : -1;
          rows = rows
            .map((tr, i) => [tr, keyOf(tr.cells[col]), i])
            .sort((x, y) => compare(x[1], y[1]) * dir || x[2] - y[2])
            .map((x) => x[0]);
          th.classList.add(next);
          th.setAttribute("aria-sort", next === "asc" ? "ascending" : "descending");
        }
        rows.forEach((tr) => body.appendChild(tr));
      };

      th.addEventListener("click", sort);
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
      });
    });
  });

  /* --------------------------------------------------------- season jump */
  // The prefix matters on a static host served from a sub-path: a bare
  // "/season/2025" would leave the site entirely.
  const meta = document.querySelector('meta[name="base-path"]');
  const basePath = (meta && meta.content) || "";
  const jump = document.getElementById("season-jump");
  if (jump) {
    jump.addEventListener("change", () => {
      if (jump.value) location.href = basePath + "/" + jump.value;
    });
  }

  /* -------------------------------------------------------- theme toggle */
  // light -> dark -> follow-system. base.html already applied any saved
  // choice before first paint; this only relabels and cycles it.
  const ICONS = {
    light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.2v2M12 19.8v2M4.4 4.4l1.4 1.4M18.2 18.2l1.4 1.4M2.2 12h2M19.8 12h2M4.4 19.6l1.4-1.4M18.2 5.8l1.4-1.4"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20.8 13.4A8.8 8.8 0 1 1 10.6 3.2a6.9 6.9 0 0 0 10.2 10.2z"/></svg>',
    auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>',
  };
  const LABEL = {
    light: "Theme: light — switch to dark",
    dark: "Theme: dark — follow the system",
    auto: "Theme: follows the system — switch to light",
  };
  const root = document.documentElement;
  const btn = document.getElementById("theme-btn");
  if (btn) {
    const paint = () => {
      const t = root.getAttribute("data-theme") || "auto";
      btn.innerHTML = ICONS[t];
      btn.setAttribute("aria-label", LABEL[t]);
      btn.title = LABEL[t];
    };
    paint();
    btn.addEventListener("click", () => {
      const t = root.getAttribute("data-theme");
      try {
        if (!t) { root.setAttribute("data-theme", "light"); localStorage.setItem("theme", "light"); }
        else if (t === "light") { root.setAttribute("data-theme", "dark"); localStorage.setItem("theme", "dark"); }
        else { root.removeAttribute("data-theme"); localStorage.removeItem("theme"); }
      } catch (e) { /* private mode: the toggle still works for this page */ }
      paint();
    });
  }
})();
