"use client";

import { KeyboardEvent, PointerEvent, useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

interface PanelBounds {
  defaultWidth: number;
  min: number;
  max: number;
}

const KEY_STEP = 16;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function readStored(key: string): number | null {
  try {
    const value = Number(window.localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: number) {
  try {
    window.localStorage.setItem(key, String(Math.round(value)));
  } catch {
    // Width is a convenience; private windows may block storage.
  }
}

/** Current window width, updated on resize; 0 before mount. */
export function useViewportWidth() {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const update = () => setWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return width;
}

/** Panel width that persists per browser and stays inside its bounds. */
export function usePanelWidth(storageKey: string, bounds: PanelBounds) {
  const { defaultWidth, min, max } = bounds;
  const [width, setWidthState] = useState(defaultWidth);

  useEffect(() => {
    const stored = readStored(storageKey);
    if (stored !== null) setWidthState(clamp(stored, min, max));
  }, [storageKey, min, max]);

  const setWidth = useCallback(
    (next: number, persist = true) => {
      const value = clamp(next, min, max);
      setWidthState(value);
      if (persist) writeStored(storageKey, value);
    },
    [storageKey, min, max],
  );

  return { width, setWidth, reset: () => setWidth(defaultWidth), min, max };
}

/**
 * A vertical divider on a panel edge. `side` is the edge the handle sits on: a left
 * rail is resized from its right edge, a right inspector from its left edge.
 */
export function PanelResizeHandle({
  side,
  label,
  width,
  min,
  max,
  onResize,
  onReset,
}: {
  side: "left" | "right";
  label: string;
  width: number;
  min: number;
  max: number;
  onResize: (width: number, persist?: boolean) => void;
  onReset: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const origin = useRef<{ x: number; width: number } | null>(null);
  const direction = side === "right" ? 1 : -1;

  useEffect(() => {
    if (!dragging) return;
    const { cursor, userSelect } = document.body.style;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.cursor = cursor;
      document.body.style.userSelect = userSelect;
    };
  }, [dragging]);

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    origin.current = { x: event.clientX, width };
    setDragging(true);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!origin.current) return;
    onResize(origin.current.width + (event.clientX - origin.current.x) * direction, false);
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>) {
    if (!origin.current) return;
    onResize(origin.current.width + (event.clientX - origin.current.x) * direction, true);
    origin.current = null;
    setDragging(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const grow = event.key === (side === "right" ? "ArrowRight" : "ArrowLeft");
    const shrink = event.key === (side === "right" ? "ArrowLeft" : "ArrowRight");
    if (grow || shrink) onResize(width + (grow ? KEY_STEP : -KEY_STEP));
    else if (event.key === "Home") onResize(min);
    else if (event.key === "End") onResize(max);
    else return;
    event.preventDefault();
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
      className={cn(
        "group absolute inset-y-0 z-20 w-2 cursor-col-resize touch-none",
        side === "right" ? "-right-1" : "-left-1",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors duration-150",
          dragging ? "bg-foreground/40" : "bg-transparent group-hover:bg-foreground/20 group-focus-visible:bg-foreground/40",
        )}
      />
    </div>
  );
}
