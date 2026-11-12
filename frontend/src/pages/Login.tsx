import { Eye, EyeOff, LockKeyhole, ScanEye, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "../components/ui/button";
import { Field, Input } from "../components/ui/field";
import { ApiError, api } from "../lib/api";

interface LoginPageProps {
  /** ``false`` on the very first launch: the password is created here. */
  configured: boolean;
  onSuccess: () => void;
}

export function LoginPage({ configured, onSuccess }: LoginPageProps) {
  const setup = !configured;
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (setup) {
      if (password.length < 8) {
        setError("رمز عبور باید حداقل ۸ نویسه باشد");
        return;
      }
      if (password !== confirm) {
        setError("تکرار رمز عبور یکسان نیست");
        return;
      }
    } else if (!password) {
      setError("رمز عبور را وارد کنید");
      return;
    }

    setBusy(true);
    try {
      if (setup) {
        await api.auth.setup(password, confirm);
      } else {
        await api.auth.login(password);
      }
      onSuccess();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "ارتباط با سرور برقرار نشد",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative grid min-h-full place-items-center overflow-hidden px-4 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 grid-noise opacity-[0.35]"
      />
      <div className="relative w-full max-w-md">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <span className="grid size-14 place-items-center rounded-2xl border border-brand/30 bg-brand/10 text-brand shadow-[0_0_40px_-12px_rgb(34_211_238/0.7)]">
            <ScanEye className="size-7" />
          </span>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">CameraSmart</h1>
            <p className="mt-1 text-xs text-muted">
              سامانه تشخیص اشیا و حرکت در دوربین‌های مداربسته
            </p>
          </div>
        </div>

        <div className="panel overflow-hidden">
          <div className="border-b border-border bg-surface-2/60 px-5 py-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <LockKeyhole className="size-4 text-brand" />
              {setup ? "تعیین رمز ادمین" : "ورود به سامانه"}
            </div>
            <p className="mt-1.5 text-[11px] leading-5 text-muted">
              {setup
                ? "اولین اجرا است. رمز ادمین را دو بار وارد کنید؛ از این پس برای هر ورود همین رمز خواسته می‌شود."
                : "برای دسترسی به تصاویر دوربین‌ها و تنظیمات، رمز ادمین را وارد کنید."}
            </p>
          </div>

          <form className="space-y-4 p-5" onSubmit={submit}>
            <Field label="رمز عبور" hint={setup ? "حداقل ۸ نویسه" : undefined} required>
              <div className="relative">
                <Input
                  type={visible ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoFocus
                  autoComplete={setup ? "new-password" : "current-password"}
                  placeholder="••••••••"
                  className="pl-10"
                  dir="ltr"
                />
                <button
                  type="button"
                  onClick={() => setVisible((value) => !value)}
                  aria-label={visible ? "پنهان‌کردن رمز" : "نمایش رمز"}
                  className="absolute inset-y-0 left-0 grid w-10 place-items-center text-muted hover:text-fg"
                >
                  {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </Field>

            {setup ? (
              <Field label="تکرار رمز عبور" required>
                <Input
                  type={visible ? "text" : "password"}
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  autoComplete="new-password"
                  placeholder="••••••••"
                  dir="ltr"
                />
              </Field>
            ) : null}

            {error ? (
              <p
                role="alert"
                className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-[11px] leading-5 text-danger"
              >
                {error}
              </p>
            ) : null}

            <Button type="submit" variant="primary" size="lg" loading={busy} className="w-full">
              {setup ? "ذخیره رمز و ورود" : "ورود"}
            </Button>

            <p className="flex items-start gap-2 text-[10px] leading-5 text-muted">
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-ok" />
              این سامانه تک‌کاربره است و رمز در پایگاه داده به‌صورت هش‌شده ذخیره می‌شود. پس از
              چند تلاش ناموفق، ورود موقتاً قفل می‌شود.
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
