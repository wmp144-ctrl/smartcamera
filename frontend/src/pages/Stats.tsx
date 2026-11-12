import { ChartColumn, Clock, Flame, MapPin, Route, ScanEye, Trophy } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DateRangeBar } from "../components/DateRangeBar";
import { HeatmapCanvas } from "../components/HeatmapCanvas";
import { Badge, StatusDot } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { Field, Input, Select } from "../components/ui/field";
import { EmptyState, LoadingBlock, Metric } from "../components/ui/misc";
import { TableWrap, Tbody, Td, Th, Thead, Tr } from "../components/ui/table";
import { formatClock, formatJalaliDate } from "../lib/jalali";
import {
  useActivity,
  useBusiestHours,
  useCameras,
  useCameraStats,
  useOverview,
  useStatCounts,
  useTopZones,
} from "../lib/queries";
import { useDateRange } from "../lib/useDateRange";
import { classLabel, directionLabel, eventTypeLabel, faNumber, toFaDigits } from "../lib/utils";

const COLORS = ["#22d3ee", "#6366f1", "#f59e0b", "#22c55e", "#f43f5e", "#38bdf8", "#a855f7", "#14b8a6"];

export function StatsPage() {
  const range = useDateRange("last7");
  const [hourly, setHourly] = useState(true);
  const [heatmapCamera, setHeatmapCamera] = useState<number | null>(null);
  const [heatmapType, setHeatmapType] = useState("");
  const [daysBack, setDaysBack] = useState(0);

  const cameras = useCameras();
  const overview = useOverview(14, 30_000);
  const cameraStats = useCameraStats(7);
  const activity = useActivity({ start: range.start, end: range.end, hourly }, 30_000);
  const objects = useStatCounts("objects", { start: range.start, end: range.end });
  const directions = useStatCounts("directions", { start: range.start, end: range.end });
  const eventTypes = useStatCounts("event-types", { start: range.start, end: range.end });
  const busiest = useBusiestHours(7);
  const zones = useTopZones(8);

  const effectiveHeatmapCamera = heatmapCamera ?? cameras.data?.[0]?.id ?? null;

  const heatmapAspect = useMemo(() => {
    const camera = cameras.data?.find((item) => item.id === effectiveHeatmapCamera);
    if (camera?.width && camera.height) return camera.width / camera.height;
    return 16 / 9;
  }, [cameras.data, effectiveHeatmapCamera]);

  const activitySeries = useMemo(
    () =>
      (activity.data ?? []).map((bucket) => ({
        label: hourly ? formatClock(bucket.bucket) : formatJalaliDate(bucket.bucket),
        count: bucket.count,
      })),
    [activity.data, hourly],
  );

  const typeSeries = useMemo(
    () => (objects.data ?? []).filter((item) => item.key).map((item) => ({ key: item.key, count: item.count })),
    [objects.data],
  );

  const directionSeries = useMemo(
    () =>
      (directions.data ?? [])
        .filter((item) => item.key)
        .map((item) => ({ name: directionLabel(item.key), icon: item.key, count: item.count })),
    [directions.data],
  );

  const eventSeries = useMemo(
    () =>
      (eventTypes.data ?? []).filter((item) => item.key).map((item, index) => ({
        name: eventTypeLabel(item.key),
        value: item.count,
        color: COLORS[index % COLORS.length],
      })),
    [eventTypes.data],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="آمار و نقشه حرارتی"
        subtitle="آمار ثبت اشیا، توزیع مکانی و پرتراکم‌ترین ساعات"
        icon={<ChartColumn />}
      />

      <Card>
        <CardBody className="flex flex-wrap items-center justify-between gap-3">
          <DateRangeBar range={range} />
          <div className="flex items-center gap-2">
            <Button size="sm" variant={hourly ? "primary" : "outline"} onClick={() => setHourly(true)}>
              نمای ساعتی
            </Button>
            <Button size="sm" variant={!hourly ? "primary" : "outline"} onClick={() => setHourly(false)}>
              نمای روزانه
            </Button>
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="مجموع اشیای ثبت‌شده"
          value={toFaDigits(overview.data?.total_tracks ?? 0)}
          icon={<ScanEye />}
          tone="brand"
          hint={`امروز: ${toFaDigits(overview.data?.objects_today ?? 0)}`}
        />
        <Metric
          label="مجموع رویدادها"
          value={toFaDigits(overview.data?.total_events ?? 0)}
          icon={<Clock />}
          hint={`امروز: ${toFaDigits(overview.data?.events_today ?? 0)}`}
        />
        <Metric
          label="مجموع هشدارها"
          value={toFaDigits(overview.data?.total_alerts ?? 0)}
          icon={<Flame />}
          tone={(overview.data?.unacknowledged_alerts ?? 0) > 0 ? "warn" : "default"}
          hint={`تأییدنشده: ${toFaDigits(overview.data?.unacknowledged_alerts ?? 0)}`}
        />
        <Metric
          label="دوربین‌های فعال"
          value={`${toFaDigits(overview.data?.online_cameras ?? 0)} / ${toFaDigits(
            overview.data?.total_cameras ?? 0,
          )}`}
          icon={<MapPin />}
          tone="ok"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title={hourly ? "فعالیت ساعتی" : "فعالیت روزانه"}
            subtitle="تعداد اشیای ثبت‌شده در بازه انتخابی"
            icon={<ChartColumn />}
          />
          <CardBody>
            {activity.isLoading ? (
              <LoadingBlock />
            ) : activitySeries.length === 0 ? (
              <EmptyState icon={<ChartColumn />} title="داده‌ای برای این بازه نیست" />
            ) : (
              <div className="h-72 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={activitySeries}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--border-strong)" allowDecimals={false} />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                    <Bar dataKey="count" name="تعداد اشیا" fill="#22d3ee" radius={[5, 5, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="ترکیب کلاس‌ها" icon={<ScanEye />} />
          <CardBody>
            {typeSeries.length === 0 ? (
              <EmptyState icon={<ScanEye />} title="داده‌ای موجود نیست" />
            ) : (
              <>
                <div className="h-56 w-full" dir="ltr">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={typeSeries.map((item, index) => ({
                          name: classLabel(item.key),
                          value: item.count,
                          color: COLORS[index % COLORS.length],
                        }))}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={46}
                        outerRadius={78}
                        paddingAngle={3}
                      >
                        {typeSeries.map((item, index) => (
                          <Cell key={item.key} fill={COLORS[index % COLORS.length]} stroke="transparent" />
                        ))}
                      </Pie>
                      <ReTooltip
                        contentStyle={{
                          background: "var(--surface-2)",
                          border: "1px solid var(--border)",
                          borderRadius: 12,
                          fontSize: 11,
                          direction: "rtl",
                        }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {typeSeries.slice(0, 8).map((item, index) => (
                    <span key={item.key} className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                      <span className="size-2 rounded-full" style={{ background: COLORS[index % COLORS.length] }} />
                      {classLabel(item.key)}
                      <span className="num">{toFaDigits(item.count)}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="جهت حرکت" icon={<Route />} />
          <CardBody>
            {directionSeries.length === 0 ? (
              <EmptyState icon={<Route />} title="داده‌ای موجود نیست" />
            ) : (
              <div className="h-64 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={directionSeries} layout="vertical" margin={{ left: 8, right: 12 }}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 10 }} stroke="var(--border-strong)" allowDecimals={false} />
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={70} stroke="var(--border-strong)" />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                    <Bar dataKey="count" name="تعداد" fill="#6366f1" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="انواع رویداد" icon={<Flame />} />
          <CardBody>
            {eventSeries.length === 0 ? (
              <EmptyState icon={<Flame />} title="داده‌ای موجود نیست" />
            ) : (
              <div className="h-64 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={eventSeries} dataKey="value" nameKey="name" outerRadius={84} innerRadius={40}>
                      {eventSeries.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} stroke="transparent" />
                      ))}
                    </Pie>
                    <Legend
                      formatter={(value) => <span style={{ fontSize: 10, color: "var(--muted)" }}>{value}</span>}
                    />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="پرتراکم‌ترین ساعات" subtitle="۷ روز گذشته" icon={<Clock />} />
          <CardBody className="space-y-2">
            {(busiest.data ?? []).filter((item) => item.key).length === 0 ? (
              <EmptyState icon={<Clock />} title="داده‌ای موجود نیست" />
            ) : (
              (busiest.data ?? [])
                .filter((item) => item.key)
                .map((item) => {
                  const max = Math.max(...(busiest.data ?? []).map((row) => row.count), 1);
                  return (
                    <div key={item.key} className="flex items-center gap-3">
                      <span className="num w-12 shrink-0 text-[11px] text-muted">
                        {item.key.length <= 2 ? `${toFaDigits(item.key)}:۰۰` : toFaDigits(item.key)}
                      </span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-3">
                        <div
                          className="h-full rounded-full bg-brand"
                          style={{ width: `${(item.count / max) * 100}%` }}
                        />
                      </div>
                      <span className="num w-10 shrink-0 text-left text-[11px] text-muted">
                        {toFaDigits(item.count)}
                      </span>
                    </div>
                  );
                })
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader
            title="نقشه حرارتی توزیع اشیا"
            subtitle="تراکم ثبت اشیا در نواحی مختلف تصویر دوربین"
            icon={<MapPin />}
          />
          <CardBody className="space-y-4">
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="دوربین">
                <Select
                  value={effectiveHeatmapCamera ?? ""}
                  onChange={(event) => setHeatmapCamera(Number(event.target.value))}
                >
                  {(cameras.data ?? []).map((camera) => (
                    <option key={camera.id} value={camera.id}>
                      {camera.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="نوع شیء">
                <Select value={heatmapType} onChange={(event) => setHeatmapType(event.target.value)}>
                  <option value="">همه کلاس‌ها</option>
                  {(objects.data ?? [])
                    .filter((item) => item.key)
                    .map((item) => (
                      <option key={item.key} value={item.key}>
                        {classLabel(item.key)}
                      </option>
                    ))}
                </Select>
              </Field>
              <Field label="بازه چند روز قبل" hint="۰ = امروز">
                <Input
                  type="number"
                  min={0}
                  max={365}
                  value={daysBack}
                  onChange={(event) => setDaysBack(Number(event.target.value))}
                />
              </Field>
            </div>

            {effectiveHeatmapCamera ? (
              <HeatmapCanvas
                cameraId={effectiveHeatmapCamera}
                objectType={heatmapType || undefined}
                daysBack={daysBack}
                aspectRatio={heatmapAspect}
              />
            ) : (
              <EmptyState icon={<MapPin />} title="برای نمایش نقشه حرارتی، دوربین انتخاب کنید" />
            )}
          </CardBody>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="پرتراکم‌ترین نواحی" icon={<Trophy />} />
            <CardBody className="p-0">
              {(zones.data ?? []).length === 0 ? (
                <EmptyState icon={<Route />} title="ناحیه‌ای ثبت نشده" />
              ) : (
                <TableWrap>
                  <Thead>
                    <tr>
                      <Th>ناحیه</Th>
                      <Th>دوربین</Th>
                      <Th>تعداد</Th>
                    </tr>
                  </Thead>
                  <Tbody>
                    {(zones.data ?? []).map((zone) => (
                      <Tr key={zone.key}>
                        <Td>{zone.key}</Td>
                        <Td className="text-muted">
                          {(zone.extra?.camera_name as string | undefined) ??
                            (zone.extra?.camera_id
                              ? `#${toFaDigits(zone.extra.camera_id as number)}`
                              : "—")}
                        </Td>
                        <Td>
                          <Badge tone="brand">
                            <span className="num">{toFaDigits(zone.count)}</span>
                          </Badge>
                        </Td>
                      </Tr>
                    ))}
                  </Tbody>
                </TableWrap>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="روند روزانه" subtitle="۱۴ روز گذشته" icon={<ChartColumn />} />
            <CardBody>
              {(overview.data?.daily ?? []).length === 0 ? (
                <EmptyState icon={<ChartColumn />} title="داده‌ای موجود نیست" />
              ) : (
                <div className="h-48 w-full" dir="ltr">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={(overview.data?.daily ?? []).map((bucket) => ({
                        label: formatJalaliDate(bucket.bucket),
                        count: bucket.count,
                      }))}
                    >
                      <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="label" tick={{ fontSize: 9 }} stroke="var(--border-strong)" />
                      <YAxis tick={{ fontSize: 10 }} stroke="var(--border-strong)" allowDecimals={false} />
                      <ReTooltip
                        contentStyle={{
                          background: "var(--surface-2)",
                          border: "1px solid var(--border)",
                          borderRadius: 12,
                          fontSize: 11,
                          direction: "rtl",
                        }}
                      />
                      <Line type="monotone" dataKey="count" name="تعداد" stroke="#22c55e" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader title="آمار به تفکیک دوربین" subtitle="۷ روز گذشته" icon={<ScanEye />} />
        <CardBody className="p-0">
          {cameraStats.isLoading ? (
            <LoadingBlock />
          ) : (cameraStats.data ?? []).length === 0 ? (
            <EmptyState icon={<ScanEye />} title="داده‌ای موجود نیست" />
          ) : (
            <TableWrap>
              <Thead>
                <tr>
                  <Th>دوربین</Th>
                  <Th>وضعیت</Th>
                  <Th>نرخ فریم</Th>
                  <Th>تعداد اشیا</Th>
                  <Th>رویدادها</Th>
                  <Th>هشدارها</Th>
                  <Th>پرتراکم‌ترین کلاس</Th>
                </tr>
              </Thead>
              <Tbody>
                {(cameraStats.data ?? []).map((stats) => (
                  <Tr key={stats.camera_id}>
                    <Td>{stats.camera_name}</Td>
                    <Td>
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot tone={stats.state === "online" ? "ok" : "muted"} />
                        {stats.state === "online" ? "آنلاین" : stats.state}
                      </span>
                    </Td>
                    <Td className="num">{faNumber(stats.fps, 1)}</Td>
                    <Td className="num">{toFaDigits(stats.total_tracks)}</Td>
                    <Td className="num">{toFaDigits(stats.total_events)}</Td>
                    <Td className="num">{toFaDigits(stats.total_alerts)}</Td>
                    <Td>
                      {stats.by_type.length > 0 ? (
                        <span className="inline-flex items-center gap-1.5">
                          {classLabel(stats.by_type[0].key)}
                          <Badge tone="muted">
                            <span className="num">{toFaDigits(stats.by_type[0].count)}</span>
                          </Badge>
                        </span>
                      ) : (
                        "—"
                      )}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </TableWrap>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
