import { create } from "zustand";

interface UiState {
  currentSessionId: string;
  collapsed: boolean;
  setCurrentSessionId: (id: string) => void;
  setCollapsed: (v: boolean) => void;
}

export const useUiStore = create<UiState>((set) => ({
  currentSessionId: "",
  collapsed: false,
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
  setCollapsed: (v) => set({ collapsed: v }),
}));
