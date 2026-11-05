"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { Field, FormError } from "@/components/Field";
import { api, messageOf, post } from "@/lib/api";
import type { Role } from "@/lib/types";
import styles from "./page.module.css";

interface DevAccount {
  officer_id: string;
  name: string;
  role: Role;
}

/** Local development only. Production builds drop this, and the API has no such route there. */
function MockLogin({ onError }: { onError: (message: string) => void }) {
  const [accounts, setAccounts] = useState<DevAccount[]>([]);

  useEffect(() => {
    api<DevAccount[]>("/auth/dev-accounts").then(setAccounts, () => setAccounts([]));
  }, []);

  async function signIn(officerId: string) {
    try {
      await post("/auth/dev-login", { officer_id: officerId });
      window.location.assign("/search");
    } catch (e) {
      onError(messageOf(e));
    }
  }

  if (accounts.length === 0) return null;

  return (
    <section className={styles.mock} aria-label="Mock login">
      <h2 className={styles.mockTitle}>Mock login (dev only)</h2>
      <ul>
        {accounts.map((account) => (
          <li key={account.officer_id} className={styles.mockRow}>
            <span className="data">{account.officer_id}</span>
            <span className="label">{account.role}</span>
            <Button size="small" onClick={() => void signIn(account.officer_id)}>
              Sign in
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function SignIn() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await post("/auth/login", {
        officer_id: form.get("officer_id"),
        password: form.get("password"),
        totp: form.get("totp"),
      });
      window.location.assign("/search");
    } catch (e) {
      setError(messageOf(e));
      setBusy(false);
    }
  }

  return (
    <>
      <header className={styles.nav}>
        <div className={styles.navInner}>
          <span>Face Match Portal</span>
          <span>Authorised officers only</span>
        </div>
      </header>
      <div className={styles.page}>
        <main className={styles.intro}>
          <div>
            <h1 className={styles.wordmark}>Rourkela Police</h1>
            <p className={styles.odia} lang="or">
              ରାଉରକେଲା ପୋଲିସ
            </p>
          </div>
          <ol className={styles.rules}>
            <li className={styles.rule}>
              <span className={styles.ruleNumber}>01</span>
              <span>Every search needs an FIR or case number and a reason.</span>
            </li>
            <li className={styles.rule}>
              <span className={styles.ruleNumber}>02</span>
              <span>Every search, view and reveal is logged against your ID.</span>
            </li>
            <li className={styles.rule}>
              <span className={styles.ruleNumber}>03</span>
              <span>A match is an investigative lead, never an identification.</span>
            </li>
          </ol>
        </main>
        <div className={styles.side}>
          <form onSubmit={submit}>
            <h2 className={styles.formTitle}>Sign in</h2>
            {error ? <FormError>{error}</FormError> : null}
            <Field id="officer_id" name="officer_id" label="Officer ID" mono required autoComplete="username" autoFocus />
            <Field id="password" name="password" label="Password" type="password" required autoComplete="current-password" />
            <Field
              id="totp"
              name="totp"
              label="2FA code"
              hint="6 digits"
              mono
              required
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              autoComplete="one-time-code"
            />
            <div className={styles.actions}>
              <Button type="submit" variant="solid" size="big" disabled={busy}>
                {busy ? "Checking" : "Sign in"}
              </Button>
            </div>
          </form>
          {process.env.NODE_ENV === "development" ? <MockLogin onError={setError} /> : null}
        </div>
      </div>
    </>
  );
}
