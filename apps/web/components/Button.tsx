import type { ButtonHTMLAttributes, ReactNode } from "react";

import styles from "./Button.module.css";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  as?: "button" | "span";
  variant?: "default" | "solid" | "danger";
  size?: "default" | "small" | "big";
}

export function Button({
  as = "button",
  variant = "default",
  size = "default",
  className,
  type = "button",
  ...props
}: Props) {
  const classes = [styles.button, styles[variant], styles[size], className]
    .filter(Boolean)
    .join(" ");
  if (as === "span") return <span className={classes}>{props.children}</span>;
  return <button type={type} className={classes} {...props} />;
}
