export type FloatingMenuPosition = {
  left: number;
  top: number;
};

export function anchoredMenuPosition(
  anchor: DOMRect,
  {
    width = 160,
    height = 88,
    gap = 4,
    margin = 8,
  }: {
    width?: number;
    height?: number;
    gap?: number;
    margin?: number;
  } = {},
): FloatingMenuPosition {
  const left = Math.max(
    margin,
    Math.min(window.innerWidth - width - margin, anchor.right - width),
  );
  const below = anchor.bottom + gap;
  const top =
    below + height <= window.innerHeight - margin
      ? below
      : Math.max(margin, anchor.top - height - gap);
  return { left, top };
}
