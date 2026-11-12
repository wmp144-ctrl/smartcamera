/**
 * Mounted once by the app shell.
 *
 * Turns the WebSocket firehose into: (1) cache invalidations so every page sees
 * fresh data, (2) in-system notifications with sound, and (3) the small live
 * state store used by tiles and health panels.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import type { AlertItem, EventItem } from "../../lib/api";
import { notifyDesktop, playAlertSound } from "../../lib/sound";
import { ALL_TOPICS, useLiveStream, type StreamMessage } from "../../lib/ws";
import { useLive, type LiveEventSummary, type LiveTrack } from "../../store/live";
import { useUI } from "../../store/ui";

const INVALIDATE_DEBOUNCE_MS = 2500;

export function LiveWatcher() {
  const client = useQueryClient();
  const pushNotification = useUI((state) => state.pushNotification);
  const soundEnabled = useUI((state) => state.soundEnabled);
  const volume = useUI((state) => state.volume);
  const desktopNotifications = useUI((state) => state.desktopNotifications);
  const setLastMessage = useLive((state) => state.setLastMessage);
  const setConnection = useLive((state) => state.setConnection);
  const setDetections = useLive((state) => state.setDetections);
  const setCameraState = useLive((state) => state.setCameraState);
  const addEvent = useLive((state) => state.addEvent);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<Set<string>>(new Set());

  const scheduleInvalidate = useCallback(
    (keys: string[]) => {
      for (const key of keys) pending.current.add(key);
      if (timer.current) return;
      timer.current = setTimeout(() => {
        timer.current = null;
        const batch = Array.from(pending.current);
        pending.current.clear();
        for (const key of batch) {
          void client.invalidateQueries({ queryKey: [key] });
        }
      }, INVALIDATE_DEBOUNCE_MS);
    },
    [client],
  );

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const handle = useCallback(
    (message: StreamMessage) => {
      setLastMessage(message.ts);
      const data = message.data;

      if (message.topic === "alert") {
        const payload = data.alert as AlertItem | undefined;
        if (payload) {
          const cameraName = (data.camera_name as string | undefined) ?? null;
          pushNotification({
            id: `alert-${payload.id}-${payload.triggered_at}`,
            kind: "alert",
            severity: payload.severity,
            title: payload.title,
            message: payload.message,
            cameraId: payload.camera_id,
            cameraName,
            at: payload.triggered_at,
            sound: payload.sound,
          });
          if (soundEnabled && payload.sound) {
            playAlertSound(payload.severity, volume);
          }
          if (desktopNotifications) {
            notifyDesktop(payload.title, `${cameraName ?? "دوربین"}: ${payload.message}`);
          }
        }
        scheduleInvalidate(["alerts", "alerts-unread", "stats-overview"]);
        return;
      }

      if (message.topic === "event") {
        const payload = data.event as EventItem | undefined;
        if (payload) {
          const summary: LiveEventSummary = {
            id: payload.id,
            camera_id: payload.camera_id,
            camera_name: (data.camera_name as string | undefined) ?? "",
            event_type: payload.event_type,
            object_type: payload.object_type,
            message: payload.message,
            occurred_at: payload.occurred_at,
            severity: payload.severity,
          };
          addEvent(summary);
        }
        scheduleInvalidate(["events", "event-summary", "objects", "stats-overview", "stats-cameras"]);
        return;
      }

      if (message.topic === "detection") {
        const cameraId = Number(data.camera_id);
        if (Number.isFinite(cameraId)) {
          setDetections(cameraId, {
            at: (data.occurred_at as string | undefined) ?? message.ts,
            tracks: (data.tracks as LiveTrack[] | undefined) ?? [],
          });
        }
        return;
      }

      if (message.topic === "status") {
        const cameraId = Number(data.camera_id);
        if (Number.isFinite(cameraId)) {
          setCameraState(cameraId, {
            state: (data.state as string | undefined) ?? "unknown",
            message: (data.message as string | undefined) ?? null,
            at: message.ts,
          });
        }
        scheduleInvalidate(["cameras-status", "cameras"]);
      }
    },
    [
      addEvent,
      desktopNotifications,
      pushNotification,
      scheduleInvalidate,
      setCameraState,
      setDetections,
      setLastMessage,
      soundEnabled,
      volume,
    ],
  );

  useLiveStream(ALL_TOPICS, handle, setConnection);

  return null;
}
