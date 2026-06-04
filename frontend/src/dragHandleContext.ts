import { createContext, useContext } from "react";

import type { DraggableAttributes } from "@dnd-kit/core";
import type { SyntheticListenerMap } from "@dnd-kit/core/dist/hooks/utilities";

/**
 * Carries dnd-kit's drag activator props from a SortableCanvasCell
 * down to whichever element wants to be the drag handle — the
 * AgentTile or ChatTile header in our case.
 *
 * AgentTile gets mounted in two places:
 *   - WorkView's canvas, wrapped in SortableCanvasCell (provider sets
 *     attributes + listeners; tile header acts as the handle).
 *   - The standalone /agents/{slug} page (no provider; ``useDragHandle``
 *     returns ``null`` and the header stays a regular header).
 */
type DragHandleValue = {
  attributes: DraggableAttributes;
  listeners: SyntheticListenerMap | undefined;
};

export const DragHandleContext = createContext<DragHandleValue | null>(null);

export function useDragHandle(): DragHandleValue | null {
  return useContext(DragHandleContext);
}
