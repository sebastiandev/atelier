import { create } from "zustand";
import { persist } from "zustand/middleware";

export const WORK_RAIL_MIN = 240;
export const WORK_RAIL_MAX = 520;
export const PLANNING_RAIL_MIN = 248;
export const PLANNING_RAIL_MAX = 420;
export const PLANNING_DOCK_MIN = 340;
export const PLANNING_DOCK_MAX = 620;

type LayoutState = {
  planningDockWidth: number;
  planningRailWidth: number;
  setPlanningDockWidth: (width: number) => void;
  setPlanningRailWidth: (width: number) => void;
  setWorkRailWidth: (width: number) => void;
  workRailWidth: number;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      planningDockWidth: 420,
      planningRailWidth: 296,
      setPlanningDockWidth: (width) =>
        set({ planningDockWidth: clamp(width, PLANNING_DOCK_MIN, PLANNING_DOCK_MAX) }),
      setPlanningRailWidth: (width) =>
        set({ planningRailWidth: clamp(width, PLANNING_RAIL_MIN, PLANNING_RAIL_MAX) }),
      setWorkRailWidth: (width) =>
        set({ workRailWidth: clamp(width, WORK_RAIL_MIN, WORK_RAIL_MAX) }),
      workRailWidth: 280,
    }),
    { name: "atelier:layout", version: 1 },
  ),
);
