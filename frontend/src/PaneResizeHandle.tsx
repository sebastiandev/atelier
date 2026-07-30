import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
} from "react";

type PaneResizeHandleProps = {
  defaultValue: number;
  edge: "left" | "right";
  label: string;
  max: number;
  min: number;
  onChange: (width: number) => void;
  value: number;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function PaneResizeHandle({
  defaultValue,
  edge,
  label,
  max,
  min,
  onChange,
  value,
}: PaneResizeHandleProps) {
  const valueRef = useRef(value);

  useEffect(() => {
    valueRef.current = value;
  }, [value]);

  function setWidth(next: number) {
    onChange(clamp(Math.round(next), min, max));
  }

  function beginResize(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = valueRef.current;
    const direction = edge === "right" ? 1 : -1;

    document.body.classList.add("pane-resizing");
    event.currentTarget.setPointerCapture(event.pointerId);

    function move(moveEvent: PointerEvent) {
      setWidth(startWidth + (moveEvent.clientX - startX) * direction);
    }

    function stop() {
      document.body.classList.remove("pane-resizing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    const step = event.shiftKey ? 32 : 16;
    const direction = edge === "right" ? 1 : -1;
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setWidth(value + step * direction);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      setWidth(value - step * direction);
    } else if (event.key === "Home") {
      event.preventDefault();
      setWidth(min);
    } else if (event.key === "End") {
      event.preventDefault();
      setWidth(max);
    }
  }

  return (
    <button
      type="button"
      className="pane-resize-handle"
      data-edge={edge}
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      role="separator"
      onPointerDown={beginResize}
      onDoubleClick={() => setWidth(defaultValue)}
      onKeyDown={onKeyDown}
    />
  );
}
