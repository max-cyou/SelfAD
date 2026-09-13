(() => {
  const tabs = Array.from(document.querySelectorAll("[data-admin-tab]"));
  const panels = Array.from(document.querySelectorAll("[data-admin-panel]"));
  const sections = new Set(tabs.map((tab) => tab.dataset.adminTab));

  if (!tabs.length || !panels.length) {
    return;
  }

  const activate = (section, updateHash = true) => {
    if (!sections.has(section)) {
      return;
    }

    tabs.forEach((tab) => {
      const isActive = tab.dataset.adminTab === section;
      tab.classList.toggle("active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
      tab.tabIndex = isActive ? 0 : -1;
    });

    panels.forEach((panel) => {
      panel.hidden = panel.dataset.adminPanel !== section;
    });

    if (updateHash) {
      history.replaceState(null, "", `#${section}`);
    }
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab.dataset.adminTab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
        return;
      }

      event.preventDefault();
      let nextIndex = index;
      if (event.key === "ArrowUp") nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "ArrowDown") nextIndex = (index + 1) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      tabs[nextIndex].focus();
      activate(tabs[nextIndex].dataset.adminTab);
    });
  });

  window.addEventListener("hashchange", () => {
    activate(location.hash.slice(1), false);
  });

  const requestedSection = location.hash.slice(1);
  const initialSection = sections.has(requestedSection)
    ? requestedSection
    : document.body.dataset.adminSection;
  activate(initialSection, false);
})();
