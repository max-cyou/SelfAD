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
  const serviceFormDescription = document.querySelector(
    "[data-service-form-description]",
  );
  const serviceSubmit = document.querySelector("[data-service-submit]");
  const serviceCancel = document.querySelector("[data-service-cancel]");
  const serviceEditor = document.querySelector("#service-editor");

  const resetServiceForm = () => {
    if (!serviceForm) return;

    serviceForm.action = serviceForm.dataset.createAction;
    serviceForm.elements.namedItem("name").value = "";
    serviceForm.elements.namedItem("slug").value = "";
    serviceForm.elements.namedItem("slug").readOnly = false;
    serviceForm.elements.namedItem("default_branch").value = "main";
    serviceForm.elements.namedItem("default_branch").readOnly = false;
    serviceForm.elements.namedItem("status").value = "draft";
    serviceForm.querySelector("[data-active-status]").disabled = true;
    serviceForm.elements.namedItem("description").value = "";
    serviceFormTitle.textContent = "Create service";
    serviceFormDescription.textContent =
      "Creates separate service and jury repositories in Gitea.";
    serviceSubmit.textContent = "Create repositories";
    serviceCancel.hidden = true;
  };

  document.querySelectorAll("[data-service-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!serviceForm) return;

      serviceForm.action = button.dataset.updateAction;
      serviceForm.elements.namedItem("name").value = button.dataset.name;
      serviceForm.elements.namedItem("slug").value = button.dataset.slug;
      serviceForm.elements.namedItem("slug").readOnly = true;
      serviceForm.elements.namedItem("default_branch").value =
        button.dataset.defaultBranch;
      serviceForm.elements.namedItem("default_branch").readOnly = true;
      serviceForm.elements.namedItem("status").value = button.dataset.status;
      serviceForm.querySelector("[data-active-status]").disabled = false;
      serviceForm.elements.namedItem("description").value =
        button.dataset.description;
      serviceFormTitle.textContent = "Edit service";
      serviceFormDescription.textContent =
        "Update service metadata and availability.";
      serviceSubmit.textContent = "Save changes";
      serviceCancel.hidden = false;
      serviceEditor.scrollIntoView({ block: "start" });
      serviceForm.elements.namedItem("name").focus();
    });
  });

  if (serviceCancel && serviceForm) {
    serviceCancel.addEventListener("click", () => {
      resetServiceForm();
      serviceForm.elements.namedItem("name").focus();
    });
  }

  document.querySelectorAll("[data-new-service]").forEach((link) => {
    link.addEventListener("click", () => {
      resetServiceForm();
      window.setTimeout(() => {
        serviceForm?.elements.namedItem("name").focus();
      }, 0);
    });
  });

  const passwordOutput = document.querySelector("[data-gitea-password]");
  const passwordReveal = document.querySelector(
    "[data-gitea-password-reveal]",
  );
  const passwordCopy = document.querySelector("[data-gitea-password-copy]");
  const credentialStatus = document.querySelector(
    "[data-gitea-credential-status]",
  );
  let giteaPassword = null;

  if (passwordReveal && passwordOutput) {
    passwordReveal.addEventListener("click", async () => {
      if (passwordReveal.dataset.visible === "true") {
        passwordOutput.textContent = "••••••••••••••••";
        passwordReveal.textContent = "Show";
        passwordReveal.dataset.visible = "false";
        credentialStatus.textContent = "";
        return;
      }

      passwordReveal.disabled = true;
      credentialStatus.classList.remove("credential-status--error");
      credentialStatus.textContent = "Loading…";

      try {
        if (!giteaPassword) {
          const csrfToken = document.querySelector(
            'input[name="csrf_token"]',
          )?.value;
          const body = new URLSearchParams({ csrf_token: csrfToken || "" });
          const response = await fetch(passwordReveal.dataset.credentialsUrl, {
            method: "POST",
            body,
            credentials: "same-origin",
          });
          if (!response.ok) {
            throw new Error("Password is unavailable.");
          }
          const credentials = await response.json();
          if (typeof credentials.password !== "string") {
            throw new Error("Password is unavailable.");
          }
          giteaPassword = credentials.password;
        }

        passwordOutput.textContent = giteaPassword;
        passwordReveal.textContent = "Hide";
        passwordReveal.dataset.visible = "true";
        passwordCopy.hidden = false;
        credentialStatus.textContent = "";
      } catch (error) {
        credentialStatus.classList.add("credential-status--error");
        credentialStatus.textContent = error.message;
      } finally {
        passwordReveal.disabled = false;
      }
    });
  }

  if (passwordCopy) {
    passwordCopy.addEventListener("click", async () => {
      if (!giteaPassword) return;

      try {
        await navigator.clipboard.writeText(giteaPassword);
        credentialStatus.classList.remove("credential-status--error");
        credentialStatus.textContent = "Copied.";
      } catch {
        credentialStatus.classList.add("credential-status--error");
        credentialStatus.textContent = "Could not copy the password.";
      }
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
