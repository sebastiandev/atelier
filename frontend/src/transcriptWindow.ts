type TranscriptEvent = {
  seq: number;
  type: string;
};

export type TranscriptWindow<T extends TranscriptEvent> = {
  events: T[];
  hiddenCount: number;
};

export function adaptiveTranscriptLimit(latestSeq: number): number {
  // ponytail: seq is a cheap size proxy; use server event-count metadata if
  // transcript sequence numbers ever stop tracking session growth.
  if (latestSeq > 10_000) return 50;
  if (latestSeq > 5_000) return 100;
  if (latestSeq > 1_000) return 250;
  return 500;
}

export function transcriptWindowFor<T extends TranscriptEvent>(
  events: readonly T[],
  visibleCount: number,
): TranscriptWindow<T> {
  if (visibleCount <= 0) {
    return { events: [], hiddenCount: countRenderableTranscriptEvents(events) };
  }

  let rendered = 0;
  let start = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    if (isRenderableTranscriptEvent(events[i])) rendered += 1;
    if (rendered >= visibleCount) {
      start = i;
      break;
    }
  }
  if (rendered < visibleCount) start = 0;

  const boundaryType = events[start]?.type;
  if (boundaryType === "message_delta" || boundaryType === "thinking_delta") {
    let runEnd = start;
    while (events[runEnd + 1]?.type === boundaryType) runEnd += 1;
    const completionType =
      boundaryType === "message_delta" ? "message_complete" : "thinking_complete";
    if (events[runEnd + 1]?.type !== completionType) {
      while (start > 0 && events[start - 1].type === boundaryType) start -= 1;
    }
  }

  return {
    events: events.slice(start),
    hiddenCount: countRenderableTranscriptEvents(events.slice(0, start)),
  };
}

function countRenderableTranscriptEvents(
  events: readonly TranscriptEvent[],
): number {
  let count = 0;
  for (const event of events) {
    if (isRenderableTranscriptEvent(event)) count += 1;
  }
  return count;
}

function isRenderableTranscriptEvent(event: TranscriptEvent): boolean {
  return (
    event.type !== "session_commands" &&
    event.type !== "session_config_options" &&
    event.type !== "session_config_changed"
  );
}
