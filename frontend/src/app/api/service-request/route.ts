const routes = {
  "parking-analytics": {
    url: "http://localhost:8084/v1/parking/facilities",
    query: {
      south: "min_lat",
      west: "min_lon",
      north: "max_lat",
      east: "max_lon",
    },
  },
  "live-traffic": {
    url: "http://localhost:8081/v1/traffic/congestion/route",
    query: {
      start_latitude: "start_latitude",
      start_longitude: "start_longitude",
      destination_latitude: "end_latitude",
      destination_longitude: "end_longitude",
    },
  },
  "construction-incidents": {
    url: "http://localhost:8083/v1/incidents",
    query: {
      latitude: "latitude",
      longitude: "longitude",
      radius_m: "radius_m",
    },
  },
  "gasoline-analytics": {
    url: "http://localhost:8087/v1/gas-stations",
    query: {
      latitude: "latitude",
      longitude: "longitude",
      radius_m: "radius_m",
    },
  },
} as const;

export async function POST(request: Request) {
  let form: FormData;

  try {
    form = await request.formData();
  } catch {
    return Response.json(
      { message: "Could not read the submitted form." },
      { status: 400 },
    );
  }

  const id = form.get("serviceId");

  function numberField(name: string): number {
    const raw = form.get(name);
    if (
      typeof raw !== "string" ||
      !raw.trim() ||
      !Number.isFinite(Number(raw))
    ) {
      throw new Error(`Enter a valid number for ${name}.`);
    }
    return Number(raw);
  }

  let url: URL;
  let options: RequestInit = {
    method: "GET",
    cache: "no-store",
    redirect: "error",
  };

  try {
    if (id === "traffic-predictor") {
      const time = form.get("forecast_time_utc");
      const forecast = typeof time === "string" ? Date.parse(time) : NaN;
      const now = Date.now();

      if (
        !Number.isFinite(forecast) ||
        forecast <= now ||
        forecast > now + 7 * 24 * 60 * 60 * 1000
      ) {
        throw new Error("Choose a future forecast time within the next seven days.");
      }

      url = new URL("http://localhost:8082/v1/traffic/predictions");
      options = {
        ...options,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          location: {
            latitude: numberField("latitude"),
            longitude: numberField("longitude"),
          },
          forecast_time: new Date(forecast).toISOString(),
        }),
      };
    } else if (
      id === "parking-analytics" ||
      id === "live-traffic" ||
      id === "construction-incidents" ||
      id === "gasoline-analytics"
    ) {
      const route = routes[id];
      url = new URL(route.url);

      for (const [field, parameter] of Object.entries(route.query)) {
        url.searchParams.set(parameter, String(numberField(field)));
      }

      if (id === "gasoline-analytics") {
        const latitude = numberField("latitude");
        const longitude = numberField("longitude");
        const radius = numberField("radius_m");
        if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 ||
            radius <= 0 || radius > 25000) {
          throw new Error("Coordinates must be valid and search radius must be between 0 and 25,000 metres.");
        }

        const fuelType = form.get("fuel_type");
        const supportedFuels = ["regular_87", "midgrade_89", "premium_91", "premium_93", "diesel"];
        if (typeof fuelType !== "string" || !supportedFuels.includes(fuelType)) {
          throw new Error("Choose a supported fuel type.");
        }
        url.searchParams.set("fuel_type", fuelType);

        const maximumPrice = form.get("max_price_per_litre");
        if (typeof maximumPrice === "string" && maximumPrice.trim() !== "") {
          const price = numberField("max_price_per_litre");
          if (price < 0.001 || price > 20 || !/^\d+(?:\.\d{1,3})?$/.test(maximumPrice.trim())) {
            throw new Error("Maximum price must be between 0.001 and 20.000 CAD/L (up to three decimal places).");
          }
          url.searchParams.set("max_price_per_litre", maximumPrice.trim());
        }
      }
    } else {
      return Response.json(
        { message: "This service is not connected yet." },
        { status: 400 },
      );
    }
  } catch (error) {
    return Response.json(
      {
        message: error instanceof Error ? error.message : "Check the form inputs.",
      },
      { status: 400 },
    );
  }

  try {
    const response = await fetch(url, {
      ...options,
      signal: AbortSignal.timeout(5000),
    });

    const data = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = data?.error?.message ?? data?.detail;
      return Response.json(
        {
          message:
            typeof detail === "string"
              ? detail
              : `The service returned HTTP ${response.status}. Check the inputs or try again.`,
        },
        { status: response.status },
      );
    }

    if (data === null) {
      return Response.json(
        { message: "The service did not return a JSON result." },
        { status: 502 },
      );
    }

    return Response.json(data);
  } catch {
    return Response.json(
      { message: "Could not get a response from the service. Check that it is running, then retry." },
      { status: 502 },
    );
  }
}