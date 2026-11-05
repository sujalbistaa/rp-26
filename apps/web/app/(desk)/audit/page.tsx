"use client";

import { useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { FormError } from "@/components/Field";
import { PageTitle } from "@/components/Heading";
import { Loading } from "@/components/Progress";
import { canDo, useUser } from "@/components/Shell";
import table from "@/components/Table.module.css";
import { count, stamp } from "@/lib/format";
import type { AuditPage } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import styles from "./page.module.css";

const PAGE = 100;

interface Filters {
  action: string;
  officer: string;
  case: string;
  since: string;
  until: string;
}

const EMPTY: Filters = { action: "", officer: "", case: "", since: "", until: "" };

function query(filters: Filters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return params;
}

export default function Audit() {
  const user = useUser();
  const allowed = canDo(user, "ADMIN");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const params = useMemo(() => query(filters), [filters]);
  const page = new URLSearchParams(params);
  page.set("offset", String(offset));
  page.set("limit", String(PAGE));
  const { data, error } = useResource<AuditPage>(allowed ? `/audit?${page}` : null);
  const { data: actions } = useResource<string[]>(allowed ? "/audit/actions" : null);

  if (!allowed) return <FormError>Your role can&apos;t do that.</FormError>;

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOffset(0);
    setFilters({
      action: String(form.get("action") ?? ""),
      officer: String(form.get("officer") ?? "").trim(),
      case: String(form.get("case") ?? "").trim(),
      since: String(form.get("since") ?? ""),
      until: String(form.get("until") ?? ""),
    });
  }

  return (
    <>
      <PageTitle
        en="Audit Log"
        or="ଅଡିଟ୍"
        aside={<>Append only<br />{data ? `${count(data.total)} rows` : "—"}</>}
      />
      <div className={styles.content}>
      <form className={styles.filters} onSubmit={apply}>
        <label>
          <span className="label">Action</span>
          <select name="action" className={styles.control}>
            <option value="">All</option>
            {actions?.map((action) => (
              <option key={action}>{action}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="label">Officer</span>
          <input name="officer" className={styles.control} autoComplete="off" />
        </label>
        <label>
          <span className="label">Case</span>
          <input name="case" className={styles.control} autoComplete="off" />
        </label>
        <label>
          <span className="label">From</span>
          <input name="since" type="date" className={styles.control} />
        </label>
        <label>
          <span className="label">To</span>
          <input name="until" type="date" className={styles.control} />
        </label>
        <Button type="submit" variant="solid" size="small">Filter</Button>
        <a href={`/api/audit/export.csv?${params}`} className={styles.export} download>
          <Button as="span" size="small">Export CSV</Button>
        </a>
      </form>
      {error ? <FormError>{error}</FormError> : null}
      {!data && !error ? <Loading what="Reading audit log" /> : null}
      {data ? (
        <div className={styles.panel}>
          <div className={table.wrap}>
            <table className={`${table.table} ${styles.dense}`}>
              <thead>
                <tr>
                  <th>#</th><th>When</th><th>Officer</th><th>Role</th><th>Action</th><th>IP</th><th>Case</th><th>Probe hash</th><th>Results</th><th>Cal</th><th>Target</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id}>
                    <td>{row.id}</td>
                    <td>{stamp(row.at)}</td>
                    <td>{row.actor_officer_id}</td>
                    <td>{row.actor_role ?? "—"}</td>
                    <td>{row.action}</td>
                    <td>{row.ip ?? "—"}</td>
                    <td>{row.case_number ?? "—"}</td>
                    <td title={row.probe_hash ?? undefined}>{row.probe_hash ? row.probe_hash.slice(0, 12) : "—"}</td>
                    <td>{row.results_returned ?? "—"}</td>
                    <td>{row.calibration_version ?? "—"}</td>
                    <td>{row.target_type ? `${row.target_type}/${(row.target_id ?? "").slice(0, 8)}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.items.length === 0 ? <p className={table.empty}>No rows match that.</p> : null}
          <div className={table.pager}>
            <span className="data">
              {data.total === 0 ? 0 : offset + 1}–{offset + data.items.length} of {count(data.total)}
            </span>
            <span className={table.pagerButtons}>
              <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button size="small" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </span>
          </div>
        </div>
      ) : null}
      </div>
    </>
  );
}
