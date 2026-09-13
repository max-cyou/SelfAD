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

  const serviceForm = document.querySelector("[data-service-form]");
  const serviceFormTitle = document.querySelector("[data-service-form-title]");
  const serviceSubmit = document.querySelector("[data-service-submit]");
  const serviceCancel = document.querySelector("[data-service-cancel]");

  document.querySelectorAll("[data-service-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!serviceForm) return;

      serviceForm.action = button.dataset.updateAction;
      serviceForm.elements.namedItem("name").value = button.dataset.name;
      serviceForm.elements.namedItem("slug").value = button.dataset.slug;
      serviceForm.elements.namedItem("gitlab_project_path").value =
        button.dataset.projectPath;
      serviceForm.elements.namedItem("default_branch").value =
        button.dataset.defaultBranch;
      serviceForm.elements.namedItem("status").value = button.dataset.status;
      serviceForm.elements.namedItem("description").value =
        button.dataset.description;
      serviceFormTitle.textContent = "Edit service";
      serviceSubmit.textContent = "Save changes";
      serviceCancel.hidden = false;
      serviceForm.elements.namedItem("name").focus();
    });
  });

  if (serviceCancel && serviceForm) {
    serviceCancel.addEventListener("click", () => {
      serviceForm.action = serviceForm.dataset.createAction;
      serviceForm.elements.namedItem("name").value = "";
      serviceForm.elements.namedItem("slug").value = "";
      serviceForm.elements.namedItem("gitlab_project_path").value = "";
      serviceForm.elements.namedItem("default_branch").value = "main";
      serviceForm.elements.namedItem("status").value = "draft";
      serviceForm.elements.namedItem("description").value = "";
      serviceFormTitle.textContent = "Add service";
      serviceSubmit.textContent = "Add service";
      serviceCancel.hidden = true;
    });
  }

  document.querySelectorAll("[data-delete-service]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.dataset.deleteService;
      if (!window.confirm(`Delete service "${name}"?`)) {
        event.preventDefault();
      }
    });
  });
})();
