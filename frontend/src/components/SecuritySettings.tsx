import { KeyRound, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "./ui/button";
import { Card, CardBody, CardHeader } from "./ui/card";
import { Field, Input } from "./ui/field";
import { toast } from "./ui/toast";
import { ApiError, api } from "../lib/api";

/**
 * Change the single admin password.
 *
 * The API rotates the session secret on success, which invalidates every other
 * signed-in browser while this one keeps its freshly issued cookie.
 */
export function SecuritySettings() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError("رمز جدید باید حداقل ۸ نویسه باشد");
      return;
    }
    if (next !== confirm) {
      setError("تکرار رمز جدید یکسان نیست");
      return;
    }
    setBusy(true);
    try {
      await api.auth.changePassword(current, next, confirm);
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success("رمز عبور تغییر کرد", "نشست‌های دیگر بسته شدند");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "ارتباط با سرور برقرار نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="امنیت و رمز ادمین"
        subtitle="تنها یک حساب ادمین وجود دارد؛ تغییر رمز، نشست‌های دیگر را می‌بندد."
        icon={<ShieldCheck />}
      />
      <CardBody>
        <form className="space-y-3" onSubmit={submit}>
          <Field label="رمز فعلی" required>
            <Input
              type="password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              autoComplete="current-password"
              dir="ltr"
            />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="رمز جدید" hint="حداقل ۸ نویسه" required>
              <Input
                type="password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
                autoComplete="new-password"
                dir="ltr"
              />
            </Field>
            <Field label="تکرار رمز جدید" required>
              <Input
                type="password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                autoComplete="new-password"
                dir="ltr"
              />
            </Field>
          </div>

          {error ? (
            <p role="alert" className="text-[11px] leading-5 text-danger">
              {error}
            </p>
          ) : null}

          <Button type="submit" variant="primary" loading={busy} disabled={!current}>
            <KeyRound />
            تغییر رمز
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}
