/**
 * Typed API client.
 *
 * One place that knows every route the FastAPI backend exposes, so the rest of
 * the app never hand-writes a fetch. All datetimes crossing this boundary are
 * UTC ISO strings — formatting to local time / Jalali happens in the UI layer.
 */

/* ------------------------------------------------------------------ */
/* Types                                                              */
/* ------------------------------------------------------------------ */

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Message {
  detail: string;
  ok: boolean;
}

export interface ClassRule {
  enabled: boolean;
  threshold: number;
  min_width: number;
  min_height: number;
  min_motion: number;
}

export interface ReconnectPolicy {
  max_retries: number;
  backoff_initial: number;
  backoff_max: number;
  backoff_factor: number;
}

export interface Zone {
  id: number;
  camera_id: number;
  name: string;
  kind: "region" | "ignore" | "line" | "alert";
  points: [number, number][];
  enabled: boolean;
  color: string;
  alert_enabled: boolean;
  alert_sound: boolean;
  alert_classes: string[];
  schedule: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Camera {
  id: number;
  name: string;
  location: string | null;
  rtsp_url: string;
  enabled: boolean;
  transport: "tcp" | "udp";
  fps: number;
  detection_fps: number;
  width: number | null;
  height: number | null;
  model_name: string;
  model_weights: string | null;
  detection_enabled: boolean;
  motion_sensitivity: number;
  record_objects: boolean;
  record_snapshots: boolean;
  notes: string | null;
  class_config: Record<string, Partial<ClassRule>>;
  reconnect_policy: Partial<ReconnectPolicy>;
  created_at: string;
  updated_at: string;
  zones: Zone[];
}

export interface CameraStatus {
  camera_id: number;
  state: string;
  fps: number;
  detection_fps: number;
  bitrate_kbps: number;
  width: number;
  height: number;
  latency_ms: number;
  jitter_ms: number;
  dropped_frames: number;
  reconnect_count: number;
  last_frame_at: string | null;
  detection_enabled: boolean;
  message: string | null;
  detector_error: string | null;
}

export interface HealthMetric {
  id: number;
  camera_id: number;
  state: string;
  fps: number;
  detection_fps: number;
  bitrate_kbps: number;
  width: number;
  height: number;
  latency_ms: number;
  jitter_ms: number;
  dropped_frames: number;
  reconnect_count: number;
  recorded_at: string;
  last_frame_at: string | null;
}

export interface ObjectImage {
  id: number;
  track_id: number;
  camera_id: number;
  kind: string;
  full_path: string;
  crop_path: string;
  thumb_path: string | null;
  bbox: number[];
  confidence: number;
  quality_score: number;
  sharpness: number;
  brightness: number;
  frame_width: number;
  frame_height: number;
  captured_at: string;
  is_best: boolean;
  full_url: string | null;
  crop_url: string | null;
  thumb_url: string | null;
  captured_at_jalali: string;
}

export interface Track {
  id: number;
  camera_id: number;
  track_id: number;
  object_type: string;
  confidence: number;
  first_seen: string;
  last_seen: string;
  duration_seconds: number;
  motion_state: string;
  direction: string;
  velocity_x: number;
  velocity_y: number;
  distance_travelled: number;
  bbox_first: number[];
  bbox_last: number[];
  status: string;
  entered_frame: boolean;
  exited_frame: boolean;
  zone_id: number | null;
  crossed_line_id: number | null;
  line_direction: string | null;
  best_image_id: number | null;
  frame_count: number;
  meta: Record<string, unknown>;
  created_at: string;
  first_seen_jalali: string;
  last_seen_jalali: string;
}

export interface TrackDetail extends Track {
  images: ObjectImage[];
  best_image: ObjectImage | null;
}

export interface EventItem {
  id: number;
  camera_id: number;
  track_id: number | null;
  zone_id: number | null;
  event_type: string;
  severity: string;
  object_type: string | null;
  confidence: number;
  direction: string | null;
  message: string;
  occurred_at: string;
  ended_at: string | null;
  snapshot_full_path: string | null;
  snapshot_crop_path: string | null;
  bbox: number[];
  acknowledged: boolean;
  acknowledged_at: string | null;
  meta: Record<string, unknown>;
  created_at: string;
  snapshot_url: string | null;
  crop_url: string | null;
  occurred_at_jalali: string;
}

export interface AlertItem {
  id: number;
  camera_id: number;
  zone_id: number | null;
  event_id: number | null;
  rule_name: string;
  severity: string;
  title: string;
  message: string;
  object_type: string | null;
  triggered_at: string;
  sound: boolean;
  delivered: boolean;
  acknowledged: boolean;
  acknowledged_at: string | null;
  meta: Record<string, unknown>;
  triggered_at_jalali: string;
}

export interface CountByKey {
  key: string;
  count: number;
  extra: Record<string, unknown>;
}

export interface TimeBucket {
  bucket: string;
  jalali: string;
  count: number;
}

export interface CameraStats {
  camera_id: number;
  camera_name: string;
  total_tracks: number;
  total_events: number;
  total_alerts: number;
  by_type: CountByKey[];
  state: string;
  fps: number;
}

export interface OverviewStats {
  generated_at: string;
  timezone: string;
  total_cameras: number;
  online_cameras: number;
  offline_cameras: number;
  total_tracks: number;
  total_events: number;
  total_alerts: number;
  unacknowledged_alerts: number;
  tracks_today: number;
  events_today: number;
  objects_today: number;
  storage_bytes: number;
  by_type: CountByKey[];
  by_direction: CountByKey[];
  by_event_type: CountByKey[];
  hourly: TimeBucket[];
  daily: TimeBucket[];
  cameras: CameraStats[];
  recent_events: EventItem[];
}

export interface HeatmapResponse {
  camera_id: number;
  grid_w: number;
  grid_h: number;
  day: string | null;
  jalali: string | null;
  max_count: number;
  total: number;
  cells: number[];
  object_type: string;
}

export interface GPUMetric {
  index: number;
  name: string;
  utilization: number;
  memory_used_mb: number;
  memory_total_mb: number;
  memory_percent: number;
  temperature_c: number | null;
  power_w: number | null;
  active_cameras: number;
  detection_fps: number;
}

export interface GPUInfo {
  available: boolean;
  source: string;
  driver_version: string | null;
  cuda_version: string | null;
  devices: GPUMetric[];
}

export interface ModelInfo {
  key: string;
  name: string;
  resolution: number;
  weights_filename: string;
  available: boolean;
  path: string | null;
  description: string;
}

export interface SystemComponent {
  name: string;
  version: string;
  available: boolean;
}

export interface SystemInfo {
  app_name: string;
  version: string;
  python_version: string;
  platform: string;
  timezone: string;
  utc_now: string;
  local_now: string;
  jalali_now: string;
  database_url: string;
  storage_root: string;
  storage_bytes: number;
  components: SystemComponent[];
  gpu: GPUInfo;
}

export interface Setting {
  key: string;
  value: unknown;
  description: string | null;
  updated_at: string;
}

export interface StorageUsage {
  root: string;
  total_bytes: number;
  by_camera: Record<string, number>;
  by_type: Record<string, number>;
  file_count: number;
}

export interface DayBoundary {
  jalali: string;
  jalali_year: number;
  jalali_month: number;
  jalali_day: number;
  start_utc: string;
  end_utc: string;
  start_local: string;
  end_local: string;
  timezone: string;
}

export interface ObjectFilters {
  object_types: { value: string; label: string; count: number }[];
  directions: { value: string; label: string; count: number }[];
  directions_all: { value: string; label: string }[];
  cameras: { value: number; label: string }[];
  range: { min: string | null; max: string | null };
}

export interface SpatialSearchRequest {
  camera_ids?: number[] | null;
  polygon?: [number, number][] | null;
  start?: string | null;
  end?: string | null;
  object_types?: string[] | null;
  directions?: string[] | null;
  min_confidence?: number | null;
  min_quality?: number | null;
  motion_state?: "moving" | "stationary" | null;
  limit?: number;
  offset?: number;
  order?: "asc" | "desc";
}

export interface SpatialSearchResult {
  total: number;
  items: TrackDetail[];
  polygon: [number, number][] | null;
}

export interface CameraCreatePayload {
  name: string;
  location?: string | null;
  rtsp_url: string;
  enabled?: boolean;
  transport?: "tcp" | "udp";
  fps?: number;
  detection_fps?: number;
  model_name?: string;
  model_weights?: string | null;
  detection_enabled?: boolean;
  motion_sensitivity?: number;
  record_objects?: boolean;
  record_snapshots?: boolean;
  notes?: string | null;
  class_config?: Record<string, ClassRule>;
}

export type CameraUpdatePayload = Partial<CameraCreatePayload> & {
  reconnect_policy?: Partial<ReconnectPolicy> | null;
};

export interface ZonePayload {
  name: string;
  kind: "region" | "ignore" | "line" | "alert";
  points: [number, number][];
  enabled?: boolean;
  color?: string;
  alert_enabled?: boolean;
  alert_sound?: boolean;
  alert_classes?: string[];
}

export interface AvailableModels {
  models: (ModelInfo & { loaded: boolean; error?: string | null })[];
  default: string;
  device: string;
}

export interface WsMessage<T = Record<string, unknown>> {
  type: "hello" | "ping" | "message";
  topic?: string;
  ts?: string;
  data?: T;
  [key: string]: unknown;
}

/* ------------------------------------------------------------------ */
/* Transport                                                          */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  readonly status: number;
  readonly errors: { field: string; message: string }[];

  constructor(status: number, message: string, errors: { field: string; message: string }[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errors = errors;
  }
}

type QueryValue = string | number | boolean | null | undefined | (string | number)[];

/** Anything we can serialise into a query string. */
export type QueryParams = object;

function buildQuery(params?: QueryParams): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params as Record<string, QueryValue>)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item === undefined || item === null || item === "") continue;
        search.append(key, String(item));
      }
    } else {
      search.append(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function request<T>(
  method: string,
  path: string,
  options: { params?: QueryParams; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const url = `${path}${buildQuery(options.params)}`;
  const init: RequestInit = {
    method,
    headers: { Accept: "application/json" },
    signal: options.signal,
  };
  if (options.body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if ((error as Error)?.name === "AbortError") throw error;
    throw new ApiError(0, "ارتباط با سرور برقرار نشد");
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const data = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail =
      (data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : null) ?? `خطای سرور (${response.status})`;
    const errors =
      data && typeof data === "object" && Array.isArray((data as { errors?: unknown }).errors)
        ? ((data as { errors: { field: string; message: string }[] }).errors ?? [])
        : [];
    throw new ApiError(response.status, detail, errors);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

const get = <T,>(path: string, params?: QueryParams, signal?: AbortSignal) =>
  request<T>("GET", path, { params, signal });
const post = <T,>(path: string, body?: unknown, params?: QueryParams) =>
  request<T>("POST", path, { body, params });
const patch = <T,>(path: string, body?: unknown) => request<T>("PATCH", path, { body });
const put = <T,>(path: string, body?: unknown) => request<T>("PUT", path, { body });
const del = <T,>(path: string) => request<T>("DELETE", path);

/* ------------------------------------------------------------------ */
/* Endpoints                                                          */
/* ------------------------------------------------------------------ */

export interface WindowParams {
  start?: string | null;
  end?: string | null;
}

export const api = {
  cameras: {
    list: (params?: { enabled?: boolean; search?: string }) =>
      get<Camera[]>("/api/cameras", params),
    get: (id: number) => get<Camera>(`/api/cameras/${id}`),
    create: (payload: CameraCreatePayload) => post<Camera>("/api/cameras", payload),
    update: (id: number, payload: CameraUpdatePayload) =>
      patch<Camera>(`/api/cameras/${id}`, payload),
    remove: (id: number) => del<Message>(`/api/cameras/${id}`),
    reload: (id: number) => post<Message>(`/api/cameras/${id}/reload`),
    test: (id: number) =>
      post<{
        ok: boolean;
        message: string;
        width: number | null;
        height: number | null;
        fps: number | null;
        codec?: string;
      }>(`/api/cameras/${id}/test`),
    setDetection: (id: number, enabled: boolean) =>
      post<Message>(`/api/cameras/${id}/detection`, undefined, { enabled }),
    models: () => get<AvailableModels>("/api/cameras/models/available"),
    statusAll: () => get<CameraStatus[]>("/api/cameras/status/all"),
    status: (id: number) => get<CameraStatus>(`/api/cameras/${id}/status`),
    health: (id: number, params?: { hours?: number; limit?: number }) =>
      get<HealthMetric[]>(`/api/cameras/${id}/health`, params),
    zones: (id: number) => get<Zone[]>(`/api/cameras/${id}/zones`),
    createZone: (id: number, payload: ZonePayload) =>
      post<Zone>(`/api/cameras/${id}/zones`, payload),
    updateZone: (id: number, zoneId: number, payload: Partial<ZonePayload>) =>
      patch<Zone>(`/api/cameras/${id}/zones/${zoneId}`, payload),
    deleteZone: (id: number, zoneId: number) =>
      del<Message>(`/api/cameras/${id}/zones/${zoneId}`),
    liveUrl: (id: number, overlay: boolean, fps = 20) =>
      `/api/cameras/${id}/live${buildQuery({ overlay, fps })}`,
    snapshotUrl: (id: number, overlay: boolean) =>
      `/api/cameras/${id}/snapshot${buildQuery({ overlay })}`,
  },

  objects: {
    list: (
      params: WindowParams & {
        camera_id?: number[];
        object_type?: string[];
        direction?: string[];
        motion_state?: string;
        min_confidence?: number;
        min_quality?: number;
        order?: "asc" | "desc";
        page?: number;
        page_size?: number;
      },
    ) => get<Page<TrackDetail>>("/api/objects", params),
    filters: () => get<ObjectFilters>("/api/objects/filters"),
    get: (trackId: number) => get<TrackDetail>(`/api/objects/${trackId}`),
    images: (trackId: number) => get<ObjectImage[]>(`/api/objects/${trackId}/images`),
    best: (trackId: number) => get<ObjectImage | null>(`/api/objects/${trackId}/best`),
    remove: (trackId: number) => del<Message>(`/api/objects/${trackId}`),
  },

  events: {
    list: (
      params: WindowParams & {
        camera_id?: number[];
        event_type?: string[];
        object_type?: string[];
        severity?: string[];
        direction?: string[];
        acknowledged?: boolean;
        search?: string;
        order?: "asc" | "desc";
        page?: number;
        page_size?: number;
      },
    ) => get<Page<EventItem>>("/api/events", params),
    summary: (params?: WindowParams & { camera_id?: number[] }) =>
      get<{ by_type: Record<string, number>; types: string[]; unacknowledged: number }>(
        "/api/events/summary",
        params,
      ),
    get: (id: number) => get<EventItem>(`/api/events/${id}`),
    acknowledge: (id: number) => post<EventItem>(`/api/events/${id}/acknowledge`),
    acknowledgeAll: () => post<Message>("/api/events/acknowledge-all"),
    remove: (id: number) => del<Message>(`/api/events/${id}`),
  },

  alerts: {
    list: (
      params: WindowParams & {
        camera_id?: number[];
        severity?: string[];
        acknowledged?: boolean;
        order?: "asc" | "desc";
        page?: number;
        page_size?: number;
      },
    ) => get<Page<AlertItem>>("/api/alerts", params),
    unread: () => get<{ unread: number; latest: AlertItem[] }>("/api/alerts/unread"),
    acknowledge: (id: number) => post<AlertItem>(`/api/alerts/${id}/acknowledge`),
    acknowledgeAll: () => post<Message>("/api/alerts/acknowledge-all"),
  },

  search: {
    spatial: (payload: SpatialSearchRequest) =>
      post<SpatialSearchResult>("/api/search/spatial", payload),
    point: (payload: SpatialSearchRequest) => post<Record<string, unknown>>("/api/search/point", payload),
    exportCsv: async (payload: SpatialSearchRequest): Promise<Blob> => {
      const response = await fetch("/api/search/spatial/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new ApiError(response.status, "خروجی CSV آماده نشد");
      return response.blob();
    },
  },

  stats: {
    overview: (days = 14) => get<OverviewStats>("/api/stats/overview", { days }),
    cameras: (days = 7) => get<CameraStats[]>("/api/stats/cameras", { days }),
    activity: (params: WindowParams & { camera_id?: number[]; hourly?: boolean }) =>
      get<TimeBucket[]>("/api/stats/activity", params),
    objects: (params: WindowParams & { limit?: number }) =>
      get<CountByKey[]>("/api/stats/objects", params),
    directions: (params: WindowParams & { limit?: number }) =>
      get<CountByKey[]>("/api/stats/directions", params),
    eventTypes: (params: WindowParams & { limit?: number }) =>
      get<CountByKey[]>("/api/stats/event-types", params),
    busiestHours: (days = 7) => get<CountByKey[]>("/api/stats/busiest-hours", { days }),
    topZones: (limit = 10) => get<CountByKey[]>("/api/stats/top-zones", { limit }),
    heatmap: (cameraId: number, params?: { day?: string; object_type?: string; days_back?: number }) =>
      get<HeatmapResponse>(`/api/stats/heatmap/${cameraId}`, params),
    storage: () => get<StorageUsage>("/api/stats/storage"),
  },

  system: {
    ping: () => get<{ ok: boolean; time: string; version: string }>("/api/system/ping"),
    info: () => get<SystemInfo>("/api/system/info"),
    time: () =>
      get<{
        utc: string;
        local: string;
        timezone: string;
        jalali: string;
        jalali_long: string;
        weekday_fa: string;
        day_start_utc: string;
        day_end_utc: string;
      }>("/api/system/time"),
    dayBounds: (params: { jalali?: string; year?: number; month?: number; day?: number }) =>
      get<DayBoundary>("/api/system/day-bounds", params),
    gpu: () => get<GPUInfo>("/api/system/gpu"),
    compatibility: () => get<Record<string, unknown>>("/api/system/compatibility"),
    models: () => get<ModelInfo[]>("/api/system/models"),
    settings: () => get<Setting[]>("/api/system/settings"),
    putSetting: (key: string, value: unknown) =>
      put<Setting>(`/api/system/settings/${encodeURIComponent(key)}`, { value }),
    runtime: () => get<Record<string, unknown>>("/api/system/runtime"),
    runRetention: (days?: number, dryRun = false) =>
      post<Record<string, unknown>>("/api/system/retention/run", { days, dry_run: dryRun }),
    initDb: () => post<{ ok: boolean; detail: string }>("/api/system/maintenance/init-db"),
    storage: () => get<StorageUsage>("/api/system/storage"),
  },
};

export const WS_URL = "/api/ws/events";

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
