import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";

// Route-level code splitting: each page becomes its own chunk so the first
// paint only ships the shell + the visited route. The heavy dependencies used
// by a few screens (recharts, the virtualised tables) stay out of the entry
// bundle entirely and are fetched the first time their page is opened.
const DashboardPage = lazy(() =>
  import("./pages/Dashboard").then((m) => ({ default: m.DashboardPage })),
);
const CamerasPage = lazy(() =>
  import("./pages/Cameras").then((m) => ({ default: m.CamerasPage })),
);
const CameraDetailPage = lazy(() =>
  import("./pages/CameraDetail").then((m) => ({ default: m.CameraDetailPage })),
);
const ObjectsPage = lazy(() =>
  import("./pages/Objects").then((m) => ({ default: m.ObjectsPage })),
);
const ObjectDetailPage = lazy(() =>
  import("./pages/ObjectDetail").then((m) => ({ default: m.ObjectDetailPage })),
);
const EventsPage = lazy(() =>
  import("./pages/Events").then((m) => ({ default: m.EventsPage })),
);
const AlertsPage = lazy(() =>
  import("./pages/Alerts").then((m) => ({ default: m.AlertsPage })),
);
const SpatialSearchPage = lazy(() =>
  import("./pages/SpatialSearch").then((m) => ({ default: m.SpatialSearchPage })),
);
const StatsPage = lazy(() =>
  import("./pages/Stats").then((m) => ({ default: m.StatsPage })),
);
const SettingsPage = lazy(() =>
  import("./pages/Settings").then((m) => ({ default: m.SettingsPage })),
);

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/cameras" element={<CamerasPage />} />
        <Route path="/cameras/:cameraId" element={<CameraDetailPage />} />
        <Route path="/objects" element={<ObjectsPage />} />
        <Route path="/objects/:trackId" element={<ObjectDetailPage />} />
        <Route path="/events" element={<EventsPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/search" element={<SpatialSearchPage />} />
        <Route path="/stats" element={<StatsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}
