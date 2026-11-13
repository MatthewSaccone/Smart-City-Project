import ServiceRequestForm from "../../ServiceRequestForm";
import Link from "next/link";
import { notFound } from "next/navigation";

type Field = {
  label: string;
  name: string;
  type?: "text" | "number";
  value?: string;
  min?: number;
  max?: number;
};

type Service = {
  title: string;
  description: string;
  action: string;
  fields: Field[];
};

function coordinates(
  prefix = "",
  latitude = "43.0096",
  longitude = "-81.2737",
): Field[] {
  const label = prefix ? `${prefix} ` : "";
  const name = prefix ? `${prefix.toLowerCase()}_` : "";

  return [
    { label: `${label}Latitude`, name: `${name}latitude`, type: "number",
      value: latitude, min: -90, max: 90 },
    { label: `${label}Longitude`, name: `${name}longitude`, type: "number",
      value: longitude, min: -180, max: 180 },
  ];
}

const radius: Field = {
  label: "Search radius (metres)",
  name: "radius_m",
  type: "number",
  value: "1000",
  min: 1,
};

const services = new Map<string, Service>([
  ["live-traffic", {
    title: "Live Traffic Analytics",
    description: "Check current congestion and estimated delay between two points.",
    action: "Check traffic",
    fields: [
      ...coordinates("Start"),
      ...coordinates("Destination", "42.9915", "-81.2495"),
    ],
  }],
  ["construction-incidents", {
    title: "Construction & Incidents",
    description: "Find roadwork and reported incidents near a location.",
    action: "Find incidents",
    fields: [...coordinates(), radius],
  }],
  ["parking-analytics", {
    title: "Parking Analytics",
    description: "Search for parking facilities within a sample area in London, Ontario.",
    action: "Find parking",
    fields: [
      { label: "South latitude", name: "south", type: "number",
        value: "42.9800", min: -90, max: 90 },
      { label: "North latitude", name: "north", type: "number",
        value: "43.0100", min: -90, max: 90 },
      { label: "West longitude", name: "west", type: "number",
        value: "-81.2700", min: -180, max: 180 },
      { label: "East longitude", name: "east", type: "number",
        value: "-81.2300", min: -180, max: 180 },
    ],
  }],
  ["weather-analytics", {
    title: "Weather Analytics",
    description: "Look up current weather conditions for a location.",
    action: "View weather",
    fields: coordinates(),
  }],
  ["transit-analytics", {
    title: "Public Transit Analytics",
    description: "Check upcoming arrivals at a stop. Use a valid stop ID once the API is connected.",
    action: "Check arrivals",
    fields: [{ label: "Stop ID", name: "stop_id" }],
  }],
  ["gasoline-analytics", {
    title: "Gasoline Price Analytics",
    description: "Find nearby stations and filter by fuel type and reported price.",
    action: "Find fuel prices",
    fields: [
      ...coordinates(),
      radius,
      { label: "Fuel type", name: "fuel_type", value: "regular_87" },
      { label: "Maximum price (CAD/L)", name: "max_price_per_litre",
        type: "number", value: "1.70", min: 0 },
    ],
  }],
]);

export default async function ServicePage({
  params,
}: {
  params: Promise<{ serviceId: string }>;
}) {
  const { serviceId } = await params;
  const service = services.get(serviceId);

  if (!service) notFound();

  const fields = service.fields.map((field) => (
    <label key={field.name} className="grid gap-1">
      {field.label}
      <input
        name={field.name}
        type={field.type ?? "text"}
        defaultValue={field.value}
        min={field.min}
        max={field.max}
        step={field.type === "number" ? "any" : undefined}
        required
        className="rounded-md border border-slate-300 p-2"
      />
    </label>
  ));

  return (
    <main className="min-h-screen bg-slate-100 p-6 text-slate-900">
      <section className="mx-auto max-w-xl">
        <Link href="/" className="font-semibold text-teal-700 underline">
          ← All services
        </Link>

        <h1 className="mt-5 text-3xl font-bold">{service.title}</h1>
        <p className="mt-2 text-slate-600">{service.description}</p>

      {["parking-analytics", "live-traffic", "construction-incidents"].includes(serviceId) ? (
          <ServiceRequestForm serviceId={serviceId} action={service.action}>
            {fields}
          </ServiceRequestForm>
        ) : (
          <div className="mt-6 grid gap-4 rounded-xl bg-white p-6">
            {fields}
            <button
              type="button"
              disabled
              className="rounded-md bg-slate-200 px-4 py-2 text-slate-600"
            >
              {service.action}
            </button>
            <p className="text-sm text-slate-600">
              This service is not connected yet.
            </p>
          </div>
        )}
      </section>
    </main>
  );
}