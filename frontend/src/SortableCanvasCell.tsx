import type { ReactNode } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

/**
 * Canvas cell wrapped with @dnd-kit/sortable so the whole tile can be
 * dragged to a new position. The drag handle lives in the top-left
 * corner — only listening on the grip lets the user click into the
 * composer and transcript without triggering a drag.
 *
 * Position transitions are driven by the CSS transform from useSortable;
 * the tile content (children) is unaware that any reordering is going on.
 */
export function SortableCanvasCell({
  agentSlug,
  persona,
  focused,
  onFocus,
  registerRef,
  children,
}: {
  agentSlug: string;
  persona: string;
  focused: boolean;
  onFocus: () => void;
  /** WorkView keeps a slug→element map for scroll-into-view after creating
   *  a new agent; the wrapper hands its DOM node back via this hook. */
  registerRef: (el: HTMLDivElement | null) => void;
  children: ReactNode;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: agentSlug });

  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : undefined,
    // Lift the dragged cell above its peers so the transformed copy
    // doesn't render under the static ones during the animation.
    zIndex: isDragging ? 10 : undefined,
  };

  return (
    <div
      ref={(el) => {
        setNodeRef(el);
        registerRef(el);
      }}
      className={"canvas-cell" + (focused ? " focused" : "")}
      data-persona={persona}
      onMouseDown={onFocus}
      style={style}
    >
      <button
        type="button"
        className="canvas-cell-grip"
        aria-label={`Reorder ${agentSlug}`}
        title="Drag to reorder"
        {...attributes}
        {...listeners}
      >
        <GripIcon />
      </button>
      {children}
    </div>
  );
}

function GripIcon() {
  // 12-viewBox SVG — same convention as the rest of the app's icons.
  return (
    <svg
      viewBox="0 0 12 12"
      width="12"
      height="12"
      fill="currentColor"
      aria-hidden="true"
    >
      <circle cx="4" cy="3" r="1" />
      <circle cx="4" cy="6" r="1" />
      <circle cx="4" cy="9" r="1" />
      <circle cx="8" cy="3" r="1" />
      <circle cx="8" cy="6" r="1" />
      <circle cx="8" cy="9" r="1" />
    </svg>
  );
}
