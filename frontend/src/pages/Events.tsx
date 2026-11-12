import {
  Activity,
  CheckCheck,
  Eye,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Trash,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DateRangeBar } from "../components/DateRangeBar";
import { MultiChipGroup } from "../components/MultiChipGroup";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { ConfirmDialog, Sheet } from "../components/ui/dialog";
import { Field, Input, Select } from "../components/ui/field";
import { EmptyState, KeyValue, LoadingBlock } from "../components/ui/misc";
import { Pager, TableWrap, Tbody, Td, Th, Thead, Tr } from "../components/ui/table";
import { toast } from "../components/ui/toast";
import type { EventItem } from "../lib/api";
import {
  useAcknowledgeAllEvents,
  useAcknowledgeEvent,
  useCameras,
  useDeleteEvent,
  useEventSummary,
  useEvents,
} from "../lib/queries";
import { formatJalaliDateTime } from "../lib/jalali";
import {
  DIRECTION_ARROWS,
  classLabel,
  directionLabel,
  eventTypeLabel,
  formatRelative,
  severityTone,
  toFaDigits,
} from "../lib/utils";
import { useLive } from "../store/live";
import { useDateRange } from "../lib/useDateRange";

const EVENT_TYPES = ["enter", "exit", "stationary", "moving", "line_cross", "motion", "alert", "detection"];
const SEVERITIES = ["info", "warning", "critical"];

export function EventsPage() {
  const range = useDateRange("today");
  const [cameraIds, setCameraIds] = useState<number[]>([]);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [severities, setSeverities] = useState<string[]>([]);
  const [acknowledged, setAcknowledged] = useState<"all" | "yes" | "no">("all");
  const [search, setSearch] = useState("");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selected, setSelected] = useState<EventItem | null>(null);
  const [pendingDelete, setPendingDelete] = useState<EventItem | null>(null);

  const cameras = useCameras();
  const connection = useLive((state) => state.connection);

  const params = useMemo(
    () => ({
      start: range.start,
      end: range.end,
      camera_id: cameraIds.length > 0 ? cameraIds : undefined,
      event_type: eventTypes.length > 0 ? eventTypes : undefined,
      severity: severities.length > 0 ? severities : undefined,
      acknowledged: acknowledged === "all" ? undefined : acknowledged === "yes",
      search: search.trim() || undefined,
      order,
      page,
      page_size: pageSize,
    }),
    [range.start, range.end, cameraIds, eventTypes, severities, acknowledged, search, order, page, pageSize],
  );

  const events = useEvents(params, 20_000);
  const summary = useEventSummary({ start: range.start, end: range.end, camera_id: cameraIds });
  const acknowledge = useAcknowledgeEvent();
  const acknowledgeAll = useAcknowledgeAllEvents();
  const remove = useDeleteEvent();

  const cameraNames = useMemo(
    () => new Map((cameras.data ?? []).map((camera) => [camera.id, camera.name])),
    [cameras.data],
  );

  const summaryChips = useMemo(
    () =>
      EVENT_TYPES.map((type) => ({
        type,
        label: eventTypeLabel(type),
        count: summary.data?.by_type?.[type] ?? 0,
      })).filter((item) => item.count > 0),
    [summary.data],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="رویدادها"
        subtitle="بخش ویژه نمایش رویدادهای ثبت‌شده: ورود و خروج، توقف، عبور از خط و هشدارها"
        icon={<Activity />}
        actions={
          <>
            <Badge tone={connection === "open" ? "ok" : "muted"}>
              <span className="num">{toFaDigits(summary.data?.unacknowledged ?? 0)}</span> تأییدنشده
            </Badge>
            <Button variant="outline" size="sm" onClick={() => void events.refetch()} loading={events.isFetching}>
              <RefreshCw />
              به‌روزرسانی
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={acknowledgeAll.isPending}
              onClick={async () => {
                const result = await acknowledgeAll.mutateAsync();
                toast.success(result.detail);
              }}
            >
              <CheckCheck />
              تأیید همه
            </Button>
          </>
        }
      />

      {summaryChips.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {summaryChips.map((item) => (
            <Badge key={item.type} tone="muted">
              {item.label}
              <span className="num">{toFaDigits(item.count)}</span>
            </Badge>
          ))}
        </div>
      ) : null}

      <Card>
        <CardHeader
          title="فیلترها"
          subtitle="بازه زمانی، دوربین، نوع رویداد و وضعیت تأیید"
          icon={<SlidersHorizontal />}
        />
        <CardBody className="space-y-4">
          <DateRangeBar range={range} />

          <div className="grid gap-4 lg:grid-cols-3">
            <MultiChipGroup
              label="نوع رویداد"
              options={EVENT_TYPES.map((type) => ({ value: type, label: eventTypeLabel(type) }))}
              selected={eventTypes}
              onChange={(values) => {
                setEventTypes(values);
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
            <MultiChipGroup
              label="شدت"
              options={SEVERITIES.map((severity) => ({
                value: severity,
                label: severity === "critical" ? "بحرانی" : severity === "warning" ? "هشدار" : "اطلاع",
              }))}
              selected={severities}
              onChange={(values) => {
                setSeverities(values);
                setPage(1);
              }}
            />
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <Field label="وضعیت تأیید">
              <Select
                value={acknowledged}
                onChange={(event) => {
                  setAcknowledged(event.target.value as "all" | "yes" | "no");
                  setPage(1);
                }}
              >
                <option value="all">همه</option>
                <option value="no">تأییدنشده</option>
                <option value="yes">تأییدشده</option>
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
            <Field label="جست‌وجو در متن رویداد">
              <div className="relative">
                <Search className="absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
                <Input
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                  placeholder="مثلاً عبور از خط"
                  className="pr-9"
                />
              </div>
            </Field>
          </div>
        </CardBody>
      </Card>

      <Card>
        {events.isLoading ? (
          <LoadingBlock />
        ) : (events.data?.items ?? []).length === 0 ? (
          <EmptyState
            icon={<Activity />}
            title="رویدادی در این بازه یافت نشد"
            description="بازه زمانی یا فیلترها را تغییر دهید. رویدادها با فعال بودن تشخیص اشیا ثبت می‌شوند."
          />
        ) : (
          <>
            <TableWrap>
              <Thead>
                <tr>
                  <Th>تصویر</Th>
                  <Th>نوع رویداد</Th>
                  <Th>دوربین</Th>
                  <Th>شیء</Th>
                  <Th>جهت</Th>
                  <Th>شدت</Th>
                  <Th>زمان (شمسی)</Th>
                  <Th>پیام</Th>
                  <Th>وضعیت</Th>
                  <Th>عملیات</Th>
                </tr>
              </Thead>
              <Tbody>
                {(events.data?.items ?? []).map((event) => (
                  <Tr key={event.id}>
                    <Td>
                      <button
                        type="button"
                        onClick={() => setSelected(event)}
                        className="block size-14 overflow-hidden rounded-lg border border-border bg-[#050a14]"
                      >
                        {event.snapshot_url || event.crop_url ? (
                          <img
                            src={event.crop_url ?? event.snapshot_url ?? ""}
                            alt={eventTypeLabel(event.event_type)}
                            className="h-full w-full object-cover"
                            loading="lazy"
                            decoding="async"
                          />
                        ) : (
                          <span className="grid h-full place-items-center text-[11px] text-muted">—</span>
                        )}
                      </button>
                    </Td>
                    <Td>
                      <Badge tone={event.event_type === "line_cross" ? "warn" : "brand"}>
                        {eventTypeLabel(event.event_type)}
                      </Badge>
                    </Td>
                    <Td>
                      <Link to={`/cameras/${event.camera_id}`} className="hover:text-brand">
                        {cameraNames.get(event.camera_id) ?? `#${event.camera_id}`}
                      </Link>
                    </Td>
                    <Td>{event.object_type ? classLabel(event.object_type) : "—"}</Td>
                    <Td>
                      {event.direction ? (
                        <span>
                          {DIRECTION_ARROWS[event.direction] ?? "•"} {directionLabel(event.direction)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </Td>
                    <Td>
                      <Badge tone={severityTone(event.severity)}>
                        {event.severity === "critical"
                          ? "بحرانی"
                          : event.severity === "warning"
                            ? "هشدار"
                            : "اطلاع"}
                      </Badge>
                    </Td>
                    <Td className="whitespace-nowrap">{formatJalaliDateTime(event.occurred_at)}</Td>
                    <Td className="max-w-[260px] truncate" title={event.message}>
                      {event.message}
                    </Td>
                    <Td>
                      {event.acknowledged ? (
                        <Badge tone="ok">تأییدشده</Badge>
                      ) : (
                        <Badge tone="warn">تأییدنشده</Badge>
                      )}
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          title="مشاهده جزئیات"
                          onClick={() => setSelected(event)}
                          className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-3 hover:text-fg"
                        >
                          <Eye className="size-3.5" />
                        </button>
                        {!event.acknowledged ? (
                          <button
                            type="button"
                            title="تأیید رویداد"
                            onClick={() => acknowledge.mutate(event.id)}
                            className="grid size-7 place-items-center rounded-lg text-muted hover:bg-ok/15 hover:text-ok"
                          >
                            <CheckCheck className="size-3.5" />
                          </button>
                        ) : null}
                        <button
                          type="button"
                          title="حذف رویداد"
                          onClick={() => setPendingDelete(event)}
                          className="grid size-7 place-items-center rounded-lg text-muted hover:bg-danger/15 hover:text-danger"
                        >
                          <Trash className="size-3.5" />
                        </button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </TableWrap>

            <Pager
              page={events.data?.page ?? page}
              pageSize={events.data?.page_size ?? pageSize}
              total={events.data?.total ?? 0}
              onPage={setPage}
              onPageSize={(size) => {
                setPageSize(size);
                setPage(1);
              }}
            />
          </>
        )}
      </Card>

      <Sheet
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `رویداد ${eventTypeLabel(selected.event_type)}` : "رویداد"}
        footer={
          selected ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-muted">{formatJalaliDateTime(selected.occurred_at)}</span>
              {!selected.acknowledged ? (
                <Button
                  size="sm"
                  variant="primary"
                  onClick={async () => {
                    await acknowledge.mutateAsync(selected.id);
                    toast.success("رویداد تأیید شد");
                    setSelected(null);
                  }}
                >
                  <CheckCheck />
                  تأیید رویداد
                </Button>
              ) : (
                <Badge tone="ok">تأییدشده</Badge>
              )}
            </div>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="mb-1.5 text-[11px] text-muted">تصویر کامل صحنه</p>
                {selected.snapshot_url ? (
                  <img
                    src={selected.snapshot_url}
                    alt="تصویر رویداد"
                    className="w-full rounded-xl border border-border bg-[#050a14] object-contain"
                  />
                ) : (
                  <EmptyState icon={<TriangleAlert />} title="تصویری ذخیره نشده" />
                )}
              </div>
              <div>
                <p className="mb-1.5 text-[11px] text-muted">تصویر برش‌خورده شیء</p>
                {selected.crop_url ? (
                  <img
                    src={selected.crop_url}
                    alt="برش رویداد"
                    className="w-full rounded-xl border border-border bg-[#050a14] object-contain"
                  />
                ) : (
                  <EmptyState icon={<TriangleAlert />} title="برش تصویر ذخیره نشده" />
                )}
              </div>
            </div>

            <div className="panel p-3">
              <KeyValue label="شناسه رویداد" value={toFaDigits(selected.id)} />
              <KeyValue label="دوربین" value={cameraNames.get(selected.camera_id) ?? `#${selected.camera_id}`} />
              <KeyValue label="نوع" value={eventTypeLabel(selected.event_type)} />
              <KeyValue
                label="شیء"
                value={selected.object_type ? classLabel(selected.object_type) : "—"}
              />
              <KeyValue label="اطمینان" value={`${(selected.confidence * 100).toFixed(0)}٪`} />
              <KeyValue
                label="جهت"
                value={selected.direction ? directionLabel(selected.direction) : "—"}
              />
              <KeyValue label="شدت" value={selected.severity} />
              <KeyValue label="زمان (شمسی)" value={formatJalaliDateTime(selected.occurred_at)} />
              <KeyValue label="زمان نسبی" value={formatRelative(selected.occurred_at)} />
              {selected.track_id ? (
                <KeyValue
                  label="شیء مرتبط"
                  value={
                    <Link to={`/objects/${selected.track_id}`} className="text-brand hover:underline">
                      مشاهده شیء #{toFaDigits(selected.track_id)}
                    </Link>
                  }
                />
              ) : null}
              {selected.zone_id ? <KeyValue label="ناحیه" value={`#${toFaDigits(selected.zone_id)}`} /> : null}
            </div>

            <p className="rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[11px] leading-6">
              {selected.message}
            </p>
          </div>
        ) : null}
      </Sheet>

      <ConfirmDialog
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        title="حذف رویداد"
        message="این رویداد از دفتر رویدادها حذف می‌شود. تصاویر مرتبط با شیء حذف نمی‌شوند."
        confirmLabel="حذف"
        loading={remove.isPending}
        onConfirm={async () => {
          if (!pendingDelete) return;
          try {
            await remove.mutateAsync(pendingDelete.id);
            toast.success("رویداد حذف شد");
            setPendingDelete(null);
          } catch (error) {
            toast.error("حذف ناموفق بود", (error as Error).message);
          }
        }}
      />
    </div>
  );
}
