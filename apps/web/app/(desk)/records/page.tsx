"use client";

import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";

import { Button } from "@/components/Button";
import { FormError } from "@/components/Field";
import { PageTitle } from "@/components/Heading";
import { Loading } from "@/components/Progress";
import table from "@/components/Table.module.css";
import { count } from "@/lib/format";
import type { RecordPage } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import styles from "./page.module.css";

const PAGE = 50;

export default function Records({ searchParams }: { searchParams: Promise<{ focus?: string }> }) {
  const { focus } = use(searchParams);
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ q: query, offset: String(offset), limit: String(PAGE) });
  const { data, error, loading } = useResource<RecordPage>(`/records?${params}`);

  useEffect(() => {
    if (focus) input.current?.focus();
  }, [focus]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(text.trim());
      setOffset(0);
    }, 200);
    return () => clearTimeout(timer);
  }, [text]);

  return (
    <>
      <PageTitle
        en="Records"
        or="ରେକର୍ଡ"
        aside={data ? <>{count(data.total)} on file</> : null}
      />
      <div className={styles.search}>
        <label className="srOnly" htmlFor="q">
          Find record
        </label>
        <input
          id="q"
          ref={input}
          className={styles.input}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Find by record ID or name"
          autoComplete="off"
        />
      </div>
      <div className={styles.content}>
      {error ? <FormError>{error}</FormError> : null}
      {loading && !data ? <Loading what="Opening records" /> : null}
      {data && data.items.length === 0 ? <p className={table.empty}>Nothing matches that.</p> : null}
      {data && data.items.length > 0 ? (
        <>
          <ul className={styles.faces}>
            {data.items.map((row) => (
              <li key={row.id}>
                <Link href={`/records/${row.id}`} className={styles.card}>
                  {row.cover_image_id ? (
                    <img
                      src={`/api/records/images/${row.cover_image_id}?size=320`}
                      alt=""
                      className={styles.face}
                      loading="lazy"
                    />
                  ) : (
                    <span className={styles.noFace}>No photo</span>
                  )}
                  <span className={styles.placard}>
                    <span className={styles.id}>{row.record_id}</span>
                    <span className={styles.name}>{row.full_name}</span>
                    <span className={styles.meta}>
                      {row.images} {row.images === 1 ? "image" : "images"} / {row.cases} {row.cases === 1 ? "case" : "cases"}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className={table.pager}>
            <span className="data">
              {offset + 1}–{offset + data.items.length} of {count(data.total)}
            </span>
            <span className={table.pagerButtons}>
              <Button size="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </Button>
              <Button size="small" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </Button>
            </span>
          </div>
        </>
      ) : null}
      </div>
    </>
  );
}
