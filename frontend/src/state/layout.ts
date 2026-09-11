import { create } from "zustand";
import { persist } from "zustand/middleware";

export const WORK_RAIL_MIN = 240;
export const WORK_RAIL_MAX = 520;
export const HOME_RAIL_MIN = 360;
export const HOME_RAIL_MAX = 560;
export const PLANNING_RAIL_MIN = 248;
export const PLANNING_RAIL_MAX = 420;
export const PLANNING_DOC_MIN = 320;
export const PLANNING_DOC_MAX = 900;
export const PLANNING_DOCK_MIN = 340;
export const PLANNING_DOCK_MAX = 620;
export const LOOP_INSPECTOR_MIN = 340;
export const LOOP_INSPECTOR_MAX = 620;

type LayoutState = {
  homeRailWidth: number;
  loopInspectorWidth: number;
  planningDockWidth: number;
  planningRailWidth: number;
  /** Story doc column folded to a strip on the agent-mode canvas. */
  planningDocCollapsed: boolean;
  setPlanningDocCollapsed: (collapsed: boolean) => void;
  /** Story doc column width on the agent-mode canvas. */
  planningDocWidth: number;
  setPlanningDocWidth: (width: number) => void;
  setPlanningDockWidth: (width: number) => void;
  setPlanningRailWidth: (width: number) => void;
  setHomeRailWidth: (width: number) => void;
  setLoopInspectorWidth: (width: number) => void;
  setWorkRailWidth: (width: number) => void;
  workRailWidth: number;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      homeRailWidth: 500,
      loopInspectorWidth: 420,
      planningDockWidth: 420,
      planningRailWidth: 296,
      planningDocCollapsed: false,
      setPlanningDocCollapsed: (collapsed) => set({ planningDocCollapsed: collapsed }),
      planningDocWidth: 404,
      setPlanningDocWidth: (width) =>
        set({ planningDocWidth: clamp(width, PLANNING_DOC_MIN, PLANNING_DOC_MAX) }),
      setPlanningDockWidth: (width) =>
        set({ planningDockWidth: clamp(width, PLANNING_DOCK_MIN, PLANNING_DOCK_MAX) }),
      setPlanningRailWidth: (width) =>
        set({ planningRailWidth: clamp(width, PLANNING_RAIL_MIN, PLANNING_RAIL_MAX) }),
      setHomeRailWidth: (width) =>
        set({ homeRailWidth: clamp(width, HOME_RAIL_MIN, HOME_RAIL_MAX) }),
      setLoopInspectorWidth: (width) =>
        set({ loopInspectorWidth: clamp(width, LOOP_INSPECTOR_MIN, LOOP_INSPECTOR_MAX) }),
      setWorkRailWidth: (width) =>
        set({ workRailWidth: clamp(width, WORK_RAIL_MIN, WORK_RAIL_MAX) }),
      workRailWidth: 280,
    }),
    { name: "atelier:layout", version: 1 },
  ),
);
