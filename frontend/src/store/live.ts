/**
 * Live state derived from the WebSocket stream.
 *
 * Never persisted — it is a cache of "what the cameras are doing right now",
 * used by the dashboard, the live tiles and the camera health panels.
 */

import { create } from "zustand";
import type { ConnectionState } from "../lib/ws";

export interface LiveTrack {
  track_id: number;
  /** Database id of the stored object (null until the first write lands). */
  db_id?: number | null;
  object_type: string;
  confidence: number;
  bbox: number[];
  direction: string;
  motion_state: string;
}

export interface LiveDetectionFrame {
  at: string;
  tracks: LiveTrack[];
}

export interface LiveCameraState {
  state: string;
  message: string | null;
  at: string;
}

export interface LiveEventSummary {
  id: number;
  camera_id: number;
  camera_name: string;
  event_type: string;
  object_type: string | null;
  message: string;
  occurred_at: string;
  severity: string;
}

interface LiveState {
  connection: ConnectionState;
  lastMessageAt: string | null;
  messageCount: number;
  detections: Record<number, LiveDetectionFrame>;
  cameraStates: Record<number, LiveCameraState>;
  recentEvents: LiveEventSummary[];
  setConnection: (state: ConnectionState) => void;
  setLastMessage: (at: string) => void;
  setDetections: (cameraId: number, frame: LiveDetectionFrame) => void;
  setCameraState: (cameraId: number, state: LiveCameraState) => void;
  addEvent: (event: LiveEventSummary) => void;
  reset: () => void;
}

export const useLive = create<LiveState>((set) => ({
  connection: "closed",
  lastMessageAt: null,
  messageCount: 0,
  detections: {},
  cameraStates: {},
  recentEvents: [],
  setConnection: (connection) => set({ connection }),
  setLastMessage: (at) => set((state) => ({ lastMessageAt: at, messageCount: state.messageCount + 1 })),
  setDetections: (cameraId, frame) =>
    set((state) => ({ detections: { ...state.detections, [cameraId]: frame } })),
  setCameraState: (cameraId, value) =>
    set((state) => ({ cameraStates: { ...state.cameraStates, [cameraId]: value } })),
  addEvent: (event) => set((state) => ({ recentEvents: [event, ...state.recentEvents].slice(0, 60) })),
  reset: () => set({ detections: {}, cameraStates: {}, recentEvents: [], messageCount: 0 }),
}));
