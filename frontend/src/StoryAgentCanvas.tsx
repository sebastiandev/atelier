import type { ReactNode } from "react";
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, rectSortingStrategy } from "@dnd-kit/sortable";

import type { AgentSummary } from "./api";
import { AgentIcon } from "./Icons";
import { SortableCanvasCell } from "./SortableCanvasCell";
import { applyAgentOrder, useAgentOrderStore } from "./state/agentOrder";

/**
 * The agent tiles of one planning story, on the dot field next to the
 * story doc. Same tile grid as the work canvas (drag to reorder, auto
 * tiling), scoped to the story: the order key is ``<work>:<story>`` so a
 * story's arrangement never collides with the work canvas or another story.
 *
 * The tile itself is rendered by the parent (WorkView owns every agent
 * callback) so this component stays a layout: header row, grid, empty cell.
 */
export function StoryAgentCanvas({
  workSlug,
  storyId,
  agents,
  focusedSlug,
  readOnly,
  onFocus,
  onNewAgent,
  registerRef,
  renderTile,
  done,
  doneHint,
  onMarkDone,
}: {
  /** The story is already accepted. */
  done: boolean;
  /** "all 2 PRs merged" — shown next to Mark done when every PR landed. */
  doneHint: string | null;
  onMarkDone: () => void;
  workSlug: string;
  storyId: string;
  /** Open (not closed-to-rail) agents of this story, creation order. */
  agents: AgentSummary[];
  focusedSlug: string | null;
  readOnly: boolean;
  onFocus: (slug: string | null) => void;
  onNewAgent: () => void;
  registerRef: (slug: string, el: HTMLDivElement | null) => void;
  renderTile: (agent: AgentSummary) => ReactNode;
}) {
  const orderKey = `${workSlug}:${storyId}`;
  const override = useAgentOrderStore((s) => s.byWork[orderKey]);
  const setOrder = useAgentOrderStore((s) => s.setOrder);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );
  const bySlug = new Map(agents.map((a) => [a.slug, a]));
  const ordered = applyAgentOrder(
    override,
    agents.map((a) => a.slug),
  ).filter((slug) => bySlug.has(slug));
  const cols = ordered.length <= 1 ? 1 : ordered.length <= 4 ? 2 : 3;

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = ordered.indexOf(active.id as string);
    const to = ordered.indexOf(over.id as string);
    if (from === -1 || to === -1) return;
    const next = [...ordered];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setOrder(orderKey, next);
  }

  return (
    <section className="story-agent-canvas">
      <div className="story-agent-canvas-hd">
        <span className="mono">
          {ordered.length === 0
            ? `no agents on ${storyId}`
            : `${ordered.length} agent${ordered.length === 1 ? "" : "s"} on ${storyId}` +
              (ordered.length > 1 ? " · drag to reorder" : "")}
        </span>
        <span className="story-agent-canvas-actions">
          {done ? (
            <em className="tag good">done</em>
          ) : (
            <>
              {doneHint && <span className="mono accent">{doneHint} · mark done?</span>}
              <button className="btn sm" type="button" disabled={readOnly} onClick={onMarkDone}>
                ✓ Mark done
              </button>
            </>
          )}
          <button className="btn sm primary" type="button" disabled={readOnly} onClick={onNewAgent}>
            + New agent
          </button>
        </span>
      </div>
      <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
        <div className="work-right-canvas tiles story-tiles" data-cols={cols}>
          <SortableContext items={ordered} strategy={rectSortingStrategy}>
            {ordered.map((slug) => {
              const agent = bySlug.get(slug)!;
              return (
                <SortableCanvasCell
                  key={slug}
                  itemId={slug}
                  persona={agent.persona}
                  focused={focusedSlug === slug}
                  onFocus={() => onFocus(slug)}
                  registerRef={(el) => registerRef(slug, el)}
                >
                  {renderTile(agent)}
                </SortableCanvasCell>
              );
            })}
          </SortableContext>
          {ordered.length === 0 && (
            <button
              type="button"
              className="story-agent-empty"
              disabled={readOnly}
              onClick={onNewAgent}
            >
              <AgentIcon size={16} />
              <strong>No agents on this story yet</strong>
              <span>Launch one — it starts with the story and the plan sources attached.</span>
            </button>
          )}
        </div>
      </DndContext>
    </section>
  );
}
