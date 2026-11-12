import { ArrowUpDown, Images, LayoutGrid, Search, SlidersHorizontal, X } from "lucide-react";
import { useMemo, useState } from "react";
import { DateRangeBar } from "../components/DateRangeBar";
import { MultiChipGroup } from "../components/MultiChipGroup";
import { ObjectCard } from "../components/ObjectCard";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { Field, Input, Select } from "../components/ui/field";
import { EmptyState, LoadingBlock, Slider } from "../components/ui/misc";
import { Pager } from "../components/ui/table";
import { useCameras, useObjectFilters, useObjects } from "../lib/queries";
import { useDateRange } from "../lib/useDateRange";
import { classLabel, faNumber, toFaDigits } from "../lib/utils";

type MotionFilter = "all" | "moving" | "stationary";

export function ObjectsPage() {
  const range = useDateRange("today");
  const [cameraIds, setCameraIds] = useState<number[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [directions, setDirections] = useState<string[]>([]);
  const [motion, setMotion] = useState<MotionFilter>("all");
  const [minConfidence, setMinConfidence] = useState(0);
  const [minQuality, setMinQuality] = useState(0);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(40);
  const [search, setSearch] = useState("");
  const [showFilters, setShowFilters] = useState(true);

  const filters = useObjectFilters();
  const cameras = useCameras();

  const params = useMemo(
    () => ({
      start: range.start,
      end: range.end,
      camera_id: cameraIds.length > 0 ? cameraIds : undefined,
      object_type: types.length > 0 ? types : undefined,
      direction: directions.length > 0 ? directions : undefined,
      motion_state: motion === "all" ? undefined : motion,
      min_confidence: minConfidence > 0 ? minConfidence : undefined,
      min_quality: minQuality > 0 ? minQuality : undefined,
      order,
      page,
      page_size: pageSize,
    }),
    [range.start, range.end, cameraIds, types, directions, motion, minConfidence, minQuality, order, page, pageSize],
  );

  const objects = useObjects(params);

  const cameraNames = useMemo(
    () => new Map((cameras.data ?? []).map((camera) => [camera.id, camera.name])),
    [cameras.data],
  );

  const items = useMemo(() => {
    const list = objects.data?.items ?? [];
    if (!search.trim()) return list;
    const needle = search.trim().toLowerCase();
    return list.filter(
      (track) =>
        track.object_type.toLowerCase().includes(needle) ||
        classLabel(track.object_type).includes(needle) ||
        (cameraNames.get(track.camera_id) ?? "").toLowerCase().includes(needle),
    );
  }, [objects.data, search, cameraNames]);

  const activeFilterCount =
    (cameraIds.length > 0 ? 1 : 0) +
    (types.length > 0 ? 1 : 0) +
    (directions.length > 0 ? 1 : 0) +
    (motion !== "all" ? 1 : 0) +
    (minConfidence > 0 ? 1 : 0) +
    (minQuality > 0 ? 1 : 0);

  const resetFilters = () => {
    setCameraIds([]);
    setTypes([]);
    setDirections([]);
    setMotion("all");
    setMinConfidence(0);
    setMinQuality(0);
    setSearch("");
    setPage(1);
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="اشیای ثبت‌شده"
        subtitle="گالری اشیای شناسایی‌شده — تصویر کامل صحنه و نسخه برش‌خورده شیء در کنار هم"
        icon={<Images />}
        actions={
          <>
            <span className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[11px] text-muted">
              <LayoutGrid className="size-3.5 text-brand" />
              <span className="num">{toFaDigits(objects.data?.total ?? 0)}</span> شیء
            </span>
            <Button variant="outline" size="sm" onClick={() => setShowFilters((value) => !value)}>
              <SlidersHorizontal />
              {showFilters ? "مخفی کردن فیلترها" : "نمایش فیلترها"}
              {activeFilterCount > 0 ? <Badge tone="brand">{toFaDigits(activeFilterCount)}</Badge> : null}
            </Button>
          </>
        }
      />

      <Card>
        <CardBody className="space-y-4">
          <DateRangeBar range={range} />

          {showFilters ? (
            <div className="grid gap-4 border-t border-border/70 pt-4 lg:grid-cols-2">
              <MultiChipGroup
                label="کلاس شیء"
                options={(filters.data?.object_types ?? [])
                  .filter((item) => item.value !== "*")
                  .map((item) => ({ value: item.value, label: item.label, count: item.count }))}
                selected={types}
                onChange={(values) => {
                  setTypes(values);
                  setPage(1);
                }}
                limit={18}
              />
              <MultiChipGroup
                label="جهت حرکت"
                options={(filters.data?.directions ?? []).map((item) => ({
                  value: item.value,
                  label: item.label,
                  count: item.count,
                }))}
                selected={directions}
                onChange={(values) => {
                  setDirections(values);
                  setPage(1);
                }}
              />
              <MultiChipGroup
                label="دوربین"
                options={(cameras.data ?? []).map((camera) => ({ value: camera.id, label: camera.name }))}
                selected={cameraIds}
                onChange={(values) => {
                  setCameraIds(values);
                  setPage(1);
                }}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="وضعیت حرکت">
                  <Select
                    value={motion}
                    onChange={(event) => {
                      setMotion(event.target.value as MotionFilter);
                      setPage(1);
                    }}
                  >
                    <option value="all">همه</option>
                    <option value="moving">متحرک</option>
                    <option value="stationary">ثابت</option>
                  </Select>
                </Field>
                <Field label="ترتیب">
                  <Select
                    value={order}
                    onChange={(event) => {
                      setOrder(event.target.value as "asc" | "desc");
                      setPage(1);
                    }}
                  >
                    <option value="desc">جدیدترین ابتدا</option>
                    <option value="asc">قدیمی‌ترین ابتدا</option>
                  </Select>
                </Field>
                <Slider
                  label="حداقل اطمینان"
                  value={minConfidence}
                  onChange={(value) => {
                    setMinConfidence(value);
                    setPage(1);
                  }}
                  min={0}
                  max={0.95}
                  step={0.05}
                  format={(value) => `${faNumber(value * 100, 0)}٪`}
                />
                <Slider
                  label="حداقل کیفیت تصویر"
                  value={minQuality}
                  onChange={(value) => {
                    setMinQuality(value);
                    setPage(1);
                  }}
                  min={0}
                  max={0.95}
                  step={0.05}
                  format={(value) => `${faNumber(value * 100, 0)}٪`}
                />
              </div>

              {activeFilterCount > 0 ? (
                <div className="lg:col-span-2">
                  <Button variant="ghost" size="sm" onClick={resetFilters}>
                    <X />
                    پاک کردن همه فیلترها
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="relative max-w-sm">
            <Search className="absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="جست‌وجو در نتایج این صفحه…"
              className="pr-9"
            />
          </div>
        </CardBody>
      </Card>

      {objects.isLoading ? (
        <LoadingBlock />
      ) : objects.isError ? (
        <Card>
          <EmptyState
            icon={<ArrowUpDown />}
            title="دریافت اشیا ناموفق بود"
            description={(objects.error as Error).message}
            action={
              <Button size="sm" variant="outline" onClick={() => void objects.refetch()}>
                تلاش دوباره
              </Button>
            }
          />
        </Card>
      ) : (objects.data?.items ?? []).length === 0 ? (
        <Card>
          <EmptyState
            icon={<Images />}
            title="شیئی برای این بازه یافت نشد"
            description="بازه زمانی یا فیلترها را تغییر دهید، یا تشخیص اشیا را برای دوربین‌ها فعال کنید."
            action={
              activeFilterCount > 0 ? (
                <Button size="sm" variant="outline" onClick={resetFilters}>
                  پاک کردن فیلترها
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {items.map((track) => (
              <ObjectCard key={track.id} track={track} cameraName={cameraNames.get(track.camera_id)} />
            ))}
          </div>

          <Card>
            <Pager
              page={objects.data?.page ?? page}
              pageSize={objects.data?.page_size ?? pageSize}
              total={objects.data?.total ?? 0}
              onPage={setPage}
              onPageSize={(size) => {
                setPageSize(size);
                setPage(1);
              }}
            />
          </Card>

          <Card>
            <CardHeader
              title="راهنمای نمایش"
              subtitle="هر کارت شامل تصویر کامل صحنه و تصویر برش‌خورده شیء است"
              icon={<SlidersHorizontal />}
            />
            <CardBody className="grid gap-2 text-[11px] leading-6 text-muted md:grid-cols-3">
              <p>• «کامل» = فریم اصلی دوربین در لحظه ثبت، با حفظ کل صحنه.</p>
              <p>• «برش» = ناحیه محدود به شیء شناسایی‌شده، برای بررسی دقیق‌تر.</p>
              <p>• ترتیب پیش‌فرض بر اساس زمان شروع ردیابی است و جهت حرکت با نمادهای ← → ↑ ↓ نمایش داده می‌شود.</p>
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}
