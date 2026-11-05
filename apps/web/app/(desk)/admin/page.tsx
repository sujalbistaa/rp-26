"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { Field, FormError, SelectField } from "@/components/Field";
import { PageTitle, SectionTitle } from "@/components/Heading";
import { canDo, useUser } from "@/components/Shell";
import table from "@/components/Table.module.css";
import { messageOf, patch, post } from "@/lib/api";
import { count, date, wholePercent } from "@/lib/format";
import type { Calibration, CreatedUser, Role, User } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import styles from "./page.module.css";

const ROLES: readonly Role[] = ["OFFICER", "ANALYST", "ADMIN"];

function Users() {
  const me = useUser();
  const { data: users, reload } = useResource<User[]>("/admin/users");
  const [created, setCreated] = useState<CreatedUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    setError(null);
    try {
      setCreated(
        await post<CreatedUser>("/admin/users", {
          officer_id: form.get("officer_id"),
          name: form.get("name"),
          role: form.get("role"),
        }),
      );
      target.reset();
      await reload();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  async function change(id: string, body: Partial<Pick<User, "role" | "active">>) {
    setError(null);
    try {
      await patch(`/admin/users/${id}`, body);
      await reload();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <section className={styles.panel}>
      <SectionTitle en="Users" or="ବ୍ୟବହାରକାରୀ" />
      {error ? <FormError>{error}</FormError> : null}
      <div className={table.wrap}>
        <table className={table.table}>
          <thead>
            <tr><th>Officer ID</th><th>Name</th><th>Role</th><th>Status</th><th>Action</th></tr>
          </thead>
          <tbody>
            {users?.map((user) => (
              <tr key={user.id}>
                <td>{user.officer_id}</td>
                <td className={table.text}>{user.name}</td>
                <td>
                  {user.id === me.id ? (
                    user.role
                  ) : (
                    <select
                      aria-label={`Role for ${user.officer_id}`}
                      className={styles.select}
                      value={user.role}
                      onChange={(event) => void change(user.id, { role: event.target.value as Role })}
                    >
                      {ROLES.map((role) => (
                        <option key={role}>{role}</option>
                      ))}
                    </select>
                  )}
                </td>
                <td>{user.active ? "ACTIVE" : "DISABLED"}</td>
                <td>
                  {user.id === me.id ? (
                    "—"
                  ) : (
                    <Button
                      size="small"
                      variant={user.active ? "danger" : "default"}
                      onClick={() => void change(user.id, { active: !user.active })}
                    >
                      {user.active ? "Disable" : "Enable"}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form onSubmit={create} className={styles.form}>
        <Field id="officer_id" name="officer_id" label="Officer ID" mono required minLength={3} />
        <Field id="name" name="name" label="Name" required minLength={2} />
        <SelectField id="role" name="role" label="Role" options={ROLES} />
        <div className={styles.actions}>
          <Button type="submit">Create user</Button>
        </div>
      </form>
      {created ? (
        <dl className={styles.secret}>
          <dt className="label">SHOWN ONCE</dt>
          <dd>Hand these to {created.user.officer_id} in person. They can&apos;t be read back.</dd>
          <dt className="label">TEMPORARY PASSWORD</dt>
          <dd className="data">{created.temporary_password}</dd>
          <dt className="label">2FA SECRET</dt>
          <dd className="data">{created.totp_secret}</dd>
        </dl>
      ) : null}
    </section>
  );
}

function Thresholds() {
  const { data: versions, reload } = useResource<Calibration[]>("/admin/calibrations");
  const [error, setError] = useState<string | null>(null);

  async function activate(version: number) {
    setError(null);
    try {
      await post(`/admin/calibrations/${version}/activate`);
      await reload();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  async function setFloor(event: FormEvent<HTMLFormElement>, version: number) {
    event.preventDefault();
    const value = String(new FormData(event.currentTarget).get("floor") ?? "").trim();
    setError(null);
    try {
      await patch(`/admin/calibrations/${version}/floor`, {
        floor: value === "" ? null : Number(value) / 100,
      });
      await reload();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <section className={styles.panel}>
      <SectionTitle en="Thresholds" or="ସୀମା" />
      {error ? <FormError>{error}</FormError> : null}
      <p className={styles.help}>
        Bands come from calibration. You can raise the display floor. You can&apos;t lower it past
        the calibrated value.
      </p>
      {versions && versions.length === 0 ? (
        <p className={table.empty}>No calibration yet. Run scripts/calibrate.py.</p>
      ) : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead>
              <tr>
                <th>Version</th><th>Model</th><th>Fitted</th><th>Pairs</th><th>Floor</th><th>Possible</th><th>Strong</th><th>Data</th><th>Status</th><th>Set floor %</th>
              </tr>
            </thead>
            <tbody>
              {versions?.map((row) => (
                <tr key={row.version}>
                  <td>V{row.version}</td>
                  <td>{row.embedder_version}</td>
                  <td>{date(row.created_at)}</td>
                  <td>{count(row.n_genuine)} / {count(row.n_impostor)}</td>
                  <td>{wholePercent(row.floor)}</td>
                  <td>{wholePercent(row.possible)}</td>
                  <td>{wholePercent(row.strong)}</td>
                  <td>{row.synthetic ? "SYNTHETIC" : "GALLERY"}</td>
                  <td>
                    {row.active ? "ACTIVE" : (
                      <Button size="small" onClick={() => void activate(row.version)}>Activate</Button>
                    )}
                  </td>
                  <td>
                    <form className={styles.floor} onSubmit={(event) => void setFloor(event, row.version)}>
                      <input
                        name="floor"
                        aria-label={`Floor percent for version ${row.version}`}
                        className={styles.select}
                        inputMode="decimal"
                        placeholder={String(Math.round(row.floor * 100))}
                      />
                      <Button type="submit" size="small">Set</Button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function Admin() {
  const user = useUser();
  if (!canDo(user, "ADMIN")) return <FormError>Your role can&apos;t do that.</FormError>;
  return (
    <>
      <PageTitle en="Admin" or="ପ୍ରଶାସନ" />
      <div className={styles.content}>
        <Users />
        <Thresholds />
      </div>
    </>
  );
}
