import { type KeyboardEvent, useMemo, useState } from "react";

import { CheckIcon, EditIcon } from "./Icons";
import { MarkdownText } from "./MarkdownText";

type MarkdownSection = {
  key: string;
  text: string;
  title: string;
  start: number;
  end: number;
};

type RichMarkdownEditorProps = {
  className?: string;
  onChange: (value: string) => void;
  /** Passed straight to `MarkdownText`; see its `onLinkTo`. */
  onLinkTo?: (href: string) => (() => void) | null;
  readOnly?: boolean;
  value: string;
};

export function RichMarkdownEditor({
  className,
  onChange,
  onLinkTo,
  readOnly = false,
  value,
}: RichMarkdownEditorProps) {
  const [editing, setEditing] = useState<{ key: string; draft: string } | null>(
    null,
  );
  const sections = useMemo(() => splitMarkdownSections(value), [value]);
  const activeKey =
    editing && sections.some((section) => section.key === editing.key)
      ? editing.key
      : null;

  function updateSection(section: MarkdownSection, nextText: string) {
    const suffixNeedsBreak =
      section.end < value.length && nextText.length > 0 && !nextText.endsWith("\n");
    const replacement = suffixNeedsBreak ? `${nextText}\n` : nextText;
    onChange(value.slice(0, section.start) + replacement + value.slice(section.end));
  }

  function beginEdit(section: MarkdownSection) {
    setEditing({ key: section.key, draft: section.text });
  }

  function cancelEdit() {
    setEditing(null);
  }

  function saveEdit(section: MarkdownSection) {
    if (!editing || editing.key !== section.key) return;
    updateSection(section, editing.draft);
    setEditing(null);
  }

  function handleEditKeyDown(
    event: KeyboardEvent<HTMLTextAreaElement>,
    section: MarkdownSection,
  ) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      saveEdit(section);
    }
  }

  return (
    <div className={["rich-md-editor", "doc", className].filter(Boolean).join(" ")}>
      <div className="rich-md-sections">
        {sections.map((section) => {
          const sectionEditing = activeKey === section.key;
          const previewText = sectionPreviewMarkdown(section.text);
          return (
            <section
              key={section.key}
              className={"rich-md-section" + (sectionEditing ? " editing" : "")}
            >
              <div className="rich-md-section-head">
                <span>{section.title}</span>
                {!readOnly && !sectionEditing && (
                  <button
                    type="button"
                    className="rich-md-edit-button"
                    onClick={() => beginEdit(section)}
                    title="Edit section"
                    aria-label="Edit section"
                  >
                    <EditIcon size={13} />
                  </button>
                )}
                {sectionEditing && (
                  <div className="rich-md-edit-actions">
                    <button type="button" className="btn ghost sm" onClick={cancelEdit}>
                      Cancel
                    </button>
                    <button type="button" className="btn primary sm" onClick={() => saveEdit(section)}>
                      <CheckIcon size={12} /> Save section
                    </button>
                  </div>
                )}
              </div>
              {sectionEditing ? (
                <textarea
                  className="rich-md-inline-source"
                  value={editing?.draft ?? section.text}
                  onChange={(event) =>
                    setEditing({ key: section.key, draft: event.target.value })
                  }
                  onKeyDown={(event) => handleEditKeyDown(event, section)}
                  spellCheck={false}
                  autoFocus
                />
              ) : (
                <div className="rich-md-preview">
                  {previewText.trim() ? (
                    <MarkdownText text={previewText} onLinkTo={onLinkTo} />
                  ) : (
                    <div className="rich-md-empty-body">No body content.</div>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function splitMarkdownSections(markdown: string): MarkdownSection[] {
  const bodyStart = frontMatterEnd(markdown);
  const starts: number[] = [];
  const headingPattern = /^#{1,6}\s+\S.*$/gm;
  headingPattern.lastIndex = bodyStart;
  let match: RegExpExecArray | null;
  while ((match = headingPattern.exec(markdown)) !== null) {
    if (match.index > bodyStart && starts.length === 0) starts.push(bodyStart);
    starts.push(match.index);
  }
  if (starts.length === 0) starts.push(bodyStart);

  const sections = starts.map((start, index) => {
    const end = starts[index + 1] ?? markdown.length;
    const text = markdown.slice(start, end);
    return {
      key: `section-${start}`,
      text,
      title: sectionTitle(text, index),
      start,
      end,
    };
  });

  const kept = sections.filter(
    (section) => section.text.length > 0 || markdown.length === bodyStart,
  );
  // A document that opens with a bare H1 and nothing under it is announcing
  // its own title -- which the view above already shows. Rendering it here
  // put the title on screen twice with "No body content." between.
  if (
    kept.length > 1
    && /^#\s+\S/.test(kept[0].text.trimStart())
    && !sectionPreviewMarkdown(kept[0].text).trim()
  ) {
    return kept.slice(1);
  }
  return kept;
}

function frontMatterEnd(markdown: string): number {
  return /^\uFEFF?---[ \t]*\r?\n[\s\S]*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n(?:[ \t]*\r?\n)*|$)/.exec(markdown)?.[0].length ?? 0;
}

function sectionTitle(text: string, index: number): string {
  const heading = /^#{1,6}\s+(.+?)\s*#*\s*$/m.exec(text)?.[1]?.trim();
  if (heading) return heading;
  return index === 0 ? "Opening" : `Section ${index + 1}`;
}

function sectionPreviewMarkdown(text: string): string {
  return text.replace(/^#{1,6}\s+\S.*(?:\r?\n)?/, "").replace(/^\s*\n/, "");
}
