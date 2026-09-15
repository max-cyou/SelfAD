(() => {
  const startInput = document.querySelector("#contest-starts-at");
  const timezoneInput = document.querySelector("#browser-timezone");
  const timezoneLabel = document.querySelector("[data-browser-timezone]");

  if (startInput && timezoneInput && timezoneLabel) {
    let browserTimezone = "UTC";
    let timezoneDetected = false;
    try {
      const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (detected) {
        browserTimezone = detected;
        timezoneDetected = true;
      }
    } catch (_) {
      // The server deliberately falls back to UTC.
    }

    timezoneInput.value = browserTimezone;
    timezoneLabel.textContent = browserTimezone;

    const storedUtc = startInput.dataset.utcValue;
    if (storedUtc && !startInput.value) {
      const storedDate = new Date(storedUtc);
      if (!Number.isNaN(storedDate.getTime())) {
        const read = (localMethod, utcMethod) =>
          storedDate[timezoneDetected ? localMethod : utcMethod]();
        const pad = (value) => String(value).padStart(2, "0");
        startInput.value = [
          read("getFullYear", "getUTCFullYear"),
          pad(read("getMonth", "getUTCMonth") + 1),
          pad(read("getDate", "getUTCDate")),
        ].join("-") + `T${pad(read("getHours", "getUTCHours"))}:${pad(read("getMinutes", "getUTCMinutes"))}`;
      }
    }
  }

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

  const appearanceOpeners = Array.from(
    document.querySelectorAll("[data-appearance-open]"),
  );
  const appearanceDialogs = Array.from(
    document.querySelectorAll("[data-appearance-dialog]"),
  );
  const appearanceMode = document.querySelector("[data-appearance-mode]");

  const setDialogControlsDisabled = (dialog, disabled) => {
    dialog.querySelectorAll("input, textarea, select").forEach((control) => {
      control.disabled = disabled;
    });
  };

  const closeAppearanceDialog = (dialog) => {
    if (dialog.open) dialog.close();
    setDialogControlsDisabled(dialog, true);
    if (appearanceMode) appearanceMode.value = "identity";
  };

  const openAppearanceDialog = (mode) => {
    const dialog = appearanceDialogs.find(
      (candidate) => candidate.dataset.appearanceDialog === mode,
    );
    if (!dialog) return;

    appearanceDialogs.forEach((candidate) => {
      if (candidate !== dialog && candidate.open) {
        closeAppearanceDialog(candidate);
      }
    });
    setDialogControlsDisabled(dialog, false);
    if (appearanceMode) appearanceMode.value = mode;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };

  appearanceDialogs.forEach((dialog) => {
    setDialogControlsDisabled(dialog, true);
    dialog.querySelectorAll("[data-appearance-close]").forEach((button) => {
      button.addEventListener("click", () => closeAppearanceDialog(dialog));
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeAppearanceDialog(dialog);
    });
    dialog.addEventListener("close", () => {
      setDialogControlsDisabled(dialog, true);
      if (appearanceMode) appearanceMode.value = "identity";
    });
  });

  appearanceOpeners.forEach((button) => {
    button.addEventListener("click", () => {
      openAppearanceDialog(button.dataset.appearanceOpen);
    });
  });

  document.querySelectorAll("[data-appearance-save]").forEach((button) => {
    button.addEventListener("click", () => {
      if (appearanceMode) appearanceMode.value = button.dataset.appearanceSave;
    });
  });

  document.querySelectorAll("[data-color-value]").forEach((input) => {
    const picker = input.closest(".color-value")?.querySelector("[data-color-picker]");
    input.addEventListener("input", () => {
      if (picker && /^#[0-9a-f]{6}$/i.test(input.value)) {
        picker.value = input.value;
        document.body.style.setProperty(
          `--${input.dataset.cssVariable}`,
          input.value,
        );
      }
    });
    picker?.addEventListener("input", () => {
      input.value = picker.value.toUpperCase();
      document.body.style.setProperty(
        `--${input.dataset.cssVariable}`,
        input.value,
      );
    });
  });

  if (appearanceMode && ["palette", "templates"].includes(appearanceMode.value)) {
    openAppearanceDialog(appearanceMode.value);
  }

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

  const userForm = document.querySelector("[data-user-form]");
  const userFormTitle = document.querySelector("[data-user-form-title]");
  const userFormDescription = document.querySelector(
    "[data-user-form-description]",
  );
  const userSubmit = document.querySelector("[data-user-submit]");
  const userCancel = document.querySelector("[data-user-cancel]");
  const userEditor = document.querySelector(".participant-section");

  const resetUserForm = () => {
    if (!userForm) return;

    userForm.action = userForm.dataset.createAction;
    userForm.elements.namedItem("username").value = "";
    userForm.elements.namedItem("username").readOnly = false;
    userForm.elements.namedItem("email").value = "";
    userForm.elements.namedItem("role").value = "user";
    userForm.elements.namedItem("password").value = "";
    userForm.elements.namedItem("password").required = true;
    userForm.elements.namedItem("ssh_public_key").value = "";
    userForm.elements.namedItem("ssh_public_key").required = true;
    userFormTitle.textContent = "Create user";
    userFormDescription.textContent =
      "Creates participant access. Admin is only an additional interface role.";
    userSubmit.textContent = "Create user";
    userCancel.hidden = true;
  };

  document.querySelectorAll("[data-user-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!userForm) return;

      userForm.action = button.dataset.updateAction;
      userForm.elements.namedItem("username").value = button.dataset.username;
      userForm.elements.namedItem("username").readOnly = true;
      userForm.elements.namedItem("email").value = button.dataset.email;
      userForm.elements.namedItem("role").value = button.dataset.role;
      userForm.elements.namedItem("password").value = "";
      userForm.elements.namedItem("password").required = false;
      userForm.elements.namedItem("ssh_public_key").value = "";
      userForm.elements.namedItem("ssh_public_key").required = false;
      userFormTitle.textContent = "Edit user";
      userFormDescription.textContent = button.dataset.hasSshKey === "true"
        ? "Update account access and credentials. An SSH key is already added."
        : "Update account access and optionally add an SSH key.";
      userSubmit.textContent = "Save changes";
      userCancel.hidden = false;
      userEditor.scrollIntoView({ block: "start" });
      userForm.elements.namedItem("email").focus();
    });
  });

  if (userCancel && userForm) {
    userCancel.addEventListener("click", () => {
      resetUserForm();
      userForm.elements.namedItem("username").focus();
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

  document.querySelectorAll("[data-delete-user]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.dataset.deleteUser;
      if (!window.confirm(`Delete user "${name}" and their repositories?`)) {
        event.preventDefault();
      }
    });
  });
})();
