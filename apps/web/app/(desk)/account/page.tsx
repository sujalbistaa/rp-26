"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { Field, FormError } from "@/components/Field";
import { PageTitle } from "@/components/Heading";
import { useUser } from "@/components/Shell";
import { messageOf, post } from "@/lib/api";
import styles from "./page.module.css";

export default function Account() {
  const user = useUser();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    setError(null);
    setDone(false);
    if (form.get("new") !== form.get("again")) {
      setError("Those two passwords don't match.");
      return;
    }
    try {
      await post("/auth/password", { current: form.get("current"), new: form.get("new") });
      target.reset();
      setDone(true);
      if (user.must_change_password) window.location.assign("/search");
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <>
      <PageTitle en="Account" or="ଖାତା" aside={<>{user.officer_id}<br />{user.role}</>} />
      {user.must_change_password ? (
        <p className={styles.must}>Change your temporary password before you do anything else.</p>
      ) : null}
      <form onSubmit={submit} className={styles.form}>
        {error ? <FormError>{error}</FormError> : null}
        <Field id="current" name="current" label="Current" type="password" required autoComplete="current-password" />
        <Field id="new" name="new" label="New" hint="12 characters or more" type="password" required minLength={12} autoComplete="new-password" />
        <Field id="again" name="again" label="New, again" type="password" required minLength={12} autoComplete="new-password" />
        <div className={styles.actions}>
          <Button type="submit" variant="solid" size="big">Change password</Button>
          {done ? <p role="status">Done. Other sessions are signed out.</p> : null}
        </div>
      </form>
    </>
  );
}
