# Requirements and Architectural Significant Requirements

> **Status:** Working consolidation. The original PR1 workbook is the authoritative source. Copy exact requirement wording and identifiers from it before submission; the summaries below are provisional.

## 1. Purpose

This document provides a shared reference for system requirements and measurable architectural quality targets.

Each requirement should have an owner, verification method, and evidence.

## 2. Functional Scope

The platform is intended to provide:

- Current traffic and congestion information.
- Traffic prediction for a specified future time and area.
- Construction and incident information.
- Parking availability, congestion, and pricing information.
- Weather and air-quality information.
- Public-transit analysis.
- Gasoline price and station information.

Detailed functional requirements should be assigned stable IDs and traced to APIs and tests.

## 3. Architectural Significant Requirements (ASRs)

The following table captures the registry-related targets discussed during PR1 review.

Verify exact wording, units, and conditions against the original workbook.

| ID | Provisional summary | Target discussed | Verification |
|---|---|---|---|
| ASR1 | Runtime registration and discovery | Registration/search within 5 seconds, without component restart | Register a new instance while registry remains running; measure lookup visibility |
| ASR2 | Heartbeat and unavailable-instance detection | Heartbeat every 5 seconds; 3 missed heartbeats; unavailable within 15 seconds | Stop heartbeat and measure time to unavailable state |
| ASR3 | Registry failover | Detect primary failure within 6 seconds using 2-second probes; backup serves searches within 10 seconds | Stop primary and measure detection and successful backup lookup |
| ASR4 | Registry state replication/recovery | State shared within 2 seconds; older registrations available after failover; newer registrations recovered through heartbeat within 5 seconds | Compare primary/backup state before and after forced failover |
| ASR5 | Multiple instances per logical service | Multiple endpoints supported for one logical service | Register multiple instances and verify discovery response |

## 4. Other Service-Level Targets

The following values were previously discussed as initial targets and must be checked against the PR1 workbook before being treated as approved acceptance criteria.

| Service | Example response-time target | Other target / note |
|---|---:|---|
| Live Traffic | 1,000 ms | Location freshness target: 60 seconds |
| Traffic Predictor | 3,000 ms | Accuracy and concurrency targets require confirmation |
| Construction & Incidents | 1,500 ms | Confirm freshness and concurrency |
| Parking Analytics | 2,000 ms | Confirm data source and update interval |
| Weather Analytics | 2,000 ms | Confirm data freshness |
| Transit Analytics | 1,500 ms | Confirm data freshness |
| Gasoline Analytics | 1,500 ms | Validate fuel types, station identifiers, and timestamps |

Do not treat illustrative values as approved requirements until checked against the source workbook.

## 5. Requirement Traceability

Maintain a traceability relationship:

`Requirement ID → design element → implementation component → test case → evidence → result`

| Requirement ID | Design reference | Implementation location | Test ID | Evidence | Status |
|---|---|---|---|---|---|
| ASR1 | Service Registry | Pending | Pending | Pending | Not verified |
| ASR2 | Service Registry | Pending | Pending | Pending | Not verified |
| ASR3 | Registry failover design | Pending | Pending | Pending | Not verified |
| ASR4 | Registry replication design | Pending | Pending | Pending | Not verified |
| ASR5 | Registry instance model | Pending | Pending | Pending | Not verified |

## 6. Status Definitions

- **Planned:** Accepted scope, not implemented.
- **In progress:** Implementation or design work underway.
- **Implemented:** Code/configuration exists, but acceptance evidence may be incomplete.
- **Verified:** Acceptance test passed under a documented environment and procedure.
- **Blocked:** Progress depends on an unresolved issue.
- **Deferred:** Explicitly removed from the current delivery scope with approval.

## 7. Change Management

When a requirement changes, record its original value, new value, rationale, approver, date, and affected design and tests.

Keep the PR1 workbook and this document consistent.