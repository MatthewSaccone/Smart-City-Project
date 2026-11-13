import Link from "next/link";
import ServiceRequestForm from "../ServiceRequestForm";

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

        <ServiceRequestForm serviceId="traffic-predictor" action="Get prediction">
  <label className="grid gap-1">
    Latitude
    <input
      name="latitude"
      type="number"
      step="any"
      min={-90}
      max={90}
      defaultValue={43.6532}
      required
      className="rounded-md border border-slate-300 p-2"
    />
  </label>

  <label className="grid gap-1">
    Longitude
    <input
      name="longitude"
      type="number"
      step="any"
      min={-180}
      max={180}
      defaultValue={-79.3832}
      required
      className="rounded-md border border-slate-300 p-2"
    />
  </label>

  <label className="grid gap-1">
    Forecast date and time (your local time)
    <input
      name="forecastTime"
      type="datetime-local"
      required
      className="rounded-md border border-slate-300 p-2"
    />
  </label>
</ServiceRequestForm>
      </section>
    </main>
  );
}