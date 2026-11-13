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
      id === "construction-incidents"
    ) {
      const route = routes[id];
      url = new URL(route.url);

      for (const [field, parameter] of Object.entries(route.query)) {
        url.searchParams.set(parameter, String(numberField(field)));
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