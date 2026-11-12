import {
  ArrowRight,
  Clock,
  Crosshair,
  Images,
  Move,
  Route,
  ScanEye,
  Sparkles,
  Square,
  Trash,
  TriangleAlert,
  ZoomIn,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { ConfirmDialog, Dialog } from "../components/ui/dialog";
import { EmptyState, KeyValue, LoadingBlock } from "../components/ui/misc";
import { toast } from "../components/ui/toast";
import { useCameras, useDeleteObject, useObject } from "../lib/queries";
import { formatJalaliDateTime } from "../lib/jalali";
import {
  DIRECTION_ARROWS,
  classLabel,
  directionLabel,
  eventTypeLabel,
  faNumber,
  formatRelative,
  toFaDigits,
} from "../lib/utils";

export function ObjectDetailPage() {
  const { trackId: rawId } = useParams();
  const trackId = Number(rawId);
  const navigate = useNavigate();
  const track = useObject(Number.isFinite(trackId) ? trackId : null);
  const cameras = useCameras();
  const remove = useDeleteObject();
  const [lightbox, setLightbox] = useState<{ url: string; label: string } | null>(null);
  const [confirm, setConfirm] = useState(false);

  const cameraName = useMemo(
    () => cameras.data?.find((camera) => camera.id === track.data?.camera_id)?.name ?? "—",
    [cameras.data, track.data?.camera_id],
  );

  if (!Number.isFinite(trackId)) {
    return <EmptyState icon={<TriangleAlert />} title="شناسه شیء نامعتبر است" />;
  }
  if (track.isLoading) return <LoadingBlock />;
  if (track.isError || !track.data) {
    return (
      <EmptyState
        icon={<TriangleAlert />}
        title="شیء یافت نشد"
        description={(track.error as Error | null)?.message ?? "این شیء حذف شده یا در دسترس نیست."}
        action={
          <Link to="/objects" className="text-xs text-brand hover:underline">
            بازگشت به گالری اشیا
          </Link>
        }
      />
    );
  }

  const item = track.data;
  const best = item.best_image ?? item.images[0] ?? null;
  const stationary = item.motion_state === "stationary";

  const images = [...item.images].sort(
    (a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime(),
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={`${classLabel(item.object_type)} — شیء #${toFaDigits(item.id)}`}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            <span>{cameraName}</span>
            <span className="text-muted">•</span>
            <span>{formatJalaliDateTime(item.first_seen)}</span>
            <span className="text-muted">•</span>
            <span>{formatRelative(item.first_seen)}</span>
          </span>
        }
        icon={<Images />}
        actions={
          <>
            <Link
              to="/objects"
              className="inline-flex items-center gap-1 rounded-xl border border-border px-3 py-2 text-xs text-muted transition-colors hover:border-brand/50 hover:text-fg"
            >
              <ArrowRight className="size-3.5" />
              گالری اشیا
            </Link>
            <Link
              to={`/cameras/${item.camera_id}`}
              className="inline-flex items-center gap-1 rounded-xl border border-border px-3 py-2 text-xs text-muted transition-colors hover:border-brand/50 hover:text-fg"
            >
              <Crosshair className="size-3.5" />
              دوربین
            </Link>
            <Button variant="danger" size="sm" onClick={() => setConfirm(true)}>
              <Trash />
              حذف شیء
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={stationary ? "warn" : "brand"}>
          {stationary ? <Square className="size-3" /> : <Move className="size-3" />}
          {stationary ? "شیء ثابت" : "شیء متحرک"}
        </Badge>
        <Badge tone="muted">
          {DIRECTION_ARROWS[item.direction] ?? "•"} {directionLabel(item.direction)}
        </Badge>
        <Badge tone="muted">
          <ScanEye className="size-3" /> اطمینان {faNumber(item.confidence * 100, 0)}٪
        </Badge>
        {item.entered_frame ? <Badge tone="ok">ورود به کادر ثبت شد</Badge> : null}
        {item.exited_frame ? <Badge tone="info">خروج از کادر ثبت شد</Badge> : null}
        {item.crossed_line_id ? (
          <Badge tone="warn">
            <Route className="size-3" /> عبور از خط {directionLabel(item.line_direction)}
          </Badge>
        ) : null}
        {item.zone_id ? <Badge tone="brand">داخل ناحیه #{toFaDigits(item.zone_id)}</Badge> : null}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader
            title="تصویر کامل صحنه"
            subtitle="فریم اصلی دوربین در لحظه ثبت بهترین تصویر"
            icon={<Images />}
            actions={
              best?.full_url ? (
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => setLightbox({ url: best.full_url as string, label: "تصویر کامل" })}
                >
                  <ZoomIn />
                  بزرگ‌نمایی
                </Button>
              ) : null
            }
          />
          <CardBody>
            {best?.full_url ? (
              <button
                type="button"
                onClick={() => setLightbox({ url: best.full_url as string, label: "تصویر کامل" })}
                className="block w-full overflow-hidden rounded-xl border border-border bg-[#050a14]"
              >
                <img src={best.full_url} alt="تصویر کامل شیء" className="h-auto w-full object-contain" />
              </button>
            ) : (
              <EmptyState icon={<Images />} title="تصویر کامل ذخیره نشده است" />
            )}
          </CardBody>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="تصویر برش‌خورده" subtitle="ناحیه محدود به شیء" icon={<ScanEye />} />
            <CardBody>
              {best?.crop_url ? (
                <button
                  type="button"
                  onClick={() => setLightbox({ url: best.crop_url as string, label: "تصویر برش‌خورده" })}
                  className="mx-auto block max-w-full overflow-hidden rounded-xl border border-border bg-[#050a14]"
                >
                  <img src={best.crop_url} alt="برش شیء" className="max-h-72 object-contain" />
                </button>
              ) : (
                <EmptyState icon={<ScanEye />} title="برش تصویر ذخیره نشده است" />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="مشخصات شیء" icon={<Sparkles />} />
            <CardBody>
              <KeyValue label="شناسه شیء" value={toFaDigits(item.id)} />
              <KeyValue label="شناسه ردیابی" value={toFaDigits(item.track_id)} />
              <KeyValue label="کلاس" value={classLabel(item.object_type)} />
              <KeyValue label="اطمینان" value={`${faNumber(item.confidence * 100, 1)}٪`} />
              <KeyValue label="وضعیت حرکت" value={stationary ? "ثابت" : "متحرک"} />
              <KeyValue label="جهت" value={`${DIRECTION_ARROWS[item.direction] ?? "•"} ${directionLabel(item.direction)}`} />
              <KeyValue
                label="سرعت افقی"
                value={`${faNumber(item.velocity_x * 100, 2)}٪ بر ثانیه`}
              />
              <KeyValue
                label="سرعت عمودی"
                value={`${faNumber(item.velocity_y * 100, 2)}٪ بر ثانیه`}
              />
              <KeyValue label="مسافت طی‌شده" value={`${faNumber(item.distance_travelled * 100, 1)}٪ کادر`} />
              <KeyValue label="مدت حضور" value={`${faNumber(item.duration_seconds, 1)} ثانیه`} />
              <KeyValue label="تعداد فریم‌های پردازش‌شده" value={toFaDigits(item.frame_count)} />
              <KeyValue label="وضعیت ردیابی" value={item.status === "active" ? "در جریان" : "پایان‌یافته"} />
              {best ? (
                <>
                  <KeyValue label="امتیاز کیفیت تصویر" value={`${faNumber(best.quality_score * 100, 1)}٪`} />
                  <KeyValue label="تیزی تصویر" value={faNumber(best.sharpness, 3)} />
                  <KeyValue label="روشنایی" value={faNumber(best.brightness, 3)} />
                  <KeyValue
                    label="ابعاد فریم"
                    value={`${toFaDigits(best.frame_width)}×${toFaDigits(best.frame_height)}`}
                  />
                  <KeyValue
                    label="کادر شیء (نرمال‌شده)"
                    value={
                      best.bbox.length === 4
                        ? best.bbox.map((value) => faNumber(value, 3)).join(", ")
                        : "—"
                    }
                  />
                </>
              ) : null}
            </CardBody>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader
          title="همه تصاویر ثبت‌شده این شیء"
          subtitle="شامل لحظه ورود، خروج، توقف و بهترین تصویر"
          icon={<Clock />}
          actions={<Badge tone="muted">{toFaDigits(images.length)} تصویر</Badge>}
        />
        <CardBody>
          {images.length === 0 ? (
            <EmptyState icon={<Images />} title="تصویری ثبت نشده است" />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {images.map((image) => (
                <div key={image.id} className="panel overflow-hidden">
                  <button
                    type="button"
                    onClick={() =>
                      setLightbox({ url: image.full_url ?? image.crop_url ?? "", label: eventTypeLabel(image.kind) })
                    }
                    className="block w-full bg-[#050a14]"
                  >
                    <img
                      src={image.thumb_url ?? image.full_url ?? image.crop_url ?? ""}
                      alt={eventTypeLabel(image.kind)}
                      className="h-36 w-full object-cover"
                      loading="lazy"
                    />
                  </button>
                  <div className="flex items-center justify-between gap-2 px-3 py-2">
                    <div className="min-w-0">
                      <Badge tone={image.is_best ? "brand" : "muted"}>
                        {image.is_best ? "بهترین تصویر" : eventTypeLabel(image.kind)}
                      </Badge>
                      <p className="mt-1 truncate text-[10px] text-muted">
                        {formatJalaliDateTime(image.captured_at)}
                      </p>
                    </div>
                    <span className="num shrink-0 text-[10px] text-muted">
                      {faNumber(image.quality_score * 100, 0)}٪
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      <Dialog open={lightbox !== null} onClose={() => setLightbox(null)} title={lightbox?.label ?? "تصویر"} size="xl">
        {lightbox?.url ? (
          <img src={lightbox.url} alt={lightbox.label} className="mx-auto max-h-[70vh] object-contain" />
        ) : null}
      </Dialog>

      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="حذف شیء"
        message="تصاویر ذخیره‌شده این شیء از دیسک و پایگاه داده حذف می‌شوند. این عمل قابل بازگشت نیست."
        confirmLabel="حذف"
        loading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync(item.id);
            toast.success("شیء حذف شد");
            navigate("/objects");
          } catch (error) {
            toast.error("حذف ناموفق بود", (error as Error).message);
          }
        }}
      />
    </div>
  );
}
