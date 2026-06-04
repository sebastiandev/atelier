import type { ReactNode } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { DragHandleContext } from "./dragHandleContext";

/**
 * Canvas cell wrapped with @dnd-kit/sortable. The cell itself has no
 * built-in drag handle — instead it provides drag attributes + listeners
 * via context so AgentTile's <header> becomes the drag activator. The
 * 6px PointerSensor activation distance (set in WorkView) keeps clicks
 * on header buttons from triggering a drag.
 *
 * Position transitions are driven by the CSS transform from useSortable;
 * the tile content (children) is unaware that any reordering is going on.
 */
export function SortableCanvasCell({
  itemId,
  agentSlug,
  persona,
  focused,
  onFocus,
  registerRef,
  children,
}: {
  itemId?: string;
  agentSlug?: string;
  persona?: string;
  focused: boolean;
  onFocus: () => void;
  /** WorkView keeps a slug→element map for scroll-into-view after creating
   *  a new agent; the wrapper hands its DOM node back via this hook. */
  registerRef: (el: HTMLDivElement | null) => void;
  children: ReactNode;
}) {
  const id = itemId ?? agentSlug;
  if (!id) {
    throw new Error("SortableCanvasCell requires itemId");
  }
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

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
      <DragHandleContext.Provider value={{ attributes, listeners }}>
        {children}
      </DragHandleContext.Provider>
    </div>
  );
}
