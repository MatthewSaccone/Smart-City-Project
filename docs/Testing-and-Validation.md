# Testing and Validation

> **Status:** Test plan template. Populate with actual commands, environments, measured results, and evidence. Do not record a pass without a reproducible test result.

## 1. Purpose

This document describes how the Smart City Microservices Platform will be validated at unit, service, integration, performance, and acceptance levels.

## 2. Test Levels

| Level | Scope | Example |
|---|---|---|
| Unit | Individual functions/classes | Validate request model and data transformation |
| API/service | One service through its HTTP interface | Register an instance or query traffic endpoint |
| Integration | Multiple components together | Gateway discovers and calls a domain service |
| End-to-end | User workflow across the platform | User selects an area and views service data |
| Performance | Latency, throughput, concurrency | Measure response time under defined load |
| Resilience | Failure and recovery | Stop a service or registry node and observe behaviour |
| Acceptance | Approved requirements and ASRs | Verify each criterion in the PR1 workbook |

## 3. Registry Acceptance Tests

| Test ID | Requirement | Procedure | Expected result |
|---|---|---|---|
| REG-001 | ASR1 | Register an instance while registry is running; measure lookup visibility | Meets approved registration/search timing without restart |
| REG-002 | ASR2 | Stop heartbeat; measure lease expiry | Instance becomes unavailable within approved time |
| REG-003 | ASR3 | Stop primary registry; measure detection and backup lookup | Meets approved failover timing |
| REG-004 | ASR4 | Register instances, inspect backup state, force failover | State meets approved replication/recovery guarantees |
| REG-005 | ASR5 | Register multiple instances for one logical service | Lookup returns all eligible instances |
| REG-006 | Security | Attempt unauthorized registration and heartbeat | Requests are rejected |
| REG-007 | Identity | Attempt to reuse an instance ID for a different service | Conflicting identity is rejected |
| REG-008 | Recovery | Send heartbeat after lease expiry | Behaviour matches documented policy |

## 4. Domain Service Testing

For each domain service, test:

- Valid input and expected output.
- Missing and malformed parameters.
- Boundary values.
- Empty results.
- Stale or unavailable upstream data.
- Upstream timeout and error propagation.
- Authentication and authorization.
- Data freshness metadata.
- Concurrent requests.
- Performance against the approved service target.

## 5. Integration and End-to-End Scenarios

Suggested scenarios:

1. Frontend sends a request through the gateway to a domain service.
2. Gateway resolves a logical service ID through the registry.
3. Discovery returns multiple instances and the consumer selects an eligible endpoint.
4. An instance becomes unavailable and is no longer selected.
5. A registry lookup fails and the consumer follows the agreed retry/fallback policy.
6. One domain service is unavailable while unrelated services remain usable.
7. Invalid or unauthorized requests are rejected consistently.

## 6. Performance Test Record

For each run, record:

- Date/time and environment.
- Commit or image version.
- Machine/load-generator configuration.
- Number of concurrent users/requests.
- Warm-up duration and test duration.
- Request mix and dataset.
- p50, p95, and p99 latency.
- Throughput and error rate.
- Resource utilization where available.
- Whether the approved target passed.

| Service / operation | Target | Concurrency | p50 | p95 | Error rate | Result |
|---|---:|---:|---:|---:|---:|---|
| Live Traffic | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |
| Traffic Predictor | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |
| Construction & Incidents | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |
| Parking Analytics | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |
| Weather Analytics | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |
| Transit Analytics | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |
| Gasoline Analytics | Confirm requirement | Pending | Pending | Pending | Pending | Not tested |

## 7. Defect Tracking

For each defect, record an ID, severity, affected component, reproduction steps, expected and actual results, owner, status, fix commit, and regression test.

## 8. Evidence Checklist

- [ ] Unit-test output retained.
- [ ] API/integration test output retained.
- [ ] Performance results include environment and load details.
- [ ] Registry expiry test measures elapsed time.
- [ ] Failover test demonstrates a real backup serving requests.
- [ ] Replication test compares state across nodes.
- [ ] Security tests cover protected management endpoints.
- [ ] Acceptance results trace to exact PR1 requirements.
- [ ] Known failures and limitations are disclosed.