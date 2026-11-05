import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

import styles from "./Field.module.css";

function Row({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className={styles.row}>
      <label className={styles.label} htmlFor={htmlFor}>
        {label}
        {hint ? <span className={styles.hint}>{hint}</span> : null}
      </label>
      <div className={styles.cell}>{children}</div>
    </div>
  );
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string;
  label: string;
  hint?: string;
  mono?: boolean;
}

export function Field({ label, hint, mono, id, ...props }: FieldProps) {
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <input id={id} className={mono ? `${styles.input} ${styles.mono}` : styles.input} {...props} />
    </Row>
  );
}

interface AreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  id: string;
  label: string;
  hint?: string;
}

export function AreaField({ label, hint, id, ...props }: AreaProps) {
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <textarea id={id} className={styles.input} rows={3} {...props} />
    </Row>
  );
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  id: string;
  label: string;
  options: readonly string[];
}

export function SelectField({ label, id, options, ...props }: SelectProps) {
  return (
    <Row label={label} htmlFor={id}>
      <select id={id} className={`${styles.input} ${styles.mono}`} {...props}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </Row>
  );
}

export function FormError({ children }: { children: ReactNode }) {
  return (
    <p className={styles.error} role="alert">
      {children}
    </p>
  );
}
