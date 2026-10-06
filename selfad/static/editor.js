const workspace = document.querySelector(".editor-workspace");
const treeElement = document.querySelector("#file-tree");

function buildTree(paths) {
  const root = { directories: new Map(), files: [] };

  for (const path of paths) {
    const parts = path.split("/").filter(Boolean);
    const filename = parts.pop();
    let directory = root;

    for (const part of parts) {
      if (!directory.directories.has(part)) {
        directory.directories.set(part, { directories: new Map(), files: [] });
      }
      directory = directory.directories.get(part);
    }

    if (filename) {
      directory.files.push({ name: filename, path });
    }
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
    fileRow.addEventListener("click", () => {
      document.querySelector(".file-tree-row.is-selected")?.classList.remove("is-selected");
      fileRow.classList.add("is-selected");
    });
    item.append(fileRow);
    list.append(item);
  }

  return list;
}

async function loadFiles() {
  if (!workspace || !treeElement) return;

  const { assignmentId, editorKind } = workspace.dataset;
  try {
    const response = await fetch(`/api/editor/${assignmentId}/${editorKind}/tree`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Unable to load repository files.");

    const payload = await response.json();
    if (!Array.isArray(payload.files) || payload.files.length === 0) {
      treeElement.innerHTML = '<span class="file-tree-message">No files</span>';
      return;
    }

    const paths = payload.files.map((file) => file.path);
    const list = renderDirectory(buildTree(paths));
    list.classList.add("file-tree-list");
    treeElement.replaceChildren(list);
  } catch (_error) {
    treeElement.innerHTML = '<span class="file-tree-message">Failed to load files</span>';
  }
}

loadFiles();
