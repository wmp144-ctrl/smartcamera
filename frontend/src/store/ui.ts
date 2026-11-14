/**
 * Global UI state (Zustand).
 *
 * Deliberately small: server data lives in TanStack Query, this store only owns
 * client-side preferences that should survive reloads (theme, overlay toggles,
 * alert sound) plus the transient live-notification list.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { AlertItem } from "../lib/api";

export interface LiveNotification {
  id: string;
  kind: "alert" | "event";
  severity: string;
  title: string;
  message: string;
  cameraId: number | null;
  cameraName: string | null;
  at: string;
  sound: boolean;
}

interface UIState {
  theme: "dark" | "light";
  sidebarCollapsed: boolean;
  /** cameraId -> "show annotated overlay instead of the raw stream" */
  overlayByCamera: Record<number, boolean>;
  /** Global default: raw stream unless a camera is explicitly toggled. */
  overlayDefault: boolean;
  soundEnabled: boolean;
  desktopNotifications: boolean;
  volume: number;
  autoRefreshSeconds: number;
  /** Frame rate the camera tiles request for the live view (MJPEG costs CPU). */
  gridLiveFps: number;
  notifications: LiveNotification[];
  unreadAlerts: number;

  toggleTheme: () => void;
  setTheme: (theme: "dark" | "light") => void;
  toggleSidebar: () => void;
  setOverlay: (cameraId: number, value: boolean) => void;
  setOverlayDefault: (value: boolean) => void;
  isOverlay: (cameraId: number) => boolean;
  setSound: (value: boolean) => void;
  setDesktopNotifications: (value: boolean) => void;
  setVolume: (value: number) => void;
  setAutoRefresh: (seconds: number) => void;
  setGridLiveFps: (fps: number) => void;
  pushNotification: (notification: LiveNotification) => void;
  dismissNotification: (id: string) => void;
  clearNotifications: () => void;
  setUnreadAlerts: (count: number) => void;
}

export const useUI = create<UIState>()(
  persist(
    (set, get) => ({
      theme: "dark",
      sidebarCollapsed: false,
      overlayByCamera: {},
      overlayDefault: false,
      soundEnabled: true,
      desktopNotifications: false,
      volume: 0.6,
      autoRefreshSeconds: 15,
      gridLiveFps: 10,
      notifications: [],
      unreadAlerts: 0,

      toggleTheme: () => set((state) => ({ theme: state.theme === "dark" ? "light" : "dark" })),
      setTheme: (theme) => set({ theme }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setOverlay: (cameraId, value) =>
        set((state) => ({ overlayByCamera: { ...state.overlayByCamera, [cameraId]: value } })),
      setOverlayDefault: (value) => set({ overlayDefault: value }),
      isOverlay: (cameraId) => get().overlayByCamera[cameraId] ?? get().overlayDefault,
      setSound: (value) => set({ soundEnabled: value }),
      setDesktopNotifications: (value) => set({ desktopNotifications: value }),
      setVolume: (value) => set({ volume: value }),
      setAutoRefresh: (seconds) => set({ autoRefreshSeconds: seconds }),
      setGridLiveFps: (fps) => set({ gridLiveFps: Math.max(1, Math.min(20, Math.round(fps))) }),
      pushNotification: (notification) =>
        set((state) => ({
          notifications: [notification, ...state.notifications].slice(0, 40),
          unreadAlerts: state.unreadAlerts + 1,
        })),
      dismissNotification: (id) =>
        set((state) => ({ notifications: state.notifications.filter((item) => item.id !== id) })),
      clearNotifications: () => set({ notifications: [], unreadAlerts: 0 }),
      setUnreadAlerts: (count) => set({ unreadAlerts: count }),
    }),
    {
      name: "camerasmart-ui",
      partialize: (state) => ({
        theme: state.theme,
        sidebarCollapsed: state.sidebarCollapsed,
        overlayByCamera: state.overlayByCamera,
        overlayDefault: state.overlayDefault,
        soundEnabled: state.soundEnabled,
        desktopNotifications: state.desktopNotifications,
        volume: state.volume,
        autoRefreshSeconds: state.autoRefreshSeconds,
        gridLiveFps: state.gridLiveFps,
      }),
    },
  ),
);

export function alertToNotification(alert: AlertItem, cameraName: string | null): LiveNotification {
  return {
    id: `alert-${alert.id}-${alert.triggered_at}`,
    kind: "alert",
    severity: alert.severity,
    title: alert.title,
    message: alert.message,
    cameraId: alert.camera_id,
    cameraName,
    at: alert.triggered_at,
    sound: alert.sound,
  };
}
