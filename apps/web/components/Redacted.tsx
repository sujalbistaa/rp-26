"use client";

import { useState } from "react";

import { messageOf, post } from "@/lib/api";
import type { RevealField } from "@/lib/types";
import { Button } from "./Button";
import styles from "./Redacted.module.css";

interface Props {
  personId: string;
  field: RevealField;
  label: string;
  present: boolean;
  searchId?: string | null;
  onRevealed?: () => void;
}

/** A solid ink bar. REVEAL removes it and the API writes an audit row. */
export function Redacted({ personId, field, label, present, searchId, onRevealed }: Props) {
  const [value, setValue] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!present) return <span className="muted">Not on file</span>;

  async function reveal() {
    try {
      const body = await post<{ value: string | null }>(`/records/${personId}/reveal`, {
        field,
        search_id: searchId ?? null,
      });
      setValue(body.value ?? "");
      onRevealed?.();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  if (value !== null) return <span className="data">{value}</span>;

  return (
    <span className={styles.line}>
      <span className={styles.bar} role="img" aria-label={`${label} hidden`} />
      <Button size="small" onClick={reveal} aria-label={`Reveal ${label}`}>
        Reveal
      </Button>
      {error ? <span role="alert">{error}</span> : null}
    </span>
  );
}
