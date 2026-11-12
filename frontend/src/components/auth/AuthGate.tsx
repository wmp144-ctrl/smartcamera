/**
 * Authentication gate.
 *
 * The API answers 401 for every private route once this component's status
 * query says the session is gone, so the shell must never mount while
 * unauthenticated: pages would render empty panels and the WebSocket would
 * retry forever.
 */

import { useQuery } from "@tanstack/react-query";
import { Loader2, ServerCrash } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { api } from "../../lib/api";
import { eventStream } from "../../lib/ws";
import { LoginPage } from "../../pages/Login";
import { Button } from "../ui/button";

export function AuthGate({ children }: { children: ReactNode }) {
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => api.auth.status(),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    const handleUnauthorized = () => {
      eventStream.disconnect();
      void status.refetch();
    };
    window.addEventListener("camerasmart:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("camerasmart:unauthorized", handleUnauthorized);
  }, [status]);

  if (status.isPending) {
    return (
      <div className="grid min-h-full place-items-center">
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="size-4 animate-spin" />
          در حال بررسی وضعیت ورود…
        </div>
      </div>
    );
  }

  if (status.isError) {
    return (
      <div className="grid min-h-full place-items-center px-4">
        <div className="panel w-full max-w-sm p-5 text-center">
          <ServerCrash className="mx-auto size-8 text-danger" />
          <p className="mt-3 text-sm font-medium">ارتباط با سرور برقرار نشد</p>
          <p className="mt-1 text-[11px] leading-5 text-muted">
            مطمئن شوید سرویس CameraSmart در حال اجراست، سپس دوباره تلاش کنید.
          </p>
          <Button className="mt-4 w-full" onClick={() => void status.refetch()}>
            تلاش مجدد
          </Button>
        </div>
      </div>
    );
  }

  if (!status.data?.authenticated) {
    return (
      <LoginPage
        configured={Boolean(status.data?.configured)}
        onSuccess={() => void status.refetch()}
      />
    );
  }

  return <>{children}</>;
}
