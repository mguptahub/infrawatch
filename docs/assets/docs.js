/* InfraWatch Docs — Shared Components & Behaviour */

(function () {
  const root = document.documentElement;
  const storageKey = "infrawatch-theme";
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

  const resolvedTheme = (t) =>
    t === "auto" ? (systemTheme.matches ? "dark" : "light") : t;

  const applyTheme = (t) => {
    const actual = resolvedTheme(t);
    root.setAttribute("data-theme", actual);
    root.setAttribute("data-theme-mode", t);
    if (themeMeta)
      themeMeta.setAttribute(
        "content",
        actual === "dark" ? "#0a0a0a" : "#f8fafc"
      );
    document
      .querySelectorAll("[data-theme-btn]")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.themeBtn === t))
      );
  };

  /* ── Inject navbar ─────────────────────────────────────── */
  const nav = document.querySelector(".docs-nav");
  if (nav) {
    nav.innerHTML = `
      <div class="docs-nav-inner">
        <div class="docs-nav-left">
          <a href="../" class="docs-logo">
            <span class="docs-logo-glyph">⬡</span>
            InfraWatch
          </a>
          <span class="docs-label">Docs</span>
        </div>
        <div class="docs-nav-right">
          <button class="docs-menu-btn" aria-label="Toggle menu">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </button>
          <div class="theme-toggle" role="group" aria-label="Theme mode">
            <button type="button" data-theme-btn="light" aria-pressed="false">Light</button>
            <button type="button" data-theme-btn="dark" aria-pressed="false">Dark</button>
            <button type="button" data-theme-btn="auto" aria-pressed="true">System</button>
          </div>
        </div>
      </div>`;
  }

  /* ── Inject sidebar ────────────────────────────────────── */
  const sidebar = document.querySelector(".docs-sidebar");
  if (sidebar) {
    const links = [
      { heading: "Getting Started", items: [
        { href: "./",                label: "Overview" },
        { href: "./docker.html",     label: "Docker Compose" },
        { href: "./kubernetes.html", label: "Kubernetes (Helm)" },
      ]},
      { heading: "Reference", items: [
        { href: "./configuration.html", label: "Configuration" },
        { href: "./iam.html",           label: "IAM Setup" },
      ]},
    ];

    const current = window.location.pathname.split("/").pop() || "index.html";
    const normalize = (href) => {
      if (href === "./") return "index.html";
      return href.replace("./", "");
    };

    const sectionsHTML = links
      .map(
        (section) => `
      <div class="sidebar-section">
        <div class="sidebar-heading">${section.heading}</div>
        ${section.items
          .map((item) => {
            const active = normalize(item.href) === current ? " active" : "";
            return `<a href="${item.href}" class="sidebar-link${active}">${item.label}</a>`;
          })
          .join("")}
      </div>`
      )
      .join("");

    sidebar.innerHTML = `
      ${sectionsHTML}
      <div class="sidebar-theme-toggle">
        <div class="theme-toggle" role="group" aria-label="Theme mode">
          <button type="button" data-theme-btn="light" aria-pressed="false">Light</button>
          <button type="button" data-theme-btn="dark" aria-pressed="false">Dark</button>
          <button type="button" data-theme-btn="auto" aria-pressed="true">System</button>
        </div>
      </div>`;
  }

  /* ── Overlay ───────────────────────────────────────────── */
  let overlay = document.getElementById("sidebarOverlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "docs-sidebar-overlay";
    overlay.id = "sidebarOverlay";
    document.querySelector(".docs-layout").before(overlay);
  }

  /* ── Sidebar toggle ────────────────────────────────────── */
  function toggleSidebar() {
    sidebar.classList.toggle("open");
    overlay.classList.toggle("open");
  }

  overlay.addEventListener("click", toggleSidebar);

  const menuBtn = document.querySelector(".docs-menu-btn");
  if (menuBtn) menuBtn.addEventListener("click", toggleSidebar);

  document.querySelectorAll(".docs-sidebar a").forEach((a) =>
    a.addEventListener("click", () => {
      sidebar.classList.remove("open");
      overlay.classList.remove("open");
    })
  );

  /* ── Theme toggle ──────────────────────────────────────── */
  document.querySelectorAll("[data-theme-btn]").forEach((b) => {
    b.addEventListener("click", () => {
      localStorage.setItem(storageKey, b.dataset.themeBtn);
      applyTheme(b.dataset.themeBtn);
    });
  });

  systemTheme.addEventListener("change", () => {
    if ((localStorage.getItem(storageKey) || "auto") === "auto")
      applyTheme("auto");
  });

  applyTheme(localStorage.getItem(storageKey) || "dark");

  /* ── Copy buttons ──────────────────────────────────────── */
  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.copy;
      const el = document.getElementById(id);
      if (!el) return;
      try {
        await navigator.clipboard.writeText(el.textContent);
      } catch {
        return;
      }
      btn.classList.add("copied");
      setTimeout(() => btn.classList.remove("copied"), 1200);
    });
  });
})();
