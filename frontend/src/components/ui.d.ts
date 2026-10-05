// Type declarations for the shared presentational components.
//
// The implementation lives in ui.jsx. These declarations give the TypeScript
// screens accurate optional props without forcing types onto the JavaScript
// pages that already use them.

import type { CSSProperties, ReactNode } from "react";

export function Panel(props: {
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  style?: CSSProperties;
}): JSX.Element;

export function Stat(props: {
  label?: ReactNode;
  value?: ReactNode;
  hint?: ReactNode;
  tone?: string;
}): JSX.Element;

export function Chip(props: { children?: ReactNode; tone?: string }): JSX.Element;

export function Tabs(props: {
  tabs: { id: string; label: ReactNode }[];
  active: string;
  onChange: (id: string) => void;
}): JSX.Element;

export function Empty(props: { children?: ReactNode }): JSX.Element;

export function ErrorBanner(props: { error?: { message?: string } | null }): JSX.Element | null;

export function Spinner(): JSX.Element;

export function Loading(props: { label?: string }): JSX.Element;

export function Progress(props: { value: number }): JSX.Element;

export function SimBadge(): JSX.Element;

export function Modal(props: {
  title?: ReactNode;
  children?: ReactNode;
  onClose: () => void;
}): JSX.Element;

export function useConfirmPhrase(): {
  value: string;
  setValue: (value: string) => void;
};

export function KeyValue(props: { rows: [ReactNode, ReactNode][] }): JSX.Element;
