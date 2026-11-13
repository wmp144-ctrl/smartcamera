import { Crosshair, Download, MapPin, Search, SlidersHorizontal, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { ObjectCard } from "../components/ObjectCard";
import { PolygonPicker } from "../components/PolygonPicker";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { Field, Input, Select } from "../components/ui/field";
import { EmptyState, LoadingBlock, Slider } from "../components/ui/misc";
import { MultiChipGroup } from "../components/MultiChipGroup";
import { toast } from "../components/ui/toast";
import { api, downloadBlob, type SpatialSearchRequest } from "../lib/api";
import { useCameras, useObjectFilters, useSpatialSearch } from "../lib/queries";
import { JalaliDatePicker } from "../components/JalaliDatePicker";
import { formatJalaliLong, jalaliToLocalDate, todayJalali, type JalaliDate } from "../lib/jalali";
import { classLabel, faNumber, toFaDigits } from "../lib/utils";

const DIRECTIONS = [
  "stationary",
  "left",
  "right",
  "up",
  "down",
  "up-left",
  "up-right",
  "down-left",
  "down-right",
];

export function SpatialSearchPage() {
  const cameras = useCameras();
  const filters = useObjectFilters();

  const today = useMemo(() => todayJalali(), []);
  const [cameraId, setCameraId] = useState<number | null>(null);
  const [polygon, setPolygon] = useState<[number, number][]>([]);
  const [from, setFrom] = useState<JalaliDate>(today);
  const [to, setTo] = useState<JalaliDate>(today);
  const [fromTime, setFromTime] = useState("00:00");
  const [toTime, setToTime] = useState("23:59");
  const [types, setTypes] = useState<string[]>([]);
  const [directions, setDirections] = useState<string[]>([]);
  const [motion, setMotion] = useState<"any" | "moving" | "stationary">("any");
  const [minConfidence, setMinConfidence] = useState(0);
  const [minQuality, setMinQuality] = useState(0);
  const [limit, setLimit] = useState(200);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [payload, setPayload] = useState<SpatialSearchRequest | null>(null);

  const results = useSpatialSearch(payload);

  const aspectRatio = useMemo(() => {
    const camera = cameras.data?.find((item) => item.id === cameraId);
    if (camera?.width && camera.height) return camera.width / camera.height;
    return 16 / 9;
  }, [cameras.data, cameraId]);

  const buildPayload = (): SpatialSearchRequest | null => {
    if (polygon.length < 3) {
      toast.warn("ناحیه جست‌وجو نامعتبر است", "برای جست‌وجوی مکانی حداقل سه نقطه رسم کنید");
      return null;
    }
    const [fromHour, fromMinute] = fromTime.split(":").map(Number);
    const [toHour, toMinute] = toTime.split(":").map(Number);
    const start = jalaliToLocalDate(from, fromHour || 0, fromMinute || 0).toISOString();
    const end = jalaliToLocalDate(to, toHour || 0, toMinute || 0).toISOString();
    return {
      camera_ids: cameraId !== null ? [cameraId] : undefined,
      polygon,
      start,
      end,
      object_types: types.length > 0 ? types : undefined,
      directions: directions.length > 0 ? directions : undefined,
      motion_state: motion === "any" ? undefined : motion,
      min_confidence: minConfidence > 0 ? minConfidence : undefined,
      min_quality: minQuality > 0 ? minQuality : undefined,
      limit,
      order,
    };
  };

  const handleSearch = () => {
    const next = buildPayload();
    if (!next) return;
    setPayload(next);
  };

  const handleExport = async () => {
    const next = buildPayload();
    if (!next) return;
    try {
      const blob = await api.search.exportCsv({ ...next, limit: Math.min(limit, 2000) });
      downloadBlob(blob, "camerasmart-spatial-search.csv");
      toast.success("خروجی CSV دانلود شد");
    } catch (error) {
      toast.error("خروجی‌گیری ناموفق بود", (error as Error).message);
    }
  };

  const items = results.data?.items ?? [];
  const typeBreakdown = useMemo(() => {
    const map = new Map<string, number>();
    for (const track of items) {
      map.set(track.object_type, (map.get(track.object_type) ?? 0) + 1);
    }
    return Array.from(map.entries())
      .map(([key, count]) => ({ key, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);
  }, [items]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="جست‌وجوی مکانی اشیا"
        subtitle="انتخاب دوربین، رسم ناحیه روی تصویر، تعیین تاریخ و ساعت، نوع شیء و جهت حرکت"
        icon={<Crosshair />}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={handleExport} disabled={polygon.length < 3}>
              <Download />
              خروجی CSV
            </Button>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2.1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader
            title="رسم ناحیه مورد جست‌وجو"
            subtitle="ناحیه روی تصویر دوربین انتخاب‌شده رسم می‌شود"
            icon={<MapPin />}
          />
          <CardBody className="space-y-4">
            <Field label="دوربین (برای رسم ناحیه)">
              <Select
                value={cameraId ?? ""}
                onChange={(event) => {
                  const value = event.target.value;
                  setCameraId(value ? Number(value) : null);
                  setPolygon([]);
                }}
              >
                <option value="">انتخاب دوربین…</option>
                {(cameras.data ?? []).map((camera) => (
                  <option key={camera.id} value={camera.id}>
                    {camera.name}
                    {camera.location ? ` — ${camera.location}` : ""}
                  </option>
                ))}
              </Select>
            </Field>

            <PolygonPicker cameraId={cameraId} aspectRatio={aspectRatio} points={polygon} onChange={setPolygon} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="شرایط جست‌وجو" subtitle="زمان، نوع شیء و جهت حرکت" icon={<SlidersHorizontal />} />
          <CardBody className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="از تاریخ">
                <JalaliDatePicker value={from} onChange={(value) => value && setFrom(value)} clearable={false} />
              </Field>
              <Field label="تا تاریخ">
                <JalaliDatePicker value={to} onChange={(value) => value && setTo(value)} clearable={false} />
              </Field>
              <Field label="از ساعت">
                <Input type="time" value={fromTime} onChange={(event) => setFromTime(event.target.value)} />
              </Field>
              <Field label="تا ساعت">
                <Input type="time" value={toTime} onChange={(event) => setToTime(event.target.value)} />
              </Field>
            </div>

            <div className="rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[11px] leading-6 text-muted">
              بازه انتخابی: {formatJalaliLong(from)} ساعت {fromTime} تا {formatJalaliLong(to)} ساعت {toTime} (به وقت
              محلی)
            </div>

            <MultiChipGroup
              label="نوع شیء"
              options={(filters.data?.object_types ?? [])
                .filter((item) => item.value !== "*")
                .map((item) => ({ value: item.value, label: item.label, count: item.count }))}
              selected={types}
              onChange={setTypes}
              limit={16}
            />

            <MultiChipGroup
              label="جهت حرکت"
              options={DIRECTIONS.map((direction) => ({
                value: direction,
                label: filters.data?.directions_all?.find((item) => item.value === direction)?.label ?? direction,
              }))}
              selected={directions}
              onChange={setDirections}
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="وضعیت حرکت">
                <Select value={motion} onChange={(event) => setMotion(event.target.value as typeof motion)}>
                  <option value="any">همه</option>
                  <option value="moving">متحرک</option>
                  <option value="stationary">ثابت</option>
                </Select>
              </Field>
              <Field label="ترتیب">
                <Select value={order} onChange={(event) => setOrder(event.target.value as "asc" | "desc")}>
                  <option value="desc">جدیدترین ابتدا</option>
                  <option value="asc">قدیمی‌ترین ابتدا</option>
                </Select>
              </Field>
              <Field label="حداکثر نتایج">
                <Input
                  type="number"
                  min={1}
                  max={2000}
                  value={limit}
                  onChange={(event) => setLimit(Number(event.target.value))}
                />
              </Field>
              <Slider
                label="حداقل اطمینان"
                value={minConfidence}
                onChange={setMinConfidence}
                max={0.95}
                step={0.05}
                format={(value) => `${faNumber(value * 100, 0)}٪`}
              />
              <Slider
                label="حداقل کیفیت تصویر"
                value={minQuality}
                onChange={setMinQuality}
                max={0.95}
                step={0.05}
                format={(value) => `${faNumber(value * 100, 0)}٪`}
              />
            </div>

            <Button className="w-full" variant="primary" onClick={handleSearch} loading={results.isFetching}>
              <Search />
              جست‌وجو
            </Button>
          </CardBody>
        </Card>
      </div>

      {payload ? (
        <Card>
          <CardHeader
            title="            نتایج جست‌وجو"
            subtitle={
              results.data
                ? `مجموع ${toFaDigits(results.data.total)} شیء مطابق شرایط — ${items.length} مورد نمایش داده شده`
                : "در حال جست‌وجو…"
            }
            icon={<Sparkles />}
            actions={
              typeBreakdown.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  {typeBreakdown.map((item) => (
                    <Badge key={item.key} tone="muted">
                      {classLabel(item.key)}
                      <span className="num">{toFaDigits(item.count)}</span>
                    </Badge>
                  ))}
                </div>
              ) : null
            }
          />
          <CardBody>
            {results.isLoading ? (
              <LoadingBlock />
            ) : items.length === 0 ? (
              <EmptyState
                icon={<Crosshair />}
                title="شیئی مطابق این ناحیه و شرایط یافت نشد"
                description="ناحیه بزرگ‌تری رسم کنید یا بازه زمانی و فیلترها را گسترده‌تر کنید."
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                {items.map((track) => (
                  <ObjectCard
                    key={track.id}
                    track={track}
                    cameraName={cameras.data?.find((camera) => camera.id === track.camera_id)?.name}
                  />
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      ) : (
        <Card>
          <EmptyState
            icon={<Crosshair />}
            title="ناحیه و شرایط را مشخص کنید"
            description="پس از رسم ناحیه روی تصویر دوربین، دکمه «جست‌وجو» را بزنید تا اشیای ثبت‌شده در آن محدوده و بازه زمانی نمایش داده شوند."
          />
        </Card>
      )}
    </div>
  );
}
