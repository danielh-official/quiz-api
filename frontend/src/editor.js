// SQL editor for /app/sql problem pages: CodeMirror over a plain <textarea data-sql-editor>.
// The textarea stays the form field (kept in sync), so the page still works if this script doesn't load.
import { basicSetup } from "codemirror";
import { EditorView, keymap } from "@codemirror/view";
import { Compartment, Prec } from "@codemirror/state";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { MariaSQL, MSSQL, MySQL, PostgreSQL, SQLite, sql } from "@codemirror/lang-sql";

const DIALECTS = { sqlite: SQLite, mysql: MySQL, mariadb: MariaSQL, tsql: MSSQL, postgres: PostgreSQL };

// Colors come from the Tailwind theme variables, so light and dark mode follow the rest of /app.
const theme = EditorView.theme({
  "&": {
    backgroundColor: "var(--color-surface)",
    color: "var(--color-ink)",
    border: "1px solid var(--color-line)",
    borderRadius: "0.5rem",
    fontSize: "14px",
  },
  "&.cm-focused": { outline: "2px solid var(--color-accent)", outlineOffset: "1px" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6", minHeight: "10rem" },
  ".cm-content": { caretColor: "var(--color-ink)", padding: "0.5rem 0" },
  ".cm-gutters": {
    backgroundColor: "var(--color-surface)",
    color: "var(--color-mute)",
    border: "none",
    borderRight: "1px solid var(--color-line)",
    borderRadius: "0.5rem 0 0 0.5rem",
  },
  ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--color-accent) 7%, transparent)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in srgb, var(--color-accent) 25%, transparent)",
  },
  ".cm-tooltip": { backgroundColor: "var(--color-surface)", border: "1px solid var(--color-line)" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--color-accent)", color: "var(--color-accent-fg)" },
});

const highlight = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--color-accent)", fontWeight: "600" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--color-ok)" },
  { tag: [tags.number, tags.bool, tags.null], color: "var(--color-due)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: "var(--color-mute)", fontStyle: "italic" },
  { tag: [tags.typeName, tags.standard(tags.name)], color: "var(--color-bad)" },
]);

for (const textarea of document.querySelectorAll("textarea[data-sql-editor]")) {
  const form = textarea.form;
  const select = form.querySelector("select[name=dialect]");
  const schema = JSON.parse(textarea.dataset.schema || "{}");
  const language = new Compartment();
  const languageFor = (dialect) => sql({ dialect: DIALECTS[dialect] || SQLite, schema, upperCaseKeywords: true });
  const run = () => {
    form.querySelector("[data-run]")?.click();
    return true;
  };

  const view = new EditorView({
    doc: textarea.value,
    extensions: [
      Prec.highest(keymap.of([{ key: "Mod-Enter", run }])),
      basicSetup,
      language.of(languageFor(select.value)),
      syntaxHighlighting(highlight),
      theme,
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({ "aria-label": textarea.getAttribute("aria-label") || "SQL query" }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) textarea.value = update.state.doc.toString();
      }),
    ],
  });
  textarea.hidden = true;
  textarea.after(view.dom);
  select.addEventListener("change", () => view.dispatch({ effects: language.reconfigure(languageFor(select.value)) }));
}
