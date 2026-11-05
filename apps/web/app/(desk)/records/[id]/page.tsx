"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { EvidenceTag } from "@/components/EvidenceTag";
import { AreaField, Field, FormError } from "@/components/Field";
import { Loading } from "@/components/Progress";
import { Redacted } from "@/components/Redacted";
import { canDo, useUser } from "@/components/Shell";
import table from "@/components/Table.module.css";
import { messageOf, patch, post } from "@/lib/api";
import { date, stamp } from "@/lib/format";
import { useKeys } from "@/lib/keys";
import type { Dossier, LogRow } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import styles from "./page.module.css";

const TABS = ["Summary", "Photos", "Cases", "Notes", "Log"] as const;
type Tab = (typeof TABS)[number];

function Summary({ record, searchId, editable, onChanged }: { record: Dossier; searchId?: string; editable: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: Record<string, string | number> = {};
    for (const [key, value] of form.entries()) {
      if (typeof value === "string" && value.trim()) {
        body[key] = key === "birth_year" ? Number(value) : value.trim();
      }
    }
    try {
      await patch(`/records/${record.id}`, body);
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  if (editing) {
    return (
      <form onSubmit={save}>
        {error ? <FormError>{error}</FormError> : null}
        <Field id="full_name" name="full_name" label="Name" defaultValue={record.full_name} />
        <Field id="gender" name="gender" label="Gender" defaultValue={record.gender ?? ""} maxLength={16} />
        <Field id="birth_year" name="birth_year" label="Birth year" mono defaultValue={record.birth_year ?? ""} inputMode="numeric" />
        <Field id="address" name="address" label="Address" hint="Blank keeps the current value" />
        <Field id="phone" name="phone" label="Phone" hint="Blank keeps the current value" mono />
        <Field id="id_number" name="id_number" label="ID number" hint="Blank keeps the current value" mono />
        <div className={styles.actions}>
          <Button type="submit" variant="solid">Save</Button>
          <Button onClick={() => setEditing(false)}>Cancel</Button>
        </div>
      </form>
    );
  }

  return (
    <>
      <dl className={styles.ledger}>
        <dt>Name</dt>
        <dd>{record.full_name}</dd>
        <dt>Gender</dt>
        <dd className="data">{record.gender ?? "—"}</dd>
        <dt>Birth year</dt>
        <dd className="data">{record.birth_year ?? "—"}</dd>
        <dt>Address</dt>
        <dd><Redacted personId={record.id} field="address" label="address" present={record.has_address} searchId={searchId} /></dd>
        <dt>Phone</dt>
        <dd><Redacted personId={record.id} field="phone" label="phone" present={record.has_phone} searchId={searchId} /></dd>
        <dt>ID number</dt>
        <dd><Redacted personId={record.id} field="id_number" label="ID number" present={record.has_id_number} searchId={searchId} /></dd>
        <dt>Opened</dt>
        <dd className="data">{date(record.created_at)}</dd>
        <dt>Updated</dt>
        <dd className="data">{date(record.updated_at)}</dd>
      </dl>
      <p className={styles.hint}>Every reveal is logged against your ID.</p>
      {editable ? (
        <div className={styles.actions}>
          <Button onClick={() => setEditing(true)}>Edit record</Button>
          <Link href={`/enroll?record=${record.record_id}`} className={styles.plainLink}>
            <Button as="span">Add photos</Button>
          </Link>
        </div>
      ) : null}
    </>
  );
}

function Photos({ record, editable, onChanged }: { record: Dossier; editable: boolean; onChanged: () => void }) {
  const pad = (n: number) => String(n).padStart(2, "0");
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function remove(event: FormEvent<HTMLFormElement>, imageId: string) {
    event.preventDefault();
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "");
    try {
      await post(`/records/${record.id}/images/${imageId}/remove`, { reason });
      setRemoving(null);
      setError(null);
      onChanged();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  if (record.images.length === 0) return <p className={table.empty}>No photos on this record.</p>;
  return (
    <ul className={styles.photos}>
      {record.images.map((image, index) => (
        <li key={image.id}>
          <EvidenceTag
            src={`/api/records/images/${image.id}?size=480`}
            alt={`Image ${index + 1} of record ${record.record_id}`}
            caption={`IMG ${pad(index + 1)}/${pad(record.images.length)} · ENROLLED ${date(image.enrolled_at)} · SRC ${image.source}${image.low_quality ? " · LOW QUALITY" : ""}`}
          />
          {editable && removing !== image.id ? (
            <Button size="small" onClick={() => setRemoving(image.id)}>
              Remove image
            </Button>
          ) : null}
          {removing === image.id ? (
            <form onSubmit={(event) => void remove(event, image.id)}>
              <AreaField id={`remove-${image.id}`} label="Why is this image wrong" name="reason" required minLength={10} />
              {error ? <FormError>{error}</FormError> : null}
              <Button size="small" type="submit">
                Confirm removal
              </Button>
              <Button size="small" onClick={() => setRemoving(null)}>
                Keep image
              </Button>
            </form>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Cases({ record, editable, onChanged }: { record: Dossier; editable: boolean; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    try {
      await post(`/records/${record.id}/cases`, {
        case_number: form.get("case_number"),
        section: form.get("section") || null,
        station: form.get("station") || null,
      });
      target.reset();
      onChanged();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <>
      {record.cases.length === 0 ? (
        <p className={table.empty}>No cases linked.</p>
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr><th>Case</th><th>Section</th><th>Station</th><th>Status</th><th>Linked</th></tr>
            </thead>
            <tbody>
              {record.cases.map((item) => (
                <tr key={item.id}>
                  <td>{item.case_number}</td>
                  <td>{item.section ?? "—"}</td>
                  <td>{item.station ?? "—"}</td>
                  <td>{item.status}</td>
                  <td>{date(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editable ? (
        <form onSubmit={add} className={styles.addForm}>
          {error ? <FormError>{error}</FormError> : null}
          <Field id="case_number" name="case_number" label="Case no." mono required />
          <Field id="section" name="section" label="Section" />
          <Field id="station" name="station" label="Station" />
          <div className={styles.actions}>
            <Button type="submit">Link case</Button>
          </div>
        </form>
      ) : null}
    </>
  );
}

function Notes({ record, editable, onChanged }: { record: Dossier; editable: boolean; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    try {
      await post(`/records/${record.id}/notes`, { body: new FormData(target).get("body") });
      target.reset();
      onChanged();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <>
      {record.notes.length === 0 ? <p className={table.empty}>No notes yet.</p> : null}
      <ul>
        {record.notes.map((note) => (
          <li key={note.id} className={styles.note}>
            <p className="data muted">{stamp(note.created_at)} / {note.author}</p>
            <p>{note.body}</p>
          </li>
        ))}
      </ul>
      {editable ? (
        <form onSubmit={add} className={styles.addForm}>
          {error ? <FormError>{error}</FormError> : null}
          <AreaField id="body" name="body" label="New note" required maxLength={4000} />
          <div className={styles.actions}>
            <Button type="submit">Add note</Button>
          </div>
        </form>
      ) : null}
    </>
  );
}

function Log({ personId }: { personId: string }) {
  const { data, error } = useResource<LogRow[]>(`/records/${personId}/log`);
  if (error) return <FormError>{error}</FormError>;
  if (!data) return <Loading what="Reading log" />;
  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead>
          <tr><th>When</th><th>Officer</th><th>Action</th><th>Case</th><th>Field</th></tr>
        </thead>
        <tbody>
          {data.map((row, index) => (
            <tr key={`${row.at}-${index}`}>
              <td>{stamp(row.at)}</td>
              <td>{row.officer}</td>
              <td>{row.action}</td>
              <td>{row.case_number ?? "—"}</td>
              <td>{row.detail.field ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RecordPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ search?: string }>;
}) {
  const { id } = use(params);
  const { search } = use(searchParams);
  const router = useRouter();
  const user = useUser();
  const [tab, setTab] = useState<Tab>("Summary");
  const path = `/records/${id}${search ? `?search_id=${search}` : ""}`;
  const { data: record, error, reload } = useResource<Dossier>(path);
  const editable = canDo(user, "ANALYST");

  useKeys({ Escape: () => router.push(search ? `/search/${search}` : "/records") });

  if (error) return <FormError>{error}</FormError>;
  if (!record) return <Loading what="Opening file" />;

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.shots}>
          {record.images.slice(0, 2).map((image, index) => (
            <img
              key={image.id}
              src={`/api/records/images/${image.id}?size=480`}
              alt={`Image ${index + 1} of record ${record.record_id}`}
              className={styles.shot}
            />
          ))}
        </div>
        <div className={styles.placard}>
          <p className={styles.kicker}>
            Criminal record / <span lang="or" className={styles.odia}>ଅପରାଧ ରେକର୍ଡ</span>
          </p>
          <h1 className={styles.record}>{record.record_id}</h1>
          <p className={styles.name}>{record.full_name}</p>
          <dl className={styles.counts}>
            <div>
              <dt className="label">Images</dt>
              <dd className={styles.big}>{record.images.length}</dd>
            </div>
            <div>
              <dt className="label">Cases</dt>
              <dd className={styles.big}>{record.cases.length}</dd>
            </div>
          </dl>
        </div>
      </header>
      <nav className={styles.tabs} aria-label="Dossier sections">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            className={tab === name ? `${styles.tab} ${styles.tabActive}` : styles.tab}
            aria-current={tab === name ? "true" : undefined}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>
      <div className={styles.file}>
        {tab === "Summary" ? <Summary record={record} searchId={search} editable={editable} onChanged={() => void reload()} /> : null}
        {tab === "Photos" ? <Photos record={record} editable={editable} onChanged={() => void reload()} /> : null}
        {tab === "Cases" ? <Cases record={record} editable={editable} onChanged={() => void reload()} /> : null}
        {tab === "Notes" ? <Notes record={record} editable={editable} onChanged={() => void reload()} /> : null}
        {tab === "Log" ? <Log personId={record.id} /> : null}
      </div>
    </div>
  );
}
