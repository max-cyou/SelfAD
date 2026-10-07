import { cpp } from "@codemirror/lang-cpp";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { basicSetup, EditorView } from "codemirror";
import { diffLines } from "diff";

const selfadHighlight = HighlightStyle.define([
  { tag: tags.comment, color: "#6f875f", fontStyle: "italic" },
  { tag: [tags.keyword, tags.modifier, tags.controlKeyword], color: "#c586c0" },
  { tag: [tags.string, tags.special(tags.string)], color: "#ce9178" },
  { tag: [tags.number, tags.bool, tags.null], color: "#b5cea8" },
  { tag: [tags.typeName, tags.className, tags.namespace], color: "#4ec9b0" },
  { tag: [tags.function(tags.variableName), tags.labelName], color: "#dcdcaa" },
  { tag: [tags.propertyName, tags.attributeName], color: "#9cdcfe" },
  { tag: [tags.variableName, tags.name], color: "#c9cdd4" },
  { tag: [tags.operator, tags.punctuation], color: "#aeb3bb" },
  { tag: [tags.heading, tags.strong], color: "#d7dae0", fontWeight: "600" },
  { tag: tags.link, color: "#76a9d5", textDecoration: "underline" },
  { tag: tags.invalid, color: "#e06c75" },
]);

const editorTheme = EditorView.theme(
  {
    "&": {
      height: "100%",
      backgroundColor: "#0f1115",
      color: "#c9cdd4",
      fontSize: "0.75rem",
    },
    ".cm-scroller": {
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
      lineHeight: "1.6",
      overflow: "auto",
    },
    ".cm-content": {
      padding: "0.75rem 0",
      caretColor: "#d9dce1",
    },
    ".cm-line": {
      padding: "0 0.875rem",
    },
    ".cm-gutters": {
      backgroundColor: "#0f1115",
      color: "#555b65",
      border: "none",
      borderRight: "1px solid #202329",
    },
    ".cm-lineNumbers .cm-gutterElement": {
      minWidth: "2.75rem",
      padding: "0 0.625rem 0 0.5rem",
    },
    ".cm-activeLine": {
      backgroundColor: "#15181d",
    },
    ".cm-activeLineGutter": {
      backgroundColor: "#15181d",
      color: "#858b95",
    },
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
      backgroundColor: "#2d405d !important",
    },
    "&.cm-focused": {
      outline: "none",
    },
  },
  { dark: true },
);

function languageForPath(path) {
  const filename = path.toLowerCase();
  if (filename.endsWith(".py")) return python();
  if (/\.(c|cc|cpp|cxx|h|hh|hpp|hxx)$/.test(filename)) return cpp();
  if (filename.endsWith(".md")) return markdown();
  if (filename.endsWith(".json")) return json();
  if (filename.endsWith(".yaml") || filename.endsWith(".yml")) return yaml();
  return [];
}

export function countChangedLines(before, after) {
  return diffLines(before, after).reduce(
    (total, part) => total + (part.added || part.removed ? part.count ?? 0 : 0),
    0,
  );
}

export function mountEditor(parent, { content, editable, onChange, path }) {
  parent.replaceChildren();
  return new EditorView({
    doc: content,
    parent,
    extensions: [
      basicSetup,
      editorTheme,
      syntaxHighlighting(selfadHighlight),
      languageForPath(path),
      EditorView.editable.of(editable),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) onChange(update.state.doc.toString());
      }),
    ],
  });
}
