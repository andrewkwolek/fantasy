// Shared hover tooltip for every chart mark, plus line-series highlighting.
(function () {
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

  // Line charts: hovering or clicking a legend chip promotes one series.
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

  // Season jump. The prefix matters on a static host served from a sub-path:
  // a bare "/season/2025" would leave the site entirely.
  const meta = document.querySelector('meta[name="base-path"]');
  const basePath = (meta && meta.content) || "";
  const jump = document.getElementById("season-jump");
  if (jump) {
    jump.addEventListener("change", () => {
      if (jump.value) location.href = basePath + "/" + jump.value;
    });
  }

  // Theme toggle: light -> dark -> follow-system.
  const root = document.documentElement;
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  const btn = document.getElementById("theme-btn");
  if (btn) {
    const label = () => {
      const t = root.getAttribute("data-theme");
      btn.textContent = t === "dark" ? "Dark" : t === "light" ? "Light" : "Auto";
    };
    label();
    btn.addEventListener("click", () => {
      const t = root.getAttribute("data-theme");
      if (!t) { root.setAttribute("data-theme", "light"); localStorage.setItem("theme", "light"); }
      else if (t === "light") { root.setAttribute("data-theme", "dark"); localStorage.setItem("theme", "dark"); }
      else { root.removeAttribute("data-theme"); localStorage.removeItem("theme"); }
      label();
    });
  }
})();
