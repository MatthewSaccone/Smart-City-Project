import Link from "next/link";

export default function TrafficPredictorPage() {
  const inputStyle = "rounded-md border border-slate-300 p-2";

  return (
    <main className="min-h-screen bg-slate-100 p-6 text-slate-900">
      <section className="mx-auto max-w-xl">
        <Link href="/" className="font-semibold text-teal-700 underline">
          ← All services
        </Link>

        <h1 className="mt-5 text-3xl font-bold">Traffic Predictor</h1>
        <p className="mt-2 text-slate-600">
          Enter a location and choose a future time.
        </p>

        <form className="mt-6 grid gap-4 rounded-xl bg-white p-6">
          <label className="grid gap-1">
            Latitude
            <input name="latitude" type="number" step="any" min={-90}
              max={90} defaultValue={43.0096} required className={inputStyle} />
          </label>

          <label className="grid gap-1">
            Longitude
            <input name="longitude" type="number" step="any" min={-180}
              max={180} defaultValue={-81.2737} required className={inputStyle} />
          </label>

          <label className="grid gap-1">
            Forecast date and time
            <input name="forecastTime" type="datetime-local"
              required className={inputStyle} />
          </label>

          <button type="button" disabled className="rounded-md bg-slate-200 p-2">
            Get prediction
          </button>
          <p className="text-sm text-slate-600">
            Predictions will be enabled when the API is connected.
          </p>
        </form>
      </section>
    </main>
  );
}