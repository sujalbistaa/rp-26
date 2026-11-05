"use client";

import Link from "next/link";
import { use, useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { Field, FormError, SelectField } from "@/components/Field";
import { PageTitle, SectionTitle } from "@/components/Heading";
import { Progress } from "@/components/Progress";
import { canDo, useUser } from "@/components/Shell";
import table from "@/components/Table.module.css";
import { api, messageOf, postForm } from "@/lib/api";
import { percent } from "@/lib/format";
import type { Batch, EnrollResult, ImageReport } from "@/lib/types";
import styles from "./page.module.css";

const SOURCES = ["MUGSHOT", "CCTV", "PHONE", "OTHER"] as const;
const POLL_MS = 2000;

function Report({ items }: { items: ImageReport[] }) {
  const flagged = items.filter((item) => item.duplicates.length > 0);
  return (
    <div className={styles.report}>
      <SectionTitle en="Per-image report" or="ପ୍ରତିଟି ଛବିର ରିପୋର୍ଟ" />
      <div className={table.wrap}>
        <table className={table.table}>
          <thead>
            <tr>
              <th>File</th><th>Result</th><th>Record</th><th>Face px</th><th>Blur</th><th>Yaw</th><th>Quality</th><th>Why</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, index) => (
              <tr key={`${item.filename}-${index}`}>
                <td>{item.filename}</td>
                <td>{item.status}</td>
                <td>{item.record_id ?? "—"}</td>
                <td>{item.face_px ?? "—"}</td>
                <td>{item.blur ?? "—"}</td>
                <td>{item.yaw ?? "—"}</td>
                <td>{item.face_px === null ? "—" : item.low_quality ? "LOW" : "OK"}</td>
                <td className={table.text}>{item.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <SectionTitle en="Duplicates found" or="ନକଲ" />
      {flagged.length === 0 ? (
        <p className={table.empty}>None.</p>
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr><th>File</th><th>Already on</th><th>Confidence</th></tr>
            </thead>
            <tbody>
              {flagged.flatMap((item) =>
                item.duplicates.map((hit) => (
                  <tr key={`${item.filename}-${hit.record_id}`}>
                    <td>{item.filename}</td>
                    <td>{hit.record_id}</td>
                    <td>{percent(hit.confidence)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Single({ record }: { record?: string }) {
  const [result, setResult] = useState<EnrollResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    const form = new FormData(event.currentTarget);
    for (const [key, value] of [...form.entries()]) {
      if (value === "") form.delete(key);
    }
    try {
      setResult(await postForm<EnrollResult>("/enroll", form));
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form onSubmit={submit} className={styles.form}>
        {error ? <FormError>{error}</FormError> : null}
        <Field id="record_id" name="record_id" label="Existing record" hint="Blank opens a new one" mono defaultValue={record ?? ""} placeholder="RKL-2021-000913" />
        <Field id="full_name" name="full_name" label="Name" hint="New records" />
        <Field id="gender" name="gender" label="Gender" maxLength={16} />
        <Field id="birth_year" name="birth_year" label="Birth year" mono inputMode="numeric" />
        <Field id="address" name="address" label="Address" />
        <Field id="phone" name="phone" label="Phone" mono />
        <Field id="id_number" name="id_number" label="ID number" mono />
        <SelectField id="source" name="source" label="Source" options={SOURCES} />
        <Field id="files" name="files" label="Photos" hint="One person per photo" type="file" multiple required accept="image/jpeg,image/png,image/webp,image/heic,.heic" />
        <div className={styles.actions}>
          <Button type="submit" variant="solid" size="big" disabled={busy}>
            {busy ? "Enrolling" : "Enroll"}
          </Button>
          {result?.person_id ? (
            <Link href={`/records/${result.person_id}`} className="data">
              OPEN {result.record_id}
            </Link>
          ) : null}
        </div>
      </form>
      {result ? <Report items={result.report} /> : null}
    </>
  );
}

function Bulk() {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = batch?.status === "QUEUED" || batch?.status === "RUNNING";

  useEffect(() => {
    if (!batch || !running) return;
    const timer = setTimeout(async () => {
      try {
        setBatch(await api<Batch>(`/enroll/batches/${batch.id}`));
      } catch (e) {
        setError(messageOf(e));
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [batch, running]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      setBatch(await postForm<Batch>("/enroll/bulk", new FormData(event.currentTarget)));
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <>
      <form onSubmit={submit} className={styles.form}>
        {error ? <FormError>{error}</FormError> : null}
        <Field id="manifest" name="manifest" label="CSV" hint="group, full_name, image_filename" type="file" required accept=".csv,text/csv" />
        <Field id="archive" name="archive" label="Zip of photos" hint="Filenames match the CSV" type="file" required accept=".zip,application/zip" />
        <SelectField id="bulk_source" name="source" label="Default source" options={SOURCES} />
        <div className={styles.actions}>
          <Button type="submit" variant="solid" size="big" disabled={running}>
            Start batch
          </Button>
        </div>
      </form>
      {batch ? (
        <div className={styles.batch}>
          <Progress
            step={`${batch.status} ${batch.processed} OF ${batch.total}`}
            fraction={batch.total ? batch.processed / batch.total : 0}
          />
          {batch.error ? <p role="alert">{batch.error}</p> : null}
        </div>
      ) : null}
      {batch && batch.report.length > 0 ? <Report items={batch.report} /> : null}
    </>
  );
}

export default function Enroll({ searchParams }: { searchParams: Promise<{ record?: string }> }) {
  const { record } = use(searchParams);
  const user = useUser();
  const [mode, setMode] = useState<"SINGLE" | "BULK">("SINGLE");

  if (!canDo(user, "ANALYST")) return <FormError>Your role can&apos;t do that.</FormError>;

  return (
    <>
      <PageTitle en="Enroll" or="ପଞ୍ଜୀକରଣ" />
      <div className={styles.modes} role="group" aria-label="Enroll mode">
        {(["SINGLE", "BULK"] as const).map((name) => (
          <button
            key={name}
            type="button"
            className={mode === name ? `${styles.mode} ${styles.modeActive}` : styles.mode}
            aria-pressed={mode === name}
            onClick={() => setMode(name)}
          >
            {name === "SINGLE" ? "Single record" : "Bulk upload"}
          </button>
        ))}
      </div>
      <div className={styles.content}>{mode === "SINGLE" ? <Single record={record} /> : <Bulk />}</div>
    </>
  );
}
