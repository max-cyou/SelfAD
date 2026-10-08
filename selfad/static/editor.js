const workspace = document.querySelector(".editor-workspace");
const treeElement = document.querySelector("#file-tree");
const editorPane = document.querySelector(".editor-pane");
const submitButton = document.querySelector("#submit-button");
const changeSummary = document.querySelector("#change-summary");
const sshKeyForm = document.querySelector("#ssh-key-form");
const sshKeyFeedback = document.querySelector("#ssh-key-feedback");
const removeSshKey = document.querySelector("#remove-ssh-key");
const copyCloneCommand = document.querySelector("#copy-clone-command");
const cloneCommand = document.querySelector("#clone-command");
const cloneFeedback = document.querySelector("#clone-feedback");

const DRAFT_DATABASE = "selfad-code-editor";
const DRAFT_STORE = "drafts";
const openFiles = new Map();
const fileRows = new Map();
const savedDrafts = new Map();

let databasePromise = null;
let editorView = null;
let currentFile = null;
let isSubmitting = false;
let repositoryHead = "";
let openRequest = 0;
let summaryTimer = null;

function draftScope() {
  const { userId, assignmentId, editorKind } = workspace.dataset;
  return `${userId}:${assignmentId}:${editorKind}`;
}

function draftKey(path) {
  return `${draftScope()}:${path}`;
}

function openDraftDatabase() {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DRAFT_DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(DRAFT_STORE)) {
        request.result.createObjectStore(DRAFT_STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return databasePromise;
}

async function readAllDrafts() {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(DRAFT_STORE, "readonly");
    const request = transaction.objectStore(DRAFT_STORE).getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function writeDraft(draft) {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(DRAFT_STORE, "readwrite");
    transaction.objectStore(DRAFT_STORE).put(draft);
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
}

async function deleteDraft(path) {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(DRAFT_STORE, "readwrite");
    transaction.objectStore(DRAFT_STORE).delete(draftKey(path));
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
}

function updateDirtyIndicator(file) {
  fileRows.get(file.path)?.classList.toggle("is-dirty", file.content !== file.savedContent);
}

function pendingChanges() {
  const changes = new Map(savedDrafts);
  for (const file of openFiles.values()) {
    if (file.content === file.originalContent) {
      changes.delete(file.path);
    } else {
      changes.set(file.path, {
        content: file.content,
        originalContent: file.originalContent,
        path: file.path,
        sha: file.sha,
      });
    }
  }
  return [...changes.values()];
}

function refreshChangeSummary() {
  const changes = pendingChanges();
  const lineCount = changes.reduce(
    (total, file) =>
      total + window.SelfADCodeMirror.countChangedLines(file.originalContent, file.content),
    0,
  );
  changeSummary.textContent = `${lineCount} ${lineCount === 1 ? "line" : "lines"} changed`;
  submitButton.disabled = isSubmitting || changes.length === 0;
}

function scheduleSummaryRefresh() {
  window.clearTimeout(summaryTimer);
  summaryTimer = window.setTimeout(refreshChangeSummary, 80);
}

async function saveFile(file) {
  if (!file || !file.editable || file.content === file.savedContent) return;

  if (file.content === file.originalContent) {
    await deleteDraft(file.path);
    savedDrafts.delete(file.path);
  } else {
    const draft = {
      key: draftKey(file.path),
      scope: draftScope(),
      baseCommit: repositoryHead,
      content: file.content,
      originalContent: file.originalContent,
      path: file.path,
      sha: file.sha,
      updatedAt: new Date().toISOString(),
    };
    await writeDraft(draft);
    savedDrafts.set(file.path, draft);
  }

  file.savedContent = file.content;
  updateDirtyIndicator(file);
  refreshChangeSummary();
}

async function saveCurrentFile() {
  try {
    await saveFile(currentFile);
  } catch (_error) {
    changeSummary.textContent = "Draft could not be saved";
  }
}

async function saveAllFiles() {
  for (const file of openFiles.values()) await saveFile(file);
}

async function selectFile(file, fileRow) {
  document.querySelector(".file-tree-row.is-selected")?.classList.remove("is-selected");
  fileRow.classList.add("is-selected");
  await openFile(file);
}

function buildTree(files) {
  const root = { directories: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    const filename = parts.pop();
    let directory = root;
    for (const part of parts) {
      if (!directory.directories.has(part)) {
        directory.directories.set(part, { directories: new Map(), files: [] });
      }
      directory = directory.directories.get(part);
    }
    if (filename) directory.files.push({ ...file, name: filename });
  }
  return root;
}

function row(label, depth, isFolder) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "file-tree-row";
  element.style.paddingLeft = `${0.4375 + depth * 0.875}rem`;

  const chevron = document.createElement("span");
  chevron.className = "file-tree-chevron";
  chevron.textContent = "›";
  element.append(chevron);

  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("class", "file-tree-icon");
  icon.setAttribute("viewBox", "0 0 16 16");
  icon.setAttribute("fill", "none");
  icon.setAttribute("stroke", "currentColor");
  icon.setAttribute("stroke-width", "1.25");
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = isFolder
    ? '<path d="M1.75 4.25h4l1.2 1.5h7.3v6.5a1 1 0 0 1-1 1H2.75a1 1 0 0 1-1-1v-8Z"/><path d="M1.75 5.75v-2a1 1 0 0 1 1-1h2.6l1.2 1.5h6.7a1 1 0 0 1 1 1v.5"/>'
    : '<path d="M3.25 1.75h6l3.5 3.5v9H3.25v-12.5Z"/><path d="M9.25 1.75v3.5h3.5"/>';
  element.append(icon);

  const name = document.createElement("span");
  name.className = "file-tree-name";
  name.textContent = label;
  element.append(name);

  if (!isFolder) {
    const dirty = document.createElement("span");
    dirty.className = "file-tree-dirty";
    dirty.innerHTML =
      '<span class="file-tree-shortcut">Ctrl+S</span><span class="file-tree-dirty-dot"></span>';
    element.append(dirty);
    element.tabIndex = -1;
  }
  return element;
}

function renderDirectory(directory, depth = 0) {
  const list = document.createElement("ul");
  const directories = [...directory.directories.entries()].sort(([left], [right]) =>
    left.localeCompare(right),
  );
  for (const [name, child] of directories) {
    const item = document.createElement("li");
    item.className = "file-tree-folder is-open";
    const folderRow = row(name, depth, true);
    folderRow.addEventListener("click", () => item.classList.toggle("is-open"));
    item.append(folderRow, renderDirectory(child, depth + 1));
    list.append(item);
  }

  for (const file of directory.files.sort((left, right) => left.name.localeCompare(right.name))) {
    const item = document.createElement("li");
    item.className = "file-tree-file";
    const fileRow = row(file.name, depth, false);
    fileRow.dataset.path = file.path;
    fileRow.addEventListener("click", () => selectFile(file, fileRow));
    fileRows.set(file.path, fileRow);
    item.append(fileRow);
    list.append(item);
  }
  return list;
}

async function openFile(fileMetadata) {
  if (!workspace || !editorPane) return;
  const requestNumber = ++openRequest;
  let file = openFiles.get(fileMetadata.path);

  if (!file) {
    const { assignmentId, editorKind } = workspace.dataset;
    const query = new URLSearchParams({ path: fileMetadata.path });
    const response = await fetch(`/api/editor/${assignmentId}/${editorKind}/file?${query}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok || requestNumber !== openRequest) return;

    const payload = await response.json();
    const draft = savedDrafts.get(payload.path);
    const draftIsCurrent = draft && draft.baseCommit === payload.head && draft.sha === payload.sha;
    const content = draftIsCurrent ? draft.content : payload.content;
    file = {
      content,
      editable: payload.editable,
      originalContent: payload.content,
      path: payload.path,
      savedContent: content,
      sha: payload.sha,
    };
    openFiles.set(file.path, file);
  }

  if (requestNumber !== openRequest) return;
  currentFile = file;
  editorView?.destroy();
  editorView = window.SelfADCodeMirror.mountEditor(editorPane, {
    content: file.content,
    editable: file.editable,
    path: file.path,
    onChange(content) {
      file.content = content;
      updateDirtyIndicator(file);
      scheduleSummaryRefresh();
    },
  });
  updateDirtyIndicator(file);
  refreshChangeSummary();
}

async function submitChanges() {
  const originalLabel = submitButton.textContent;
  isSubmitting = true;
  submitButton.disabled = true;
  submitButton.textContent = "Submitting…";
  try {
    await saveAllFiles();
    const changes = pendingChanges();
    if (changes.length === 0) {
      isSubmitting = false;
      submitButton.textContent = originalLabel;
      refreshChangeSummary();
      return;
    }

    const { assignmentId, csrfToken, editorKind } = workspace.dataset;
    const response = await fetch(`/api/editor/${assignmentId}/${editorKind}/submit`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        base_commit: repositoryHead,
        changes: changes.map((file) => ({
          operation: "update",
          path: file.path,
          content: file.content,
          sha: file.sha,
        })),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Submission failed");

    for (const file of changes) await deleteDraft(file.path);
    savedDrafts.clear();
    submitButton.textContent = "Submitted";
    changeSummary.textContent = "Changes submitted";
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    isSubmitting = false;
    submitButton.textContent = originalLabel;
    submitButton.disabled = false;
    changeSummary.textContent = error instanceof Error ? error.message : "Submission failed";
  }
}

async function loadFiles() {
  if (!workspace || !treeElement) return;
  const { assignmentId, editorKind } = workspace.dataset;
  try {
    const storedDrafts = await readAllDrafts();
    for (const draft of storedDrafts) {
      if (draft.scope === draftScope()) savedDrafts.set(draft.path, draft);
    }

    const response = await fetch(`/api/editor/${assignmentId}/${editorKind}/tree`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Unable to load repository files.");

    const payload = await response.json();
    repositoryHead = payload.head;
    for (const [path, draft] of savedDrafts) {
      if (draft.baseCommit !== repositoryHead) {
        savedDrafts.delete(path);
        await deleteDraft(path);
      }
    }

    if (!Array.isArray(payload.files) || payload.files.length === 0) {
      treeElement.innerHTML = '<span class="file-tree-message">No files</span>';
      return;
    }

    const list = renderDirectory(buildTree(payload.files));
    list.classList.add("file-tree-list");
    treeElement.replaceChildren(list);
    refreshChangeSummary();

    const preferredFile =
      payload.files.find((file) => file.path === "exploit.py") ??
      payload.files.find(
        (file) =>
          file.editable && !["Dockerfile", "README.md", "selfad.yml"].includes(file.path),
      ) ??
      payload.files.find((file) => file.editable) ??
      payload.files[0];
    const preferredRow = fileRows.get(preferredFile.path);
    if (preferredRow) await selectFile(preferredFile, preferredRow);
  } catch (_error) {
    treeElement.innerHTML = '<span class="file-tree-message">Failed to load files</span>';
  }
}

window.addEventListener(
  "keydown",
  (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      event.stopPropagation();
      saveCurrentFile();
    }
  },
  { capture: true },
);

window.addEventListener("beforeunload", (event) => {
  if ([...openFiles.values()].some((file) => file.content !== file.savedContent)) {
    event.preventDefault();
  }
});

submitButton?.addEventListener("click", submitChanges);

copyCloneCommand?.addEventListener("click", async () => {
  const command = cloneCommand?.textContent?.trim();
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    copyCloneCommand.textContent = "Copied";
    cloneFeedback.textContent = "Clone command copied.";
  } catch (_error) {
    copyCloneCommand.textContent = "Copy failed";
    cloneFeedback.textContent = "Select and copy the command manually.";
  }
  window.setTimeout(() => {
    copyCloneCommand.textContent = "Copy";
  }, 1600);
});

sshKeyForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = sshKeyForm.querySelector("button[type='submit']");
  const publicKey = new FormData(sshKeyForm).get("public_key");
  if (typeof publicKey !== "string") return;

  submit.disabled = true;
  sshKeyFeedback.textContent = "Adding SSH key…";
  try {
    const response = await fetch("/api/editor/ssh-key", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": workspace.dataset.csrfToken,
      },
      body: JSON.stringify({ public_key: publicKey }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not add SSH key.");
    sshKeyFeedback.textContent = payload.message;
    window.setTimeout(() => window.location.reload(), 350);
  } catch (error) {
    sshKeyFeedback.textContent = error instanceof Error ? error.message : "Could not add SSH key.";
    submit.disabled = false;
  }
});

removeSshKey?.addEventListener("click", async () => {
  if (!window.confirm("Remove this SSH key from your Git account?")) return;
  removeSshKey.disabled = true;
  sshKeyFeedback.textContent = "Removing SSH key…";
  try {
    const response = await fetch("/api/editor/ssh-key", {
      method: "DELETE",
      headers: {
        Accept: "application/json",
        "X-CSRF-Token": workspace.dataset.csrfToken,
      },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not remove SSH key.");
    sshKeyFeedback.textContent = payload.message;
    window.setTimeout(() => window.location.reload(), 350);
  } catch (error) {
    sshKeyFeedback.textContent = error instanceof Error ? error.message : "Could not remove SSH key.";
    removeSshKey.disabled = false;
  }
});

loadFiles();
