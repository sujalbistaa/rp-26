"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { AreaField, Field, FormError } from "@/components/Field";
import { PageTitle, SectionTitle } from "@/components/Heading";
import { Progress } from "@/components/Progress";
import { Stamp } from "@/components/Stamp";
import table from "@/components/Table.module.css";
import { messageOf, post, postForm } from "@/lib/api";
import { count, stamp } from "@/lib/format";
import { useKeys } from "@/lib/keys";
import type { Probe, Results, SearchRow } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import styles from "./page.module.css";

const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif";
const MAX_BYTES = 15 * 1024 * 1024;

type Step = { name: string; fraction: number } | null;

export default function NewSearch() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [step, setStep] = useState<Step>(null);
  const [error, setError] = useState<string | null>(null);
  const recent = useResource<SearchRow[]>("/search");

  useKeys({ u: () => input.current?.click() });

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function choose(next: File | undefined) {
    setProbe(null);
    setError(null);
    if (!next) return;
    if (next.size > MAX_BYTES) {
      setFile(null);
      setError("That photo is over 15 MB.");
      return;
    }
    setFile(next);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  async function run(id: string, gallery: number, faceIndex: number | null) {
    setStep({ name: `SEARCHING ${count(gallery)} IMAGES`, fraction: 0.75 });
    try {
      const result = await post<Results>(`/search/${id}/run`, { face_index: faceIndex });
      setStep({ name: `DONE ${result.duration_ms ?? 0} MS`, fraction: 1 });
      router.push(`/search/${id}`);
    } catch (e) {
      setError(messageOf(e));
      setStep(null);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Drop a photo first.");
      return;
    }
    const form = new FormData(event.currentTarget);
    form.set("file", file);
    setError(null);
    setStep({ name: "DETECTING FACE", fraction: 0.25 });
    try {
      const created = await postForm<Probe>("/search/probe", form);
      setStep({ name: "CHECKING QUALITY", fraction: 0.5 });
      if (created.status === "NO_FACE") {
        setError("Couldn't find a face in this photo.");
        setStep(null);
        void recent.reload();
      } else if (created.faces.length === 1) {
        await run(created.id, created.gallery_size, null);
      } else {
        setProbe(created);
        setStep(null);
      }
    } catch (e) {
      setError(messageOf(e));
      setStep(null);
    }
  }

  const busy = step !== null;

  return (
    <>
      <PageTitle en="New Search" or="ନୂତନ ସନ୍ଧାନ" aside={<>JPG, PNG, WEBP, HEIC<br />Up to 15 MB</>} />
      <div className={styles.content}>
      <form className={styles.desk} onSubmit={submit}>
        {probe ? (
          <section className={styles.picker} aria-label="Pick a face">
            <div className={styles.pickerHead}>
              <p className={styles.pickerTitle}>{probe.faces.length} faces in this photo</p>
              <p className="muted">Pick the one to search. The system won&apos;t choose for you.</p>
            </div>
            <div className={styles.photo}>
              <img src={`/api/search/${probe.id}/probe`} alt="Uploaded probe" className={styles.photoImage} />
              {probe.faces.map((face) => {
                const [x1, y1, x2, y2] = face.box;
                return (
                  <button
                    key={face.index}
                    type="button"
                    className={styles.faceBox}
                    disabled={busy}
                    onClick={() => void run(probe.id, probe.gallery_size, face.index)}
                    style={{
                      left: `${(x1 / probe.width) * 100}%`,
                      top: `${(y1 / probe.height) * 100}%`,
                      width: `${((x2 - x1) / probe.width) * 100}%`,
                      height: `${((y2 - y1) / probe.height) * 100}%`,
                    }}
                  >
                    <span className={styles.faceLabel}>Face {face.index + 1}</span>
                  </button>
                );
              })}
            </div>
          </section>
        ) : (
          <label
            className={dragging ? `${styles.zone} ${styles.over}` : styles.zone}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <input
              ref={input}
              type="file"
              accept={ACCEPT}
              className="srOnly"
              onChange={(event) => choose(event.target.files?.[0])}
            />
            {preview && file ? (
              <span className={styles.chosen}>
                <img src={preview} alt={`Chosen photo: ${file.name}`} className={styles.chosenImage} />
                <span className={styles.chosenName}>
                  {file.name} / {(file.size / 1048576).toFixed(1)} MB / press U to change
                </span>
              </span>
            ) : (
              <span className={styles.prompt}>
                <span className={styles.promptTitle}>Drop a photo here</span>
                <span className="muted">or press U to choose one</span>
              </span>
            )}
          </label>
        )}

        <aside className={styles.slip}>
          <SectionTitle en="Search details" or="ଖୋଜା ସୂଚନା" />
          {error ? <FormError>{error}</FormError> : null}
          <div className={styles.fields}>
            <Field id="case_number" name="case_number" label="FIR / Case no." hint="Required" mono required minLength={3} maxLength={40} placeholder="214/26" />
          <AreaField id="reason" name="reason" label="Reason" hint="Required" required minLength={10} maxLength={500} placeholder="What the search is for." />
          </div>
          <div className={styles.actions}>
            {step ? <Progress step={step.name} fraction={step.fraction} /> : null}
            {!probe ? (
              <Button type="submit" variant="solid" size="big" disabled={busy}>
                Run search
              </Button>
            ) : (
              <Button size="big" onClick={() => { setProbe(null); setFile(null); }} disabled={busy}>
                Start over
              </Button>
            )}
          </div>
          <p className={styles.notice}>No case number, no search. This search is logged against your ID.</p>
        </aside>
      </form>

      <section className={styles.recent}>
      <SectionTitle en="Your recent searches" or="ସାମ୍ପ୍ରତିକ ସନ୍ଧାନ" />
      {recent.data && recent.data.length > 0 ? (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr>
                <th>When</th>
                <th>Case</th>
                <th>People</th>
                <th>Probe</th>
                <th>Open</th>
              </tr>
            </thead>
            <tbody>
              {recent.data.map((row) => (
                <tr key={row.id}>
                  <td>{stamp(row.created_at)}</td>
                  <td>{row.case_number}</td>
                  <td>{row.status === "COMPLETE" ? count(row.people) : "—"}</td>
                  <td>
                    {row.purged ? <Stamp word="PURGED" /> : row.attached_to_case ? "On case" : row.status === "NO_FACE" ? "No face" : row.low_quality ? "Low quality" : "OK"}
                  </td>
                  <td>{row.status === "COMPLETE" ? <Link href={`/search/${row.id}`}>Open</Link> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={table.empty}>Nothing here yet. Drop a photo to start.</p>
      )}
      </section>
      </div>
    </>
  );
}
