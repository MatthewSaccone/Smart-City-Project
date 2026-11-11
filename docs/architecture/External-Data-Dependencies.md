# Smart City Platform — External Data Dependencies and Integration Policy

| Document property | Value |
|---|---|
| Document ID | T008-EXT-DEP |
| Version | 1.0 |
| Status | Architecture baseline — provider integrations pending verification |
| Coverage | S1–S7 domain microservices and their supporting ingestion services |
| Reference date | 2026-11-11 |
| Related specifications | `API-Standards.md`; S1–S7 service API contracts; `Service-Data-Ownership.md` |

## 1. Purpose and scope

This document identifies candidate data sources and external dependencies for all seven Smart City microservices. It defines **what information each integration must supply**, expected access mechanisms, applicable credentials and usage restrictions, refresh behavior, risks, outage fallbacks, and the checks needed before a provider is accepted.

It is **prescriptive about integration behavior**, but careful about external facts. A published provider API is not the same as an available account, an approved license, an installed integration, a demonstrated update rate, or guaranteed coverage. None of those are assumed without an explicit verification record. The project's original PR1 requirements remain authoritative for required service behavior; additional operational thresholds derive from the adopted service contracts.

**Geographic working example:** London, Ontario, and relevant Ontario-wide feeds. This is a practical research focus, **not** a new mandatory coverage restriction imposed by PR1. The final supported geographic boundary and coverage claims must be approved separately.

### Status terminology

| Status | Meaning |
|---|---|
| **Published / candidate** | Provider publicly documents the named feed or product. It has **not** been selected, provisioned, tested, or approved for platform use. |
| **Potential source / access unconfirmed** | Related information is public, but usable programmatic access, geography, licensing, or field coverage has not been established. |
| **First-party / planned** | The platform will produce this data through its own operation, user submissions, or derivations; no external vendor is implied. |
| **Unavailable / unverified** | A source satisfying the required capability has not been identified or validated. |
| **Integrated / approved** | Reserved for a future signed-off provider record with tested credentials, verified sample payloads, coverage, terms, limits and monitoring. **No external provider is assigned this status by this document.** |

## 2. Platform-wide integration requirements

1. Each domain service **MUST** own its normalized data and derived results according to `Service-Data-Ownership.md`. The Service Registry's database **MUST NOT** become a shared domain database.
2. External provider adapters **MUST** be isolated from the public S1–S7 contracts. Changing an upstream vendor must not silently change public field names, units, errors, or availability claims.
3. All provider requests **MUST** use finite timeouts, bounded concurrency, documented retry rules, and backoff with jitter for retryable failures. Respect `429` and `Retry-After` where sent. Do not automatically retry non-idempotent writes unless replay safety is established.
4. Credentials **MUST** be stored in secret management or environment configuration; never in source control, returned JSON, browser code, or logs. Use HTTPS where supported and legally appropriate.
5. An adapter **MUST** capture `source`, provider record ID, `observed_at` (when supplied), `received_at`, `source_updated_at` (when supplied), and `service_updated_at` where relevant. Missing source timestamps **MUST NOT** be fabricated. Convert times to UTC and units to the adopted public API conventions.
6. A provider's *publication cadence*, a service's *polling interval*, a contractual *freshness threshold*, and its *storage retention period* are four different concepts. Document and monitor them separately.
7. An integration **MUST NOT** imply data coverage beyond the feed's proven geography, included roads or facilities, supported service hours, or available fields. No data is not equivalent to zero incidents, no traffic, open parking spaces, or an on-time bus.
8. Retain cached or persisted data according to the service contract and provider licensing. Outages **MUST** preserve honest freshness/provenance indicators and use documented degraded modes, not invented live data.
9. External licensing, attribution, privacy obligations, redistribution restrictions and geographic restrictions **MUST** be reviewed before production ingestion and especially before sharing raw datasets with another service.
10. Monitoring **MUST** track request success, authentication failure, latency, upstream rate-limit events, provider timestamp lag, coverage changes, transformation failures and last successful refresh. S1's pub/sub ingestion **MUST** use a versioned internal event schema with deduplication and appropriate access controls.

### Integration boundary

```text
External API / feed / authorized first-party data
               |
       Service-owned adapter
       [validate, normalize,
        timestamp, provenance]
               |
     Service-owned storage/cache
               |
       S1–S7 domain logic
               |
    Adopted /v1/... API contract
```

For S1, an ingestion component can publish normalized observations to an approved message broker, and S1 consumes them. No external vendor is presumed to offer a native pub/sub feed merely because the *internal architecture* uses pub/sub.

## 3. Consolidated provider and dependency register

| Service | Source or dependency | Data / purpose | Publicly documented access | Authentication & limits | Selection status |
|---|---|---|---|---|---|
| S1 | TomTom Traffic Flow and Routing APIs | Road speeds, free-flow comparisons, route travel times and delays | HTTPS REST / JSON | Vendor API key; quota and plan to verify | Published / candidate |
| S1 | Ontario 511 | Provincial road events, closures and construction as supplemental context | HTTPS REST / JSON | Developer key; documented throttle **10 calls per 60 seconds** | Published / candidate |
| S2 | TomTom Traffic Stats | Historical area/route speed and travel-time statistics for training/analysis | API/batch analysis and downloads; some jobs asynchronous | Account/API key, evaluation/paid rights; quota and export restrictions to verify | Published / candidate |
| S2 | City of London Open Data | Potential historical municipal transportation context | Portal downloads / dataset-specific services | Dataset-specific; precise coverage and rights unconfirmed | Potential source / access unconfirmed |
| S3 | Ontario 511 events and construction | Provincial traffic-event/roadwork context | HTTPS REST / JSON | Developer key; **10 calls per 60 seconds** aggregate documented throttle | Published / candidate |
| S3 | City of London Renew construction map | Local planned/current construction context | Official map/website; machine-readable feed **not confirmed** | Automated reuse and terms unconfirmed | Potential source / access unconfirmed |
| S3 | Users and moderators of the platform | Reports, corrections, withdrawals, resolution status | First-party authenticated S3 API | Platform identity/role system required | First-party / planned |
| S4 | City of London municipal parking information | Facility directory, published prices, payment conditions | Official published web information; supported machine feed **not confirmed** | Reuse rights, change detection and feed access unconfirmed | Potential source / access unconfirmed |
| S4 | Facility operators / authorized occupancy feed | Real-time space counts or occupancy observations | Contracted feed, authorized operator data, or permitted observations | Provider agreement and format unconfirmed | Unavailable / unverified |
| S5 | ECCC / MSC GeoMet OGC API | Canadian weather, AQHI observations/forecasts and geospatial data | Public OGC API services | Public API published; use policy / fair use to check; no fixed quota assumed | Published / candidate |
| S5 | Open-Meteo | Weather forecasts, historical weather and modeled air-quality concentrations | HTTPS GET / JSON | Free non-commercial access states no key and **up to 10,000 calls/day**; commercial use requires appropriate plan | Published / candidate |
| S6 | London Transit Commission (LTC) GTFS Schedule | Routes, stops, trips, calendars and scheduled arrivals | Official GTFS ZIP download | Public link published; terms apply; request caps not stated on page | Published / candidate |
| S6 | LTC GTFS-Realtime | Trip updates, service alerts, vehicle positions | Official GTFS-RT `.pb` and `.json` endpoints | Public feed links published; access/refresh policy and occupancy fields to verify | Published / candidate |
| S7 | First-party user-reported observations | Station-level reported prices and timestamps | Authenticated S7 submissions per API contract | Platform user authentication, moderation and idempotency | First-party / planned |
| S7 | Authorized station catalog and optional licensed price data | Station metadata and/or independent price observations | **Not selected**; specific interface subject to authorization and contract | Unknown until source is selected; do not assume a public feed | Unavailable / unverified |
| S7 | Official aggregate fuel-price statistics (optional reference) | Regional historic trends, **not** station-level quotes | Published aggregate data; reuse/API method to verify | Terms and machine-feed access unconfirmed | Potential source / access unconfirmed |

**Interpretation:** “published” verifies the existence of documented material at the cited provider, **not** live connectivity from this project, blanket usage rights, or data fitness for every PR1 requirement. Limits above are only reported where a provider publishes a concrete number; **unknown means unknown**.

## 4. S1 — Live Traffic Analytics

**Required inputs:** current road-segment speeds, free-flow/baseline travel speeds, geospatial segment location or geometry, route travel times and delays, provider timestamps, and confidence/coverage information when supplied. PR1 S1 QR4 requires location observations **received by the platform within the last 60 seconds** to underpin live calculations.

**Candidates.** TomTom documents a Traffic Flow segment service with current speed, free-flow speed, current travel time and free-flow travel time. Its Routing service calculates traffic-aware routes and returns distance, travel time and traffic delay. Vendor API keys are required; the available product version and paid/evaluation entitlement must be confirmed before implementation. Ontario 511 offers events and roadwork context but **is not a substitute for segment-level speed observations**.

**Access and cadence.** Treat TomTom as an authenticated HTTPS/JSON candidate; contract-specific rate limits and update latency remain unverified. Treat Ontario 511 as a separate developer-key feed with a documented throttle of 10 calls per 60 seconds; that throttle is **not** a statement that observations refresh every six seconds. The platform ingestion service should schedule queries according to verified quotas, normalize records and publish events to S1's internal bus. Choose topic, version, broker, delivery guarantees and deduplication keys during implementation design.

**Risks:** paid access, street coverage outside major roads, road/segment ID mismatches, cached but aged provider observations, different free-flow baselines, and quota exhaustion. A newly *received* but already old provider observation should not be misrepresented as a current measurement.

**Fallback:** continue serving last acceptable cached congestion results with `is_live: false`, `is_stale: true`, original source time and `data_status: cached`; never recompute a supposedly live estimate from stale inputs. Without usable coverage or a cached result, use the applicable standard error response; distinguish no matching segment (`200`, empty result) from provider failure.

**Acceptance gate:** verify example roads and at least two routes in the intended service area; measure provider observation lag and service receipt lag separately; confirm route-level travel-time mapping and rights to store derived outputs; measure sustained refresh under actual quota.

## 5. S2 — Traffic Predictor

**Required inputs:** reproducible historical traffic speed/travel-time observations or aggregates, road/network identifiers, location and time-of-day/day-of-week coverage, dataset acquisition period and provenance; optional external event or weather features require separately documented access/feature lineage.

**Candidate.** TomTom Traffic Stats offers historical route/area analysis of speeds and travel times, including API and batch modes. Some jobs are asynchronous and result in downloadable reports. An evaluation offering is documented, but an evaluation account is **not** an approved permanent data license. Municipal datasets may provide contextual features but should not be treated as equivalent to full historical roadway time series without inspecting their contents.

**Access and cadence.** Provider account, API key or export entitlement; job and download workflows where required. The actual training-data refresh schedule, history window, location coverage, per-operation quota and storage/redistribution rights require confirmation. Periodically load approved datasets into S2-owned historical storage, normalize UTC times and locations, and record `dataset_updated_at`, `training_data_end_at`, `model_version`, and refresh deadlines.

**Architectural consequence:** PR1's **3-second prediction response target** and **25 concurrent prediction requests** must be satisfied by precomputed/locally served patterns and a deployed prediction model. S2 **MUST NOT** wait for a historical-data export job on every prediction request. Macro-F1 ≥ 0.70 on chronologically held-out data is the adopted S2 design acceptance threshold; actual attainment has not been demonstrated.

**Risks:** training/serving region mismatch, historical licensing, incomplete time-of-day coverage, class imbalance, calendar/season shifts, data leakage, export delays and poor model generalization.

**Fallback:** use the last approved, valid model and its authorized historical dataset while within the contract's validity window; mark stale historical patterns honestly. Once the model validity period has lapsed and no validated alternative exists, do not fabricate predictions—return documented unavailability.

**Acceptance gate:** obtain representative historical samples with explicit date ranges, prove that target congestion classes can be produced from available fields, record an allowed storage/retraining license, run chronological evaluation, and demonstrate production-style latency independently of batch collection.

## 6. S3 — Construction & Incident Reporting

**Required inputs:** first-party user reports and moderation actions; optional official roadwork/event feeds supply contextual records and may support duplicate detection or report enrichment, but must not silently overwrite submitted reports.

**Candidates.** Ontario 511 documents events and construction APIs with event identifiers, road descriptions, event type, coordinates, and published/report update times; access uses a developer key. Its coverage is focused on reported Ontario transportation information and may not enumerate every neighbourhood event. The City of London's Renew map publishes municipal construction information, but **a supported machine-readable API and automated redistribution rights have not been verified**.

**Access and cadence.** Ontario 511: REST/JSON and the documented 10-calls/60-seconds throttle; provider refresh frequency unknown. City map: official human-readable display; do not assume scraping or a GIS endpoint is authorized. User reports: platform S3 authenticated write operations, with owner/moderator authorization and durable persistence.

**Risks:** duplicated events with different IDs, overlapping city/provincial reporting, incomplete neighbourhood coverage, stale planned end dates, corrections, and privacy issues in user reports.

**Fallback:** S3-owned durable reports remain available and modifiable when official feeds fail. Mark official-source records as delayed/unknown, preserve provider timestamps, keep user and official records in distinct provenance classes, and do not invent “no incidents” from missing feeds.

**Acceptance gate:** review an Ontario 511 sample for local and provincial coverage; establish source-to-report mapping and deduplication policy; obtain explicit confirmation for any city-data automation; verify user-authentication and moderator-role dependency.

## 7. S4 — Parking Analytics

**Required inputs:** parking facility locations and identifiers, capacities if available, current occupancy estimates/availability, prices and fees with time conditions, and distinct timestamps for occupancy observations versus prices. The adopted S4 contract treats occupancy as fresh up to **300 seconds** and pricing metadata as fresh up to **24 hours**.

**Candidate.** The City of London publishes a municipal parking-lot information page listing facility locations and rates. This provides a useful **human-verifiable source for the municipal subset**, but a stable API, coverage of private garages, permission for automated extraction and timely updates have **not** been confirmed. A live occupancy supplier or authorized observation feed remains unverified. Historical/demo fixtures are test data, not proof of real-time capacity.

**Access and cadence.** Confirm whether a city dataset, operator agreement or maintained approved import is available; do not assume the official HTML page is a supported data API. Site rate changes need monitored change control if a 24-hour pricing freshness claim is to be made. For occupancy, document permitted collection channel, timestamp reliability, update cadence, operator coverage, possible inference errors and any restrictions on individual device/location data.

**Risks:** price conditions differ by time/day, garage operators differ from municipality, total capacity may be unknown, missing live feed, estimated occupancy is not confirmed vacancy, and legal/privacy issues with trace-based observations.

**Fallback:** return persisted facility details and price information separately from occupancy. If occupancy is unverified or too old, mark it `cached`/`unavailable`; **do not claim spaces are open**. If rates exceed the contract's freshness threshold, mark them stale even if the published page has not changed from the team's last manual review.

**Acceptance gate:** create an approved facility coverage list, confirm machine-readable access and reuse rights for pricing, establish update monitoring, identify a lawful live occupancy source or explicitly scope the first version to estimated/unavailable occupancy, and demonstrate provenance separation for rates/observations.

## 8. S5 — Weather & Air Quality

**Required inputs:** weather observations, forecast issuance/validity, air-quality measurements or Canadian AQHI, historical environmental trends, and timestamps sufficient to generate weather-based travel guidance. The adopted S5 thresholds are: current observations **30 minutes**, air quality **60 minutes**, and forecast issue age **6 hours**.

**Candidates.** Environment and Climate Change Canada's Meteorological Service of Canada exposes GeoMet OGC web APIs with publicly listed weather/climate collections and AQHI observation and forecast collections. Open-Meteo offers weather forecast, historical reanalysis and modeled air-quality products. Open-Meteo's air-quality global models are **not automatically equivalent to measured, local Canadian AQHI**; do not relabel a US/EU AQI or model estimate as `ca_aqhi`.

**Access and cadence.** ECCC: publicly documented OGC API, collection-specific query formats and usage policy; no universal numeric request quota is asserted here. Open-Meteo: published public GET/JSON endpoints without API keys for qualifying non-commercial use; site states up to **10,000 daily requests** in that category, with subscription/terms changes for commercial use. Weather model data cadence is model-specific, and ECCC AQHI station cadence varies by dataset/collection; do not assert they meet the adopted freshness thresholds until measuring their actual timestamp lag.

**Risks:** model grid approximations versus station readings, missing pollutants, unit conversions (µg/m³, ppb, ppm), source updates slower than polling, provider terms/attribution, changes to collection IDs, and geographic coverage gaps.

**Fallback:** serve last available weather observations as explicitly stale when the provider is unavailable, as required by PR1 S5 QR3; expose independent air-quality availability. Derived travel precautions must identify insufficient evidence rather than classify missing data as safe. Historical series are labeled historical, not “live.”

**Acceptance gate:** choose a weather observation source, a forecast source and an air-quality/AQHI source independently; verify geographic coverage and actual observation timestamps for the intended area; test unit conversions against documented example records; check 30/60-minute and 6-hour conditions under normal and outage simulations.

## 9. S6 — Public Transit Analysis

**Required inputs:** authoritative route/stop/trip/service calendars and stop times; optional real-time trip updates, service alerts, vehicle locations, occupancy/crowding if explicitly supported, with feed timestamps. PR1 requires distinguishing live transit updates older than **60 seconds**, preserving schedules without live data, and meeting **1,500 ms** arrival-response targets under **50 concurrent requests**.

**Strong candidate.** London Transit Commission's official Open Data page publishes a **GTFS Schedule ZIP** and links to **GTFS-Realtime Trip Updates, Service Alerts and Vehicle Positions** (both Protocol Buffers and JSON). This is a concrete, relevant feed family for a London-area pilot. LTC's published open-data terms allow reuse subject to their conditions. The web page **does not establish passenger occupancy availability**, a guaranteed live-feed update interval, service-level uptime, or a per-minute request quota.

**Access and cadence.** Download GTFS schedules on an approved interval based on feed validity / version dates (not on the 60-second live threshold). Poll GTFS-RT feeds conservatively, using HTTP metadata and provider guidance if supplied; schedule the actual poll period only after observing timestamps and checking traffic policy. The public page provides feed URLs; no key requirement is stated there, but successful machine access and stable endpoint policies still require a connectivity test.

**Risks:** mismatched GTFS static and realtime trip IDs, calendar exceptions, holiday schedule changes, vehicle positions without useful arrival updates, time zones and daylight saving, alerts without structured closures, no occupancy values, and unknown update latency.

**Fallback:** S6 MUST continue returning valid published scheduled arrivals with `basis: schedule` when the real-time feed is missing or stale. Set `live_update_status: stale` or `unavailable` and avoid presenting old `estimated_at` values as current. Delay, disruption, and occupancy coverage can each be independently partial or unavailable.

**Acceptance gate:** download and parse the current schedule, verify current service calendar, map a sample trip update to a scheduled trip, measure update ages and request limits, test a disabled live feed, and explicitly determine whether passenger crowding information exists before committing to FR4 implementation.

## 10. S7 — Gasoline Price Analytics

**Required inputs:** an authorized and normalized station catalog (station identifiers, locations and known fuel categories), authenticated first-party submitted price reports and their acceptance timestamps, optional independent prices only under appropriate source authorization, and source-level provenance for any comparison. The adopted S7 contract marks user-reported prices stale after **24 hours**; that does not establish their correctness.

**Sources and status.** The initial reliable application data path is **first-party user submissions**, recorded in S7-owned durable storage with server-side timestamps, authenticated reporter ownership, validation and moderation. An independent station metadata source and externally verified station-level price feed have **not been selected or confirmed**. Published regional aggregate fuel-price trends, where available, are contextual only: they cannot be substituted for individual station prices or their timestamps.

**Access and cadence.** S7 receives its first-party reports over its own documented API. Report frequency is user-dependent; no continuous updates are guaranteed. For any future authorized external supplier, record exact data entitlement, geography, access protocol, credentials, update cadence, quotas, permitted uses, provenance, and rules for mixing supplier records with first-party reports before integration. No such feed, quota or credential is presumed here.

**Risks:** stale or inaccurate user reports, incomplete station catalog, unavailable individual prices, inconsistent fuel-grade mapping, duplicate submissions, and incorrectly equating a recent report with a confirmed live observation.

**Fallback:** persist and return historical reports with honest `source: user_report`, `is_live: false`, and age-derived `is_stale`; omit unknown price quotes rather than displaying zero. When station directory coverage is unavailable, return the documented unavailable error instead of claiming the geographic area contains no stations.

**Acceptance gate:** validate durable report persistence, authentication and idempotency, a legitimately authorized station catalog, explicit unverifiable/unknown-source flags, and separate price/source timestamps. No unverified upstream price feed is required for the first-party reporting contract.

## 11. Cross-service dependencies and ownership

| Consumer | Information needed | Approved communication pattern | Ownership and safeguards |
|---|---|---|---|
| S1 | Traffic ingestion observations | Versioned internal pub/sub or service-owned ingestion adapter | S1 owns normalized active observations/results; no raw private location stream is implicitly public |
| S2 | Historical traffic and optional aggregated S1 observations | Approved historical provider pipeline; versioned, permissioned S1 export/events if allowed | S2 owns derived historical/model datasets; respect S1/provider redistribution and privacy restrictions |
| S3 | External events plus first-party reports | S3-only adapters and authenticated report API | S3 owns report lifecycle; external records carry distinct IDs and provenance |
| S4 | Facility/pricing data and permitted occupancy inputs | Operator/city ingestion adapter or authorized first-party observations | S4 owns normalized facilities/caches, not Registry or S3 storage |
| S5 | Weather, forecast and AQHI/pollutant feeds | S5-owned provider adapters | S5 normalizes units and source timestamps before exposing data |
| S6 | Static and real-time transit feeds | GTFS/GTFS-RT ingestion into S6-owned cache/database | S6 alone owns normalized stop/trip and derived live result representations |
| S7 | First-party reports and any authorized catalog | S7 API plus explicitly permitted provider adapter | S7 owns station catalog and durable reports; never treat a report as provider-verified |

Cross-service consumption **MUST** use documented APIs/events, not direct writes to another service's storage. Private first-party user/location records **MUST NOT** be transferred to another service merely because they could improve a prediction. Adopt data minimization, explicit permission, and aggregation where appropriate.

## 12. Provider onboarding and verification checklist

A provider moves to **Integrated / approved** only when a signed-off integration record includes:

- [ ] Identity: provider, product/feed, owner, official documentation URL, version, geographic/service-area coverage.
- [ ] Entitlement: permitted uses, attribution, storage, caching, redistribution, commercial limits and relevant privacy review.
- [ ] Access: protocol, base URL/format, connection test, credential owner/rotation, TLS requirements.
- [ ] Data mapping: sample payload, normalized fields, identifiers, timezones, units, null semantics and validation strategy.
- [ ] Timeliness: **source publication frequency**, measured actual source lag, **platform poll/subscription cadence**, contract freshness threshold and historical retention documented separately.
- [ ] Limits: provider quota and burst limits (or explicit **unknown**), concurrency limits, expected request budget, `429` response handling.
- [ ] Reliability: timeout/retry/backoff, circuit breaker, source-health monitoring and degraded behavior exercised.
- [ ] Licensing/privacy: rights to cache, derive, redistribute, use in model training, and retain after termination established.
- [ ] Tests: coverage samples in intended geography, malformed/empty responses, late timestamps, partial feeds and long outage.
- [ ] Approval: integration owner, verification date, product/API version and evidence recorded in the team tracker.

### Recommended verification record template

```yaml
provider_id: example_provider
service_id: sX
status: candidate              # candidate | approved | integrated | rejected
product_or_feed: pending
source_documentation_url: pending
coverage: unverified
protocol_and_format: unverified
credentials_required: unknown
request_limit: unknown
source_update_interval: unknown
platform_refresh_interval: undecided
contract_freshness_threshold: service_contract
source_timestamp_fields: unverified
storage_and_redistribution_rights: unverified
sample_payload_tested: false
outage_fallback: service_contract
approved_by: null
verified_at: null
```

This record is a **template**, not evidence that `example_provider` exists.

## 13. Priority decisions before implementation

| Priority | Service(s) | Decision / blocker | Completion evidence |
|---|---|---|---|
| P0 | All | Confirm supported pilot geography and source/license approval process | Written scope, owners and provider approval checklist adopted |
| P0 | S1 | Choose traffic source and prove < 60-second platform receipt freshness **without confusing it with provider observation age** | Measured provider sample, quota, latency, internal ingestion-event design |
| P0 | S2 | Acquire usable, legally retainable historical traffic dataset and map classes | Historical sample, coverage check and evaluation rights confirmed |
| P0 | S3 | Decide which official records can be ingested and how they coexist with user reports | Feed coverage review and source-identity/deduplication rules |
| P0 | S4 | Find authorized current occupancy data or explicitly mark unsupported coverage | Operator approval or documented initial unavailable/estimated data behavior |
| P0 | S5 | Verify Canadian AQHI versus modeled pollutant products and meet freshness targets when possible | Timestamp and unit conversion test samples |
| P0 | S6 | Confirm current LTC GTFS/GTFS-RT quality and occupancy coverage | Parsed schedule, matched trip update, measured update age, field-coverage report |
| P0 | S7 | Confirm authorized station catalog and first-party reporting pipeline | Approved catalog provenance and durable submissions test |
| P1 | S2 | Validate predictive quality threshold and model lifecycle | Chronological evaluation and 25-concurrent-request benchmark |
| P1 | All | Create production provider-health dashboards and fallback tests | Automated source-lag, rate-limit and outage conformance reports |

No source is described as integrated until these checks are performed. An integration that cannot satisfy a hard PR1 requirement **MUST** generate a tracked gap or scope decision, not a false assertion of compliance.

## 14. T008 external dependency acceptance checklist

- [x] Every service S1–S7 has at least one first-party, candidate or explicitly missing input dependency recorded.
- [x] Provider/dataset and retrieved data categories are documented for each service.
- [x] API/feed access methods are identified where published; unverified methods are marked unknown.
- [x] Authentication and concrete provider throttles are recorded where documented; other limits remain explicitly unknown.
- [x] Update cadence is distinguished from project polling, freshness and retention; unverified cadence is marked unknown.
- [x] Service-specific risks and fallback behavior are described.
- [x] Published candidates are distinguished from actually integrated/approved providers.
- [ ] Before deployment: confirm credentials, terms, dataset rights, coverage and live update behavior for selected sources.
- [ ] Before claiming full PR1 functionality: prove access to the required live, historical, occupancy or station input for each affected service.

This document completes the **design-stage dependency inventory**, not real-world provider integration or license acceptance.

## 15. Research sources and verification links

Official vendor, public agency and transit-operator documentation consulted on **2026-11-11**. The links are evidence for published products, feeds, or data descriptions; they do not confirm successful integration, continuous availability, or commercial rights beyond the published terms.

**Traffic and roadworks**

1. TomTom — Traffic Flow Segment Data: <https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data>
2. TomTom — Routing, Calculate Route: <https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/calculate-route>
3. TomTom — Traffic Stats: <https://docs.tomtom.com/traffic-stats/documentation/product-information/introduction>
4. Ontario 511 — Developer API: <https://511on.ca/developers/doc>
5. Ontario 511 — Events feed schema: <https://511on.ca/help/endpoint/event>
6. Ontario 511 — Developer terms: <https://511on.ca/developers/resources>
7. City of London — Road construction / Renew map: <https://london.ca/living-london/roads-sidewalks-transportation/road-construction>
8. City of London — Open Data Portal: <https://opendata.london.ca/>

**Parking**

9. City of London — Municipal parking lots and rates: <https://london.ca/living-london/parking/parking-lot-information>

**Weather and air quality**

10. ECCC / MSC — GeoMet API portal: <https://api.weather.gc.ca/>
11. ECCC / MSC — OGC API technical documentation and AQHI collections: <https://eccc-msc.github.io/open-data/msc-geomet/ogc_api_en/>
12. ECCC / MSC — AQHI open data description: <https://eccc-msc.github.io/open-data/msc-data/aqhi/readme_aqhi_en/>
13. Open-Meteo — Weather forecast and terms summary: <https://open-meteo.com/>
14. Open-Meteo — Air Quality API (including modeled source data): <https://open-meteo.com/en/docs/air-quality-api>
15. Open-Meteo — Historical Weather API: <https://open-meteo.com/en/docs/historical-weather-api>

**Transit**

16. London Transit Commission — Open Data, GTFS/GTFS-Realtime links: <https://www.londontransit.ca/open-data/>
17. LTC — Open Data Terms of Use: <https://www.londontransit.ca/open-data/ltcs-open-data-terms-of-use/>

**S7**

18. Any optional external station-level data provider remains unselected; its eventual source documentation, legal authorization and technical restrictions must be appended to this register only after approval.

---

**Document control:** Changes to an approved source, input capability, timestamp meaning, freshness threshold, legal entitlement or fallback strategy MUST be reviewed against the common standards, applicable S1–S7 OpenAPI schema, and service data ownership rules. New provider selections require a dated verification record rather than only a document edit.
