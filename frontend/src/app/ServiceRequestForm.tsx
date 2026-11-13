"use client";

import { useState, type FormEvent, type ReactNode } from "react";

type Props = {
  serviceId: string;
  action: string;
  children: ReactNode;
};

export default function ServiceRequestForm({
  serviceId,
  action,
  children,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;

    setMessage("");
    setResult("");

    const form = new FormData(event.currentTarget);
    form.set("serviceId", serviceId);

    if (serviceId === "traffic-predictor") {
      const value = form.get("forecastTime");
      const date = new Date(typeof value === "string" ? value : "");

      if (!Number.isFinite(date.getTime())) {
        setMessage("Choose a valid forecast date and time.");
        return;
      }

      form.set("forecast_time_utc", date.toISOString());
    }

    setBusy(true);

    try {
      const response = await fetch("/api/service-request", {
        method: "POST",
        body: form,
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        setMessage(
          typeof data?.message === "string"
            ? data.message
            : `The request failed (HTTP ${response.status}). Please try again.`,
        );
        return;
      }

      if (data === null) {
        setMessage("The server did not return a JSON result.");
        return;
      }

      setResult(JSON.stringify(data, null, 2));
    } catch {
      setMessage("Could not reach the dashboard server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-6 grid gap-4 rounded-xl bg-white p-6">
      {children}

      <button
        type="submit"
        disabled={busy}
        className="rounded-md bg-teal-700 px-4 py-2 font-semibold text-white disabled:opacity-50"
      >
        {busy ? "Loading…" : action}
      </button>

      {message && (
        <p role="alert" className="text-sm text-red-700">
          {message}
        </p>
      )}

      {result && (
        <section aria-label="Service result" aria-live="polite" className="min-w-0">
          <h2 className="mb-2 font-semibold">Service result</h2>
          <pre className="overflow-auto rounded-md bg-slate-100 p-3 text-sm">
            {result}
          </pre>
        </section>
      )}
    </form>
  );
}