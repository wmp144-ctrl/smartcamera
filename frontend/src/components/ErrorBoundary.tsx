import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Last-resort guard: a render error in any page used to leave a blank screen
 * (the operator could not even reach Settings to recover). Show the error and
 * offer a reload instead.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("CameraSmart UI crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }
    return (
      <div className="grid min-h-full place-items-center px-4 py-10">
        <div className="panel w-full max-w-md p-6 text-center">
          <p className="text-sm font-medium">خطای غیرمنتظره در رابط کاربری</p>
          <p className="mt-2 text-[11px] leading-5 text-muted">
            صفحه به‌درستی نمایش داده نشد. با بارگذاری مجدد، برنامه بازنشانی می‌شود؛
            داده‌های ضبط‌شده آسیبی نمی‌بینند.
          </p>
          <pre className="mt-3 max-h-32 overflow-auto rounded-xl bg-surface-2/70 p-3 text-left text-[10px] text-danger">
            {this.state.error.message}
          </pre>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 w-full rounded-xl bg-brand px-4 py-2 text-sm font-medium text-[#04202a]"
          >
            بارگذاری مجدد
          </button>
        </div>
      </div>
    );
  }
}
