import { Suspense } from "react";
const serviceCards = [
  { id: "live-traffic", name: "Live Traffic Analytics" },
  { id: "traffic-predictor", name: "Traffic Predictor" },
  { id: "construction-incidents", name: "Construction & Incidents" },
  { id: "parking-analytics", name: "Parking Analytics" },
  { id: "weather-analytics", name: "Weather Analytics" },
  { id: "transit-analytics", name: "Public Transit Analytics" },
  { id: "gasoline-analytics", name: "Gasoline Price Analytics" },
];

type RegisteredService = {
  service_id: string;
  healthy_instance_count: number;
};

async function getServices(): Promise<RegisteredService[] | null> {
  try {
    const response = await fetch("http://localhost:8090/v1/services", {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });

    if (!response.ok) return null;

    const data = await response.json();
    return Array.isArray(data.services) ? data.services : null;
  } catch {
    return null;
  }
}

export default function Home() {
  return (
    <Suspense fallback={<p className="p-6">Checking service availability…</p>}>
      <ServiceDirectory />
    </Suspense>
  );
}

async function ServiceDirectory() {
  const registeredServices = await getServices();

  return (
    <main className="min-h-screen bg-slate-100 p-6 text-slate-900 sm:p-10">
      <section className="mx-auto max-w-5xl">
        <p className="font-semibold text-teal-700">Smart City Services</p>
        <h1 className="mt-2 text-3xl font-bold">Find a city service</h1>
        <p className="mt-2 text-slate-600">
          Refresh this page to check the latest service availability.
        </p>

        {registeredServices === null && (
          <p role="status" className="mt-4 rounded-lg bg-amber-100 p-3">
            Could not check service availability. Make sure the local
            Docker stack is running, then refresh this page.
          </p>
        )}

        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {serviceCards.map((card) => {
            const count = registeredServices?.find(
              (service) => service.service_id === card.id
            )?.healthy_instance_count;

            const status =
              registeredServices === null
                ? "Status unknown"
                : count === undefined
                  ? "Not discovered"
                  : count === 0
                    ? "Unavailable"
                    : `Available · ${count} healthy ${count === 1 ? "instance" : "instances"}`;
{card.id === "traffic-predictor" && (
  <a
    href="/traffic-predictor"
    className="mt-4 block w-fit rounded-md bg-teal-700 px-4 py-2 text-white"
  >
    Open service
  </a>
)}
            return (
              <article
  key={card.id}
  className="rounded-xl border border-slate-200 bg-white p-5"
>
  <h2 className="text-lg font-semibold">{card.name}</h2>

  <p
    className={`mt-3 inline-block rounded-full px-3 py-1 text-sm ${
      count !== undefined && count > 0
        ? "bg-emerald-100 text-emerald-900"
        : "bg-slate-100 text-slate-700"
    }`}
  >
    {status}
  </p>

  {card.id === "traffic-predictor" && (
    <div className="mt-4">
      <a
        href="/traffic-predictor"
        className="inline-block rounded-md bg-teal-700 px-4 py-2 font-semibold text-white"
      >
        Open service
      </a>
    </div>
  )}
</article>
            );
          })}
        </div>
      </section>
    </main>
  );
}