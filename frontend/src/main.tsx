import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthGate } from "./components/auth/AuthGate";
import { ApiError } from "./lib/api";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The app is meant to run on a LAN/offline box: retrying twice keeps the
      // UI alive through a brief backend restart without hammering a dead host.
      // Client errors are excluded - a 404/422 will not fix itself, so retrying
      // only multiplies the failed requests (a stale camera id was retried on
      // every poll). Network failures and 5xx are still retried.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 2;
      },
      retryDelay: (attempt) => Math.min(8000, 600 * 2 ** attempt),
      staleTime: 10_000,
      refetchOnWindowFocus: true,
      gcTime: 5 * 60_000,
    },
    mutations: { retry: 0 },
  },
});

const container = document.getElementById("root");
if (!container) {
  throw new Error("عنصر #root پیدا نشد");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthGate>
          <App />
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
