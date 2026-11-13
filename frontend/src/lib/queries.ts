/**
 * TanStack Query hooks.
 *
 * Every hook is thin — caching, retries and invalidation are the whole point.
 * Mutations invalidate the narrowest set of keys so the UI stays consistent
 * without polling everything.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  ApiError,
  api,
  type Camera,
  type CameraCreatePayload,
  type CameraStatus,
  type CameraUpdatePayload,
  type DayBoundary,
  type GPUInfo,
  type HeatmapResponse,
  type Message,
  type ModelInfo,
  type ObjectFilters,
  type Setting,
  type SpatialSearchRequest,
  type SpatialSearchResult,
  type SystemInfo,
  type Zone,
  type ZonePayload,
} from "./api";

export const keys = {
  cameras: ["cameras"] as const,
  camera: (id: number) => ["camera", id] as const,
  camerasStatus: ["cameras-status"] as const,
  cameraStatus: (id: number) => ["camera-status", id] as const,
  cameraHealth: (id: number, hours: number) => ["camera-health", id, hours] as const,
  zones: (id: number) => ["zones", id] as const,
  availableModels: ["available-models"] as const,
  objectFilters: ["object-filters"] as const,
  objects: (params: unknown) => ["objects", params] as const,
  object: (id: number) => ["object", id] as const,
  objectImages: (id: number) => ["object-images", id] as const,
  events: (params: unknown) => ["events", params] as const,
  eventSummary: (params: unknown) => ["event-summary", params] as const,
  alerts: (params: unknown) => ["alerts", params] as const,
  alertsUnread: ["alerts-unread"] as const,
  overview: (days: number) => ["stats-overview", days] as const,
  statsCameras: (days: number) => ["stats-cameras", days] as const,
  activity: (params: unknown) => ["stats-activity", params] as const,
  statCounts: (kind: string, params: unknown) => ["stats-counts", kind, params] as const,
  busiestHours: (days: number) => ["stats-busiest-hours", days] as const,
  topZones: (limit: number) => ["stats-top-zones", limit] as const,
  heatmap: (cameraId: number, params: unknown) => ["heatmap", cameraId, params] as const,
  storage: ["storage"] as const,
  systemInfo: ["system-info"] as const,
  systemTime: ["system-time"] as const,
  dayBounds: (jalali: string) => ["day-bounds", jalali] as const,
  gpu: ["gpu"] as const,
  models: ["models"] as const,
  settings: ["settings"] as const,
  runtime: ["runtime"] as const,
  compatibility: ["compatibility"] as const,
  spatialSearch: (payload: unknown) => ["spatial-search", payload] as const,
};

/** Shared invalidation helpers. */
export function invalidateCameraData(client: QueryClient, cameraId?: number) {
  void client.invalidateQueries({ queryKey: keys.cameras });
  void client.invalidateQueries({ queryKey: keys.camerasStatus });
  if (cameraId !== undefined) {
    void client.invalidateQueries({ queryKey: keys.camera(cameraId) });
    void client.invalidateQueries({ queryKey: keys.cameraStatus(cameraId) });
    void client.invalidateQueries({ queryKey: keys.zones(cameraId) });
  }
}

/* ------------------------------------------------------------------ */
/* Cameras                                                            */
/* ------------------------------------------------------------------ */

/**
 * Poll cadence for a resource that may not exist yet.
 *
 * A 4xx (typically 404 for a camera that was deleted, or an id from a stale
 * link) will never fix itself, so stop the interval instead of hammering the
 * server — and the console — once per tick forever.
 */
function pollWhilePresent(interval: number) {
  return (query: { state: { error: unknown } }): number | false =>
    query.state.error instanceof ApiError &&
    query.state.error.status >= 400 &&
    query.state.error.status < 500
      ? false
      : interval;
}

export function useCameras(
  params?: { enabled?: boolean; search?: string },
  options?: { refetchInterval?: number | false },
): UseQueryResult<Camera[]> {
  return useQuery({
    queryKey: [...keys.cameras, params],
    queryFn: () => api.cameras.list(params),
    refetchInterval: options?.refetchInterval,
    staleTime: 5_000,
  });
}

export function useCamera(id: number | null): UseQueryResult<Camera> {
  return useQuery({
    queryKey: keys.camera(id ?? -1),
    queryFn: () => api.cameras.get(id as number),
    enabled: id !== null && id > 0,
    staleTime: 5_000,
  });
}

export function useCameraStatuses(refetchInterval = 5000): UseQueryResult<CameraStatus[]> {
  return useQuery({
    queryKey: keys.camerasStatus,
    queryFn: () => api.cameras.statusAll(),
    refetchInterval,
  });
}

export function useCameraStatus(id: number | null, refetchInterval = 4000) {
  return useQuery({
    queryKey: keys.cameraStatus(id ?? -1),
    queryFn: () => api.cameras.status(id as number),
    enabled: id !== null && id > 0,
    refetchInterval: pollWhilePresent(refetchInterval),
  });
}

export function useCameraHealth(id: number | null, hours = 6) {
  return useQuery({
    queryKey: keys.cameraHealth(id ?? -1, hours),
    queryFn: () => api.cameras.health(id as number, { hours, limit: 600 }),
    enabled: id !== null && id > 0,
    refetchInterval: pollWhilePresent(30_000),
  });
}

export function useZones(cameraId: number | null): UseQueryResult<Zone[]> {
  return useQuery({
    queryKey: keys.zones(cameraId ?? -1),
    queryFn: () => api.cameras.zones(cameraId as number),
    enabled: cameraId !== null && cameraId > 0,
  });
}

export function useAvailableModels() {
  return useQuery({
    queryKey: keys.availableModels,
    queryFn: () => api.cameras.models(),
    staleTime: 60_000,
  });
}

export function useCreateCamera(): UseMutationResult<Camera, Error, CameraCreatePayload> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: CameraCreatePayload) => api.cameras.create(payload),
    onSuccess: (camera) => invalidateCameraData(client, camera.id),
  });
}

export function useUpdateCamera(): UseMutationResult<
  Camera,
  Error,
  { id: number; payload: CameraUpdatePayload }
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }) => api.cameras.update(id, payload),
    onSuccess: (camera) => invalidateCameraData(client, camera.id),
  });
}

export function useDeleteCamera(): UseMutationResult<Message, Error, number> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.cameras.remove(id),
    onSuccess: () => invalidateCameraData(client),
  });
}

export function useReloadCamera(): UseMutationResult<Message, Error, number> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.cameras.reload(id),
    onSuccess: (_data, id) => invalidateCameraData(client, id),
  });
}

export function useTestCamera() {
  return useMutation({ mutationFn: (id: number) => api.cameras.test(id) });
}

export function useToggleDetection(): UseMutationResult<
  Message,
  Error,
  { id: number; enabled: boolean }
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }) => api.cameras.setDetection(id, enabled),
    onSuccess: (_data, variables) => {
      void client.invalidateQueries({ queryKey: keys.camera(variables.id) });
      void client.invalidateQueries({ queryKey: keys.cameras });
      void client.invalidateQueries({ queryKey: keys.camerasStatus });
    },
  });
}

export function useSaveZone() {
  const client = useQueryClient();
  return useMutation<
    Zone,
    Error,
    { cameraId: number; zoneId?: number; payload: ZonePayload | Partial<ZonePayload> }
  >({
    mutationFn: ({ cameraId, zoneId, payload }) =>
      zoneId === undefined
        ? api.cameras.createZone(cameraId, payload as ZonePayload)
        : api.cameras.updateZone(cameraId, zoneId, payload),
    onSuccess: (_data, variables) => {
      void client.invalidateQueries({ queryKey: keys.zones(variables.cameraId) });
      void client.invalidateQueries({ queryKey: keys.camera(variables.cameraId) });
    },
  });
}

export function useDeleteZone() {
  const client = useQueryClient();
  return useMutation<Message, Error, { cameraId: number; zoneId: number }>({
    mutationFn: ({ cameraId, zoneId }) => api.cameras.deleteZone(cameraId, zoneId),
    onSuccess: (_data, variables) => {
      void client.invalidateQueries({ queryKey: keys.zones(variables.cameraId) });
      void client.invalidateQueries({ queryKey: keys.camera(variables.cameraId) });
    },
  });
}

/* ------------------------------------------------------------------ */
/* Objects                                                            */
/* ------------------------------------------------------------------ */

export interface ObjectQueryParams {
  start?: string | null;
  end?: string | null;
  camera_id?: number[];
  object_type?: string[];
  direction?: string[];
  motion_state?: string;
  min_confidence?: number;
  min_quality?: number;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export function useObjects(params: ObjectQueryParams) {
  return useQuery({
    queryKey: keys.objects(params),
    queryFn: () => api.objects.list(params),
    placeholderData: (previous) => previous,
  });
}

export function useObjectFilters(): UseQueryResult<ObjectFilters> {
  return useQuery({
    queryKey: keys.objectFilters,
    queryFn: () => api.objects.filters(),
    staleTime: 60_000,
  });
}

export function useObject(trackId: number | null) {
  return useQuery({
    queryKey: keys.object(trackId ?? -1),
    queryFn: () => api.objects.get(trackId as number),
    enabled: trackId !== null && trackId > 0,
  });
}

export function useDeleteObject() {
  const client = useQueryClient();
  return useMutation<Message, Error, number>({
    mutationFn: (trackId: number) => api.objects.remove(trackId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["objects"] });
      void client.invalidateQueries({ queryKey: keys.objectFilters });
    },
  });
}

/* ------------------------------------------------------------------ */
/* Events / alerts                                                    */
/* ------------------------------------------------------------------ */

export interface EventQueryParams {
  start?: string | null;
  end?: string | null;
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
}

export function useEvents(params: EventQueryParams, refetchInterval?: number) {
  return useQuery({
    queryKey: keys.events(params),
    queryFn: () => api.events.list(params),
    placeholderData: (previous) => previous,
    refetchInterval,
  });
}

export function useEventSummary(params: { start?: string | null; end?: string | null; camera_id?: number[] }) {
  return useQuery({
    queryKey: keys.eventSummary(params),
    queryFn: () => api.events.summary(params),
  });
}

export function useAcknowledgeEvent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.events.acknowledge(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["events"] });
      void client.invalidateQueries({ queryKey: ["event-summary"] });
    },
  });
}

export function useAcknowledgeAllEvents() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.events.acknowledgeAll(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["events"] });
      void client.invalidateQueries({ queryKey: ["event-summary"] });
    },
  });
}

export function useDeleteEvent() {
  const client = useQueryClient();
  return useMutation<Message, Error, number>({
    mutationFn: (id: number) => api.events.remove(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["events"] });
      void client.invalidateQueries({ queryKey: ["event-summary"] });
    },
  });
}

export interface AlertQueryParams {
  start?: string | null;
  end?: string | null;
  camera_id?: number[];
  severity?: string[];
  acknowledged?: boolean;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export function useAlerts(params: AlertQueryParams, refetchInterval?: number) {
  return useQuery({
    queryKey: keys.alerts(params),
    queryFn: () => api.alerts.list(params),
    placeholderData: (previous) => previous,
    refetchInterval,
  });
}

export function useUnreadAlerts(refetchInterval = 20_000) {
  return useQuery({
    queryKey: keys.alertsUnread,
    queryFn: () => api.alerts.unread(),
    refetchInterval,
  });
}

export function useAcknowledgeAlert() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.alerts.acknowledge(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["alerts"] });
      void client.invalidateQueries({ queryKey: keys.alertsUnread });
    },
  });
}

export function useAcknowledgeAllAlerts() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.alerts.acknowledgeAll(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["alerts"] });
      void client.invalidateQueries({ queryKey: keys.alertsUnread });
    },
  });
}

/* ------------------------------------------------------------------ */
/* Stats                                                              */
/* ------------------------------------------------------------------ */

export function useOverview(days = 14, refetchInterval?: number) {
  return useQuery({
    queryKey: keys.overview(days),
    queryFn: () => api.stats.overview(days),
    refetchInterval,
  });
}

export function useCameraStats(days = 7) {
  return useQuery({ queryKey: keys.statsCameras(days), queryFn: () => api.stats.cameras(days) });
}

export function useActivity(
  params: { start?: string | null; end?: string | null; camera_id?: number[]; hourly?: boolean },
  refetchInterval?: number,
) {
  return useQuery({
    queryKey: keys.activity(params),
    queryFn: () => api.stats.activity(params),
    refetchInterval,
  });
}

export function useStatCounts(
  kind: "objects" | "directions" | "event-types",
  params: { start?: string | null; end?: string | null; limit?: number },
) {
  return useQuery({
    queryKey: keys.statCounts(kind, params),
    queryFn: () => {
      if (kind === "objects") return api.stats.objects(params);
      if (kind === "directions") return api.stats.directions(params);
      return api.stats.eventTypes(params);
    },
  });
}

export function useBusiestHours(days = 7) {
  return useQuery({ queryKey: keys.busiestHours(days), queryFn: () => api.stats.busiestHours(days) });
}

export function useTopZones(limit = 10) {
  return useQuery({ queryKey: keys.topZones(limit), queryFn: () => api.stats.topZones(limit) });
}

export function useHeatmap(
  cameraId: number | null,
  params: {
    day?: string;
    object_type?: string;
    days_back?: number;
    grid_w?: number;
    grid_h?: number;
    hour_from?: number;
    hour_to?: number;
  } = {},
): UseQueryResult<HeatmapResponse> {
  return useQuery({
    queryKey: keys.heatmap(cameraId ?? -1, params),
    queryFn: () => api.stats.heatmap(cameraId as number, params),
    enabled: cameraId !== null && cameraId > 0,
  });
}

export function useStorage() {
  return useQuery({ queryKey: keys.storage, queryFn: () => api.stats.storage() });
}

/* ------------------------------------------------------------------ */
/* System                                                             */
/* ------------------------------------------------------------------ */

export function useSystemInfo(refetchInterval?: number): UseQueryResult<SystemInfo> {
  return useQuery({
    queryKey: keys.systemInfo,
    queryFn: () => api.system.info(),
    refetchInterval,
  });
}

export function useSystemTime(refetchInterval = 30_000) {
  return useQuery({ queryKey: keys.systemTime, queryFn: () => api.system.time(), refetchInterval });
}

export function useDayBounds(jalali: string | null): UseQueryResult<DayBoundary> {
  return useQuery({
    queryKey: keys.dayBounds(jalali ?? ""),
    queryFn: () => api.system.dayBounds({ jalali: jalali as string }),
    enabled: Boolean(jalali),
  });
}

export function useGPU(refetchInterval = 4000): UseQueryResult<GPUInfo> {
  return useQuery({ queryKey: keys.gpu, queryFn: () => api.system.gpu(), refetchInterval });
}

export function useModels(): UseQueryResult<ModelInfo[]> {
  return useQuery({ queryKey: keys.models, queryFn: () => api.system.models(), staleTime: 60_000 });
}

export function useSettings(): UseQueryResult<Setting[]> {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.system.settings() });
}

export function useSaveSetting() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) => api.system.putSetting(key, value),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.settings }),
  });
}

export function useRuntime(refetchInterval = 5000) {
  return useQuery({
    queryKey: keys.runtime,
    queryFn: () => api.system.runtime(),
    refetchInterval,
  });
}

export function useCompatibility() {
  return useQuery({
    queryKey: keys.compatibility,
    queryFn: () => api.system.compatibility(),
    staleTime: 120_000,
  });
}

export function useRunRetention() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ days, dryRun }: { days?: number; dryRun?: boolean }) =>
      api.system.runRetention(days, dryRun),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.storage }),
  });
}

export function useInitDb() {
  return useMutation({ mutationFn: () => api.system.initDb() });
}

/* ------------------------------------------------------------------ */
/* Spatial search                                                     */
/* ------------------------------------------------------------------ */

export function useSpatialSearch(
  payload: SpatialSearchRequest | null,
): UseQueryResult<SpatialSearchResult> {
  return useQuery({
    queryKey: keys.spatialSearch(payload),
    queryFn: () => api.search.spatial(payload as SpatialSearchRequest),
    enabled: payload !== null,
    placeholderData: (previous) => previous,
  });
}

export function usePointQuery() {
  return useMutation({
    mutationFn: (payload: SpatialSearchRequest) => api.search.point(payload),
  });
}
