# Smart City Platform — Service Data Ownership and Storage Architecture

| Document property | Value |
|---|---|
| Document ID | `T008-DATA-OWN` |
| Version | `1.0` |
| Status | **Adopted data-architecture design baseline**; provider agreements, retention schedules, and operational provisioning remain implementation prerequisites |
| Applies to | S1–S7 domain microservices, their owned data stores, caches, and interservice data exchanges |
| Governing standards | `docs/api/API-Standards.md` |
| Related contracts | `docs/api/contracts/S1-Live-Traffic.md` through `S7-Gasoline-Analytics.md`, and corresponding files in `shared/api-contracts/` |
| Requirement basis | PR1 workbook: `Project Task` T008; `Service Requirements` S1–S7; ASR6 (fault isolation) |
| Intended repository path | `docs/architecture/Service-Data-Ownership.md` |

## 1. Purpose, scope, and terminology

This document establishes **who owns each category of data**, the minimum logical data model, where that information is stored or cached, how it is exchanged, and what must survive restart or provider outages. It is **prescriptive**: service implementations must conform to it. The document does not describe existing source code or claim that a proposed database or external feed has already been deployed.

The seven service-specific API contracts govern public resource shapes, identifiers, permissions, and response semantics. This document governs the underlying ownership and lifecycle of those resources. A change that affects an API response must be reflected in both documents before implementation. The companion `External-Data-Dependencies.md` will identify particular third-party providers, licenses, access methods, and feed update rates; **none is implicitly approved here**.

Terms used below:

- **Data owner:** The one domain service responsible for the authoritative *platform-managed* record, its mutations, validation, lifecycle, and publication. An external provider may still be the authoritative origin of a measurement or timetable.
- **Source data:** Measurements, reports, schedules, or other records received from users or external feeds, with retained provenance.
- **Normalized data:** Source data mapped to the platform's stable identifiers, types, units, and timestamps.
- **Derived data:** Computations, summaries, estimates, forecasts, or projections based on other records; these are not independent external observations.
- **Persistent store:** Durable storage expected to survive an application process or container restart.
- **Cache:** A replaceable or refreshable copy or derived projection used to serve requests efficiently. A cache is not a substitute for durability where PR1 requires persistent accepted submissions.
- **Freshness threshold:** Age after which information must be identified as stale. **Not** a retention duration, deletion time, or proof of accuracy.

## 2. Mandatory ownership principles

1. **Single domain owner.** Every platform-managed domain entity MUST have one designated owning service. Other services MAY hold read-only derived copies under a documented contract, but MUST NOT modify the owner's private store.
2. **Database isolation (ASR6).** S1–S7 MUST NOT share a universally writable domain database or use the Service Registry's PostgreSQL database for domain data. Separate databases or tightly isolated schemas, users, and grants MAY reside on the same managed database infrastructure, provided service credentials cannot read/write another service's private tables. Independent failure recovery, migrations, and deployment remain required.
3. **Explicit contracts at boundaries.** Cross-service access MUST occur through a documented, versioned API, approved published event, or separately defined export contract. Database joins across service boundaries are prohibited.
4. **Origin is not ownership.** A provider retains responsibility for the content it publishes; the consuming service owns the platform's normalized, provenance-labeled representation. A user-submitted observation MUST NOT be mislabeled as externally verified.
5. **Preserve lineage.** Source identifiers, relevant observation/receipt/ingestion/update timestamps, transformation/model versions, and data quality must be recorded where needed for the served result. Unknown source timestamps remain unknown; they must not be fabricated.
6. **Protect sensitive data.** User and device identifiers, authentication claims, precise movement observations, and private report ownership/audit information MUST NOT be exposed in public analytics responses. Store only what the contracted operation requires, using minimum practical access and retention.
7. **Correct failure behavior.** Each service MUST define which persisted data or caches permit honest degraded responses during a feed failure. No service may convert missing data into zero or present a cached/simulated/historical result as fresh and live.
8. **Durability before acknowledgment.** For S3 and S7 submissions, a successful `201 Created` MUST mean the accepted source report and necessary ownership, audit/idempotency metadata have been durably committed as specified by their API contracts.
9. **No inferred integrations.** References to candidate historical-traffic, parking, weather, transit, municipal, or fuel-price feeds do not authorize their use. Provider selection, licensing, geographical coverage, credentials, quotas, and retention rights belong in the external-dependency specification.

## 3. Consolidated data ownership matrix

The **owned** column is authoritative for platform-managed data. “Consumed” never grants direct database access. Technology entries are the intended implementation design, not a claim of deployed infrastructure.

| Owner | Data owned and authoritative platform representation | Consumes but does not own at source | Persisted data | Temporary / derived cache | Storage architecture |
|---|---|---|---|---|---|
| **S1 — Live Traffic** | Normalized recent road-segment observations, congestion snapshots, route/area estimates and calculation metadata | Approved live traffic/location ingestion feed; road-network/routing data | Optional bounded aggregated snapshots needed for recovery; **no required long-term personal movement history** | Recent qualified observations and congestion/route results | S1-isolated shared runtime cache/event consumer; optional isolated durable aggregate store |
| **S2 — Traffic Predictor** | Historical traffic datasets, normalized features, recurring patterns, model artifacts/versions, evaluation results and prediction projections | Approved historical traffic dataset; optional explicitly published S1 export | Historical aggregates, model artifacts, training metadata, evaluation evidence | Recent predictions and precomputed patterns | S2-owned relational/time-series data + object/artifact storage; optional cache |
| **S3 — Incidents** | Incident reports, private owner references, status history, audit events, idempotency records | Verified user identity/roles; optional municipal incident feeds | **Required:** accepted reports, ownership, audit/history and 24-hour active idempotency records | Geographic query indexes and read caches | S3-owned PostgreSQL + PostGIS (selected design) |
| **S4 — Parking** | Normalized facility catalog, geofences, capacity/status, tariffs/fees, computed occupancy snapshots | Facility/rate providers; approved occupancy observations | Facility catalog and normalized rates; bounded aggregated occupancy history when justified | Latest occupancy estimates and source freshness metadata | S4-owned PostgreSQL + PostGIS; bounded cache (selected design) |
| **S5 — Weather & Air Quality** | Normalized weather observations, forecast runs/intervals, air-quality measurements, trend buckets, derived guidance | Weather, forecast and air-quality providers; optional travel alerts | Latest usable cached-source records and licensed historical aggregates | Location/time-indexed current readings, forecasts and guidance | S5-owned durable normalized store and bounded cache (PostgreSQL is selected default) |
| **S6 — Public Transit** | Normalized agencies, stops, routes, trips, calendars and timetables; live-arrival projections, disruptions and occupancy states | Approved schedule/live update/alert/occupancy feeds | **Required:** usable schedule dataset for live-feed outage fallback | Time-stamped arrivals, delays, disruptions and occupancy | S6-owned PostgreSQL (PostGIS for spatial lookup) + bounded live cache |
| **S7 — Gasoline Prices** | Station catalog, accepted price reports, report provenance/ownership, current-price projection, idempotency records | Station directory; user price reports; optional separately approved price feed | **Required:** station catalog, accepted price reports, ownership, projection rebuild inputs and 24-hour active idempotency records | Latest displayed station/fuel price summaries and comparisons | S7-owned PostgreSQL + PostGIS (selected design) |
| **Service Registry (infrastructure)** | Service instance registration, health/lease/discovery metadata | Registration/heartbeat messages from domain services | Registry-owned registration store | Discovery/health lookup data | Registry-owned PostgreSQL; **outside S1–S7 domain storage** |

A service may use a managed infrastructure cluster shared with other services **only** if logical data ownership, isolation, credentials, migrations, and operational failure boundaries are preserved. Shared physical hosting is not permission for cross-service SQL access.

## 4. Service entity models, attributes, and relationships

The following schemas describe **logical entities**, not mandatory table names. Types follow the API standards: opaque IDs, finite WGS 84 coordinates, numeric values with explicit units, and UTC instants. Implementations MAY split or denormalize entities internally while preserving these meanings and boundaries.

### 4.1 S1 — Live Traffic Analytics

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `RoadSegmentReference` | `road_segment_id`, geographic reference, map/source version | Identifies the segment to which observations and estimates refer; road-network provider may own original map geometry |
| `TrafficObservation` | segment or location reference, derived traffic measurement, `observed_at` if supplied, trustworthy `received_at`, `source` | Many observations may contribute to one road segment; bounded transient input for live analytics |
| `CongestionSnapshot` | `road_segment_id`, congestion level, computed metrics, `as_of`, `service_updated_at`, `source`, `is_stale`, `is_live` | Current computed state for a segment; updated or invalidated after eligible observations |
| `RouteCongestionResult` | request/route geometry reference, `baseline_duration_s`, `estimated_duration_s`, `estimated_delay_s`, snapshot/model version, `as_of` | Derived from one or more segment snapshots; replaceable cache |
| `AreaCongestionResult` | query region, included segment references, aggregate congestion measures, `as_of` | Derived geographic view; replaceable cache |

**Ownership and retention:** S1 owns normalized analytics and the records necessary to substantiate those results, **not** a permanent archive of individual user movements. Inputs can be discarded when no longer needed for the approved live computation. For live results, only observations with `received_at` within the preceding **60 seconds** may contribute (S1 QR4). This calculation eligibility rule does **not** prescribe a 60-second database TTL. Any raw-location storage beyond transient processing requires its own privacy, consent, and retention decision. Cache versions must be invalidated or refreshed after relevant new observations arrive.

**Dependency and failure boundary:** An ingestion service and message broker may carry observations to S1. Transport topics, event shape, duplicate handling, and transport retention require a separate internal event contract. Outdated computed results may be served only with the stale/cached indicators authorized by the S1 contract; missing usable data must produce a documented unavailable result, not invented congestion.

### 4.2 S2 — Traffic Predictor

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `HistoricalTrafficRecord` | record/segment or area reference, measurement interval, observed/received times when available, congestion metrics, source/dataset version | Many records constitute a dated, licensed historical dataset |
| `RecurringTrafficPattern` | region, day-of-week/time-of-day bucket, named local timezone, derived congestion category/statistics, source coverage | Derived from a known historical dataset; retains aggregation/algorithm version |
| `TrainingDatasetVersion` | dataset ID/version, data interval, extraction/feature version, training cutoff, provenance | Referenced by one or more model artifacts; must support reproducible evaluation |
| `PredictionModelArtifact` | `model_version`, `training_data_end_at`, `model_valid_until`, artifact location/checksum, configuration | Approved model used to produce forecasts; versioned immutable artifact |
| `ModelEvaluation` | model/dataset versions, evaluation interval, macro-F1, per-class metrics, pass/fail record | Evidence for S2 QR3 and model deployment eligibility |
| `TrafficPrediction` | query location/region, forecast instant, predicted congestion category, `model_version`, `as_of`, source dataset version | Derived forecast; may be cached; **not** a measured observation |

**Relationships:** one dataset version may support multiple derived patterns and model versions; each model may produce many predictions; each model must have evaluation evidence. S2 owns its approved imported dataset **copy**, not the original external provider dataset. A future S1-to-S2 feed is allowed only through an explicitly specified export/API/event interface; direct reads from S1 private tables are forbidden.

**Persistence:** Historical aggregates, approved model artifacts, training metadata, and evaluation results must survive restarts and allow model/result lineage. Cached predictions may be discarded and recomputed. PR1 defines no universal history-retention period or training cadence. Keep versions for the documented reproducibility/audit window and under the source's licensing terms; the exact duration is an outstanding deployment policy. Expired/unapproved models cannot silently serve predictions. The S2 contract's initial eligibility rule is **macro-F1 ≥ 0.70** on the specified held-out historical evaluation.

### 4.3 S3 — Construction & Incident Reporting

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `IncidentReport` | `incident_id` (UUID), `incident_type`, WGS 84 location, description, status, optional `observed_at`, server `submitted_at`/`updated_at`, **private** `owner_principal_id` | One verified user owns each accepted report; supports active/resolved/completed/inaccurate/withdrawn lifecycle |
| `IncidentAuditEvent` | event ID, `incident_id`, private verified actor reference, action, prior/new status, server event time, change metadata | One incident has zero or more audit events; owner/moderator operations recorded privately |
| `IncidentSubmissionIdempotency` | verified user scope, operation, key, request fingerprint, original response/report ID, expiration time | Ensures repeated create requests with the same key are handled consistently for **24 hours** |

**Relationships:** one report has one verified owner reference, many audit events, and may be linked to an idempotency record. User credentials and the full user account belong to the platform identity authority, **not** S3. An incident report records an asserted occurrence; its content is not automatically externally verified.

**Persistence and access:** An accepted report, private ownership reference and relevant audit metadata MUST be durably committed before a `201` response. Report updates/withdrawals are authorized for the verified owner or moderator and commit atomically. Withdrawal is a **soft deletion**: the status becomes `withdrawn`, public GETs stop revealing the report, and private records remain subject to an approved retention/deletion policy. The contract's **7-day `is_stale` flag for active reports** is a review-age indicator, not an automatic withdrawal or database deletion deadline. Configure database-level geographical indexing and separate private ownership/audit storage privileges. A formal retention/privacy schedule must be approved before production collection of user-submitted personal information.

### 4.4 S4 — Parking Analytics

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `ParkingFacility` | `facility_id`, name/location, geometry/geofence, capacity, operational status, catalog source/update time | One facility has zero or many rates and occupancy snapshots |
| `ParkingRate` / `ParkingFee` | facility ID, tariff/fee type and conditions, numeric amount, ISO 4217 currency, effective period and `source_updated_at` when available | One facility has a versioned set of rate/fee conditions; provider remains original tariff authority |
| `OccupancySnapshot` | facility ID, capacity basis, occupied/available estimate where known, algorithm/source version, `as_of`, freshness/provenance | Derived from approved observations; bounded aggregated history/cache |
| `TimeLocationObservation` (input only) | pseudonymous subject reference, coordinates, `observed_at` | May be supplied to restricted estimation; **not persisted by the HTTP estimation endpoint** |

**Persistence:** S4 must retain a usable normalized facility catalog and available rate/fee schedules. Occupancy snapshots are replaceable derived data; retaining aggregated history is optional and must be bounded. The public API must never expose pseudonymous subject movement records. Raw time-location inputs are **not** a default S4 database entity; any future ingestion workflow requires a separate explicit consent, access, and retention policy.

**Freshness:** Occupancy estimates are stale after **300 seconds**, while normalized prices/fees are stale after **24 hours** under the S4 contract. Both timestamps and flags must be independent. These thresholds do not delete source tariffs or the facility catalog. Lack of occupancy input must produce unknown/available-fallback semantics, not a false guarantee that spaces are free.

### 4.5 S5 — Weather & Air Quality

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `WeatherObservation` | coordinates/coverage, temperature/humidity/wind/precipitation/visibility values and units, `observed_at`, provider/provenance | Many observations over time for a covered location/region; latest usable copy supports outage fallback |
| `ForecastRun` | run ID, provider/source, `issued_at`, geographic coverage, model/run identifier | One run contains many forecast intervals; a forecast is not an observation |
| `ForecastInterval` | forecast run ID, start/end UTC instants, expected weather measurements/conditions | Child of a forecast run; must match the requested forecast horizon |
| `AirQualityObservation` | coordinates/coverage, pollutant concentrations + units, optional Canadian AQHI, `observed_at`, provenance | Separate feed/coverage/freshness from weather; one AQHI value does not replace pollutant readings |
| `EnvironmentalTrendBucket` | metric, location, time interval, aggregate/statistical value, sample count/coverage, dataset/source version | Derived historical aggregation, subject to provider rights |
| `TravelGuidanceResult` | query location/time, risk/assessment status, applicable rule-set version, source input references, `as_of` | Derived from weather/forecast/air-quality data; recomputable and not an independently observed fact |

**Persistence and caching:** S5 must retain its most recent usable source-backed weather observation and forecast run so an outage can return an honest cached result. Air-quality information has independent availability. Historical observations and trends can be persisted only to the extent permitted by the provider's license and approved retention policy. Guidance and location-specific query results are replaceable caches. Stale weather alone does not justify creating synthetic values or deleting last-known weather during provider downtime.

**Freshness:** Current weather **30 minutes from `observed_at`**; air-quality measurements **60 minutes from `observed_at`**; forecasts **6 hours from `issued_at`**. Historical trend intervals are marked historical rather than evaluated as if they were real-time observations. Guidance inherits the quality/freshness of its underlying inputs; insufficient evidence yields unknown risk rather than invented certainty.

### 4.6 S6 — Public Transit Analysis

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `TransitAgency` | stable normalized agency ID, name, coverage, provider/dataset version | One agency serves multiple routes |
| `TransitRoute` | route ID, agency ID, public name/number, mode, geometry/coverage as available | One route has trips and associated stops |
| `TransitStop` | stop ID, name, coordinates, accessible metadata when supplied | One stop participates in many trips/stop-time entries |
| `TransitTrip` / `ServiceCalendar` | trip ID, route ID, service ID/dates, direction, schedule version | A trip has many ordered scheduled stop times; service calendar controls applicable dates |
| `ScheduledStopTime` | trip ID, stop ID, stop sequence, scheduled arrival/departure times | Join between trip and stop for timetable lookup |
| `LiveTransitUpdate` | vehicle/trip/stop reference, source update time, predicted arrival/departure, last-live-update time, state | Short-lived normalized feed record; not a replacement for published schedules |
| `TransitDisruption` | disruption ID, affected route/stops/trips, nature/status, time window, source/update time | May affect many routes/stops; source-aware, possibly cached |
| `PassengerOccupancy` | trip/vehicle/route reference, qualitative level and optional count, observation/update timestamp, provenance | Optional feed with limited coverage; avoid storing individual passenger identities |

**Persistence:** S6 must persist a usable versioned normalized schedule dataset to answer stop/route/arrival requests **when live data fails**. Live-arrival updates, disruptions, and occupancy values are replaceable bounded caches; optionally retain non-identifying aggregates under a documented policy. Timetables are not stale merely because a real-time feed is absent, but they must have the applicable date/service calendar and source version.

**Freshness:** Known live updates are fresh through **60 seconds** and stale afterward; unknown live timestamps result in `live_update_status: unavailable`. During an outage, scheduled arrivals are still returned with `basis: schedule`; stale predictions must not be presented as current. Source data formats such as GTFS Schedule and GTFS Realtime are candidates, **not confirmed feed integrations**.

### 4.7 S7 — Gasoline Price Analytics

| Logical entity | Required core attributes | Relationship and lifecycle |
|---|---|---|
| `GasStation` | `station_id`, station name/address, WGS 84 location, supported fuel types, catalog provenance/update time | One station has zero or more accepted reports per fuel type |
| `PriceReport` | `price_report_id`, `station_id`, fuel type, `price_per_litre`, `currency` (`CAD`), optional reporter `observed_at`, server `submitted_at`, report review/provenance state, **private** reporter principal ID | Immutable accepted submission; many reports may exist for a station/fuel pair |
| `CurrentPriceByFuel` | `station_id`, fuel type, selected `price_report_id`, displayed quote/age/quality metadata | At most one displayed user-report quote per station/fuel pair; **derived, rebuildable projection** |
| `SubmissionIdempotency` | verified user scope, operation, key, canonical request fingerprint, original response/report ID, expiry | Deduplicates retries for **24 hours**, not all independently submitted matching prices |
| `StationDirectorySource` | source reference, dataset version/update time, normalized station mapping and coverage | Provenance for the S7-owned catalog; upstream provider owns its original directory |

**Relationships and consistency:** `GasStation` 1-to-many `PriceReport`; `GasStation` + fuel type 0-to-1 displayed `CurrentPriceByFuel`. On accepting a report, persist the report and necessary private metadata atomically, then update the current-price projection in the same transaction or via a durable, replayable projection mechanism. If the projection is lost, rebuild it from accepted reports. S7 QR4 requires accepted prices/reports to remain retrievable after restart.

**Data quality:** A submitted price is an **accepted user report**, not necessarily a verified pump price. The most recent accepted report time (`last_accepted_report_at`) must be exposed as required by PR1. The displayed quote is stale **after 24 hours** under the S7 contract; this does not delete its report. The initial v1 contract uses **CAD per litre** (up to three decimals) and its enumerated fuel types. An optional future third-party price feed cannot overwrite or be conflated with user submissions without defined precedence/provenance rules.

## 5. Relationship and referential-integrity rules

Within a service, relationships MUST be consistent and updated atomically where a write affects multiple authoritative records. Cross-service entities are related **by opaque identifiers and contracts**, never foreign keys that require writing another service's database.

| Service | Core relationship | Required integrity rule |
|---|---|---|
| S1 | Road segment → observations → congestion snapshot → route/area result | Derived result identifies its source/snapshot revision and `as_of` |
| S2 | Historical dataset → features/patterns → model artifact → prediction; model → evaluation | Every served prediction references a validated model version; evaluations remain attributable to a held-out dataset |
| S3 | Incident report → audit events; verified identity reference → owned reports | Owner never changed through public PATCH; withdrawal hides public reads but preserves the authorized private record |
| S4 | Facility → tariffs; facility → occupancy snapshots | Stable facility ID connects independent rate and occupancy update streams |
| S5 | Forecast run → forecast intervals; observations → trends/guidance | Guidance identifies its source observation/forecast and rule version; forecasts never masquerade as observed conditions |
| S6 | Agency → route → trip → scheduled stop time ↔ stop | Stop-time queries follow an applicable schedule/calendar version; live overlays never replace schedule truth |
| S7 | Station → price reports; station/fuel → current quote; request key → accepted report | Current quote is derivable from accepted reports; write/retry operations do not create unintended duplicates |

For externally sourced IDs, the owning service MUST manage provider-to-platform mappings. Clients MUST treat public IDs as opaque. Provider identifier reuse or provider migration must not silently map one historical entity to a different real-world entity.

## 6. Cross-service data sharing and access controls

| Producer / owner | Allowed consumer | Contract boundary | Restrictions |
|---|---|---|---|
| S1 | S2, **if a historical export is formally defined** | Versioned export, API or published event | No S1 database access; copy only approved aggregated history with time/source/road-segment mapping |
| S1–S7 | Frontend/API Gateway | Published `/v1` read endpoints | Public response fields only; prevent internal IDs/credentials or raw private observations from leaking |
| S3 | Other services, only if subsequently required | Explicit incident read API or approved event | Do not publish private reporter identity, moderation details, or withdrawn content |
| S4 | Other services, only if required | Public facility read API or approved aggregate event | No raw user/device observation export |
| S5 | Other services, only if required | Weather/air-quality/guidance API | Preserve original observation time and provider provenance; do not relabel derived guidance as measured data |
| S6 | Other services, only if required | Stop/route/arrival/disruption read API | Distinguish scheduled and live values; provider usage rules apply |
| S7 | Other services, only if required | Station/price/comparison read API | No private submitter identity; accepted reports retain provenance and freshness |
| Platform identity authority | S3 and S7 protected write operations | Verified bearer-token/claims validation | S3/S7 store a private principal reference, not the identity provider's password or token database |
| Any domain service | Service Registry | Registry registration/discovery APIs | Registry owns only service metadata; never treat its API key as end-user authorization |

No other interservice dependency is imposed by this document. New integrations MUST have a named producer, consumer, entity/schema, transport, version, authentication, failure behavior, and retention policy. Published events should carry stable event IDs and timestamps; consumers MUST account for duplicate or out-of-order delivery. The external-dependency specification must separately cover third-party feeds.

## 7. Storage selection, deployment, and isolation

The following is the **adopted logical architecture**, with concrete deployment products selected where a good default is justified. A technology change is permitted without changing the API only if the data ownership, durability, consistency, security, and recovery guarantees remain intact.

| Service | Intended primary storage | Indexes / supporting cache | Required reliability boundary |
|---|---|---|---|
| S1 | Memory-first, service-isolated live aggregate/snapshot store | Time-bounded cache and authorized event subscriber; durable aggregate checkpoint optional | Replica-safe snapshot versioning; replay/refresh after restart; transient input cannot be represented as persistent history |
| S2 | S2-owned PostgreSQL for historical/feature metadata; S2-controlled object storage for versioned model artifacts | Interval/location indices and optional prediction cache | Durable model/dataset lineage and approved artifact retrieval after restart |
| S3 | S3-owned PostgreSQL + PostGIS | Spatial, status, owner, timestamp and idempotency-key indexes | Transactional durable report/audit/ownership commits; restart recovery |
| S4 | S4-owned PostgreSQL + PostGIS | Geospatial indexes, provider/update-time indexes and bounded occupancy cache | Durable catalog/tariffs; truthful occupancy fallback |
| S5 | S5-owned PostgreSQL (or equivalent durable normalized store) | Location/time/source indexes and bounded latest-data cache | Persist most recent usable weather/forecast for outage fallback |
| S6 | S6-owned PostgreSQL + PostGIS | Indexed route/stop/trip/calendar tables and bounded live feed cache | Usable timetable survives service restart/live-feed outage |
| S7 | S7-owned PostgreSQL + PostGIS | Station/fuel/submission-time indexes, uniqueness constraints, query cache | Durable immutable accepted reports and rebuildable current-price projection |

**Operational rules:**

- Each service MUST have exclusive credentials for its data and migrations; access to another service's domain tables is denied even if a physical database cluster is shared.
- Each service MUST own schema migrations, backups/recovery expectations, index management, and a versioned compatibility plan for data changes that affect its published contract.
- Persistent business records MUST NOT exist solely in container-local ephemeral files or RAM. Cache failure may degrade performance, but MUST NOT lose S3/S7 accepted submissions or S6's persisted schedule.
- Geographical queries MUST use WGS 84 coordinates and documented distance units; PostGIS is the selected indexing/query approach where specified, not an additional public API dependency.
- External source data MUST be stored only within approved licenses, geographic scope, and privacy constraints. Access logs and secrets MUST be protected separately from public analytics data.
- Background refreshes, broker consumers, and cache warmers MUST be idempotent or otherwise safe under retries. Multiple replicas must not conflict over ownership or generate contradictory authoritative projections.

## 8. Freshness is not retention

These periods come from the **adopted service contracts**, not a shared rule that all data is deleted at a threshold.

| Service / data | Freshness or evaluation rule | What persists or is kept independently |
|---|---|---|
| S1 live traffic input | Use `received_at` within previous **60 s** for live calculations | Bounded normalized observations/derived snapshots; any archive needs a separate retention basis |
| S2 historical traffic/model | Historical data stays labeled historical; dataset refresh deadlines and `model_valid_until` control use | Approved historical datasets, versioned models and evaluation evidence under licensing/reproducibility policy |
| S3 active incident | `is_stale` after **7 days** since `updated_at` as a review-age signal | Accepted report, private ownership and audit; withdrawal is not erasure |
| S4 occupancy | Stale after **300 s** | Usable facility catalog/rates and optionally bounded aggregated occupancy history |
| S4 parking rates/fees | Stale after **24 h** | Tariff source and effective-period history as permitted |
| S5 weather | Stale after **30 min** from `observed_at` | Latest known usable weather for provider-outage fallback |
| S5 air quality | Stale after **60 min** from `observed_at` | Licensed stored readings/trends and bounded cache |
| S5 forecast | Stale after **6 h** from `issued_at` | Last usable forecast run; historical trend data separately classified |
| S6 live transit | More than **60 s** since trustworthy live update is stale | Persisted static/scheduled timetable and bounded overlays |
| S7 price quote | Stale after **24 h** from the contract-defined quote time | Durable accepted price-report history; most recent accepted report timestamp |
| S3/S7 submission idempotency | Active de-duplication key retained **24 h** | Accepted reports persist independently after key expiry |

**Retention policy:** Neither PR1 nor the API contracts authorize arbitrary destruction of accepted reports, historical training data, or licensed provider archives. Before production deployment, each owner MUST record: data classification, collection justification, lawful/license constraints, minimum and maximum retention, deletion/anonymization process, backup expiry, and who approves changes. Until a duration is approved, **do not interpret cache age or API freshness as a deletion schedule**. Expired caches may be evicted; durable records must follow their approved lifecycle. User-facing deletion/withdrawal semantics and legal/privacy erasure processes are separate from cache eviction.

## 9. Access, privacy, provenance, and audit requirements

1. **Public vs private fields:** Public read APIs may return only the fields documented in S1–S7 OpenAPI. Private incident ownership, moderator decisions, report submitter identity, token contents, device identifiers and raw personal movement samples are excluded unless a separate authorized interface is approved.
2. **Verified identity:** S3/S7 must derive principal IDs from a trusted authentication system for writes. A user-controlled `owner_id`, `user_id`, or role request field is not an authorization source. S3 checks owner/moderator rights on every protected mutation.
3. **Pseudonymization and minimization:** S4 restricted occupancy inputs and S1 traffic/location observations must minimize linkability and avoid long-lived raw individual movement stores without an explicitly approved purpose and retention policy.
4. **Provenance:** Store and expose source references and appropriate `observed_at`, `received_at`, `source_updated_at`, `ingested_at`, `service_updated_at`, `as_of`, `submitted_at`, and `updated_at` **only according to their defined meanings**; unknown values remain null/unknown.
5. **Derived data labeling:** Prediction, congestion, occupancy, travel guidance, and current-price projections must be distinguishable from directly observed or user-submitted values. Their calculation/model/rule versions should be traceable internally.
6. **Security and audits:** Service-owned stores require least-privilege database roles, encrypted transport, protected backup access, and appropriate audit records for protected S3/S7 writes. Request IDs can link logs to operations but are not permission tokens.
7. **Provider terms:** Before ingesting any external dataset, confirm the permitted caching, redistribution, retention, attribution, geographic coverage, and personal-data restrictions in `External-Data-Dependencies.md`.

## 10. Recovery, cache invalidation, and readiness

| Service | Essential state that must survive/recover | Readiness and degraded-service principle |
|---|---|---|
| S1 | Rebuildable qualified live snapshot or explicitly permitted cached aggregate | Ready if honest live or allowed cached congestion can be served; no misleading live claims |
| S2 | Approved model artifacts plus required data/features | Ready only with an approved, unexpired model and sufficient accessible inputs, or documented equivalent fallback |
| S3 | Accepted incident records, ownership, audit and applicable idempotency | Read/write storage and token verification must be usable; a municipal feed outage alone is not fatal |
| S4 | Facility catalog and rates, with optional recent occupancy estimate | Ready with catalog and an honest response; live occupancy outage may permit unknown/cached fallback |
| S5 | Last usable weather/forecast with timestamp/provenance | Weather cache may sustain readiness if provider fails; air-quality subsystem can fail independently |
| S6 | Persisted valid schedule dataset | Ready with usable published schedules even when real-time predictions are unavailable |
| S7 | Accepted reports and station catalog; projection rebuild procedure | Durable submission/price retrieval must work; an optional third-party feed outage need not fail service |

Caches MUST be invalidated or revised when their underlying source version changes, a mutable owner record is updated, or a freshness threshold is crossed. Each service MUST have testable startup recovery and degraded-response behavior. A healthy process (`/health`) may be unready (`/ready`) if essential data is unavailable; the two probes must not be conflated.

## 11. Dependencies and outstanding deployment decisions

The ownership and storage boundaries above are adopted design rules. The following items are **not claimed as already supplied** and must be settled before the affected production integrations are enabled:

| Decision / dependency | Affected scope | Required output |
|---|---|---|
| Approved providers and data licenses | S1, S2, S4–S7; optional S3 | Provider, dataset, access method, credentials, geography, refresh frequency, rate limits, permitted storage and fallback in `External-Data-Dependencies.md` |
| S1 internal ingestion/event contract | S1, ingestion/broker; optionally S2 | Message schema, topic, observation identity, ordering/deduplication, receipt time, broker retention and failure handling |
| Platform user identity provider and moderator policy | S3, S7 | Trusted principal/token rules, claim validation, role assignment/revocation and least-privilege access |
| Retention/privacy schedule | Every service; especially S1, S3, S4, S7 | Explicit lifetimes, legal/license/privacy basis, deletion/anonymization, backup retention and approval |
| Data storage provisioning/recovery objectives | Every service | Isolated credentials/store instances, backup tests, recovery objectives, migration and scaling practices |
| S2 dataset/model governance | S2 | Historical source coverage, dataset refresh schedule, model evaluation/retraining and artifact expiry |
| Geographic provider-to-platform ID mapping | S1, S4, S6, S7 | Identifier stability, deduplication, mapping on provider change and source provenance |
| Source precedence for future mixed fuel feeds | S7 | If a third-party feed is approved, explicitly define confidence, verification and displayed-price precedence versus user reports |

An unresolved external provider or legal retention period is **not** permission to invent a provider, silently collect raw personal information, or claim the service has a production-ready integration.

## 12. T008 acceptance checklist

**Design deliverable — test against this document and the seven published API contracts:**

- [x] Each service S1–S7 has exactly one named owner for its platform-managed domain entities.
- [x] Minimum logical entities, key attributes, and relationships are documented for all seven services.
- [x] Data produced locally, consumed externally, cached, and persisted are distinguished.
- [x] Storage architecture and per-service isolation rules are defined, with the Service Registry separated.
- [x] Data sharing requires versioned APIs/events rather than cross-service database writes.
- [x] Freshness rules and accepted-report persistence/soft deletion are consistent with the S1–S7 contracts.
- [x] Privacy, provenance, readiness, recovery, and publication boundaries are defined.
- [ ] External provider access, licenses, cadence, quotas and fallback choices are confirmed or tracked in `External-Data-Dependencies.md`.
- [ ] Retention/privacy and backup policies are approved for deployment, especially for user/device data.
- [ ] Service implementations and automated conformance/restart tests verify the design.

---

**Normative boundary:** This document defines the required **logical ownership and lifecycle** of Smart City data. It does not depend on the state of any existing codebase, and it does not imply that the databases, data providers, identity system, or internal event feeds described above have already been deployed.
