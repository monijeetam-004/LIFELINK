# API coverage: final regression

**Source.** When the backend suite runs with `QA_ROUTE_LOG=<path>`, a middleware in `tests/conftest.py` records the matched route template and the real status code of every request. The list of operations comes from the app's own OpenAPI schema.

**Logs used.**
* The full final run: `evidence/route_log_final.json`, 1,388 passed and 1 skipped.
* The follow-up run of the three test files changed after it: `evidence/route_log_followup.json`, 53 passed. It adds success-path tests for four new read endpoints that the full run had only hit with 401/403.

* Operations in the API: **160**
* Exercised by the suite: **160 / 160**
* With at least one 2xx response: **159 / 160**

* `POST /admin/graph/resync`: statuses seen {401: 4, 403: 24, 503: 4}. **BLOCKED.** It rebuilds the Neo4j projection, and Neo4j is not configured here, so it correctly answers 503. Success needs a live Neo4j (NEO4J_SETUP.md).

| Tag | Method | Path | Requests | Statuses seen |
|---|---|---|---|---|
| admin | GET | `/admin/analytics/overview` | 35 | 200×5, 401×4, 403×24, 422×2 |
| admin | GET | `/admin/audit-log` | 66 | 200×29, 400×1, 401×4, 403×32 |
| admin | POST | `/admin/audit-log/checkpoint` | 29 | 200×1, 401×4, 403×23, 409×1 |
| admin | GET | `/admin/audit-log/head` | 25 | 200×1, 401×4, 403×20 |
| admin | POST | `/admin/audit-log/verify` | 46 | 200×11, 401×4, 403×28, 409×1, 422×2 |
| admin | GET | `/admin/blood-requests` | 45 | 200×15, 401×4, 403×21, 422×5 |
| admin | POST | `/admin/blood-requests/{request_id}/transition` | 31 | 200×3, 400×2, 401×4, 403×21, 409×1 |
| admin | GET | `/admin/certificates` | 35 | 200×10, 401×5, 403×20 |
| admin | POST | `/admin/certificates/{certificate_id}/revoke` | 29 | 200×2, 401×4, 403×21, 404×1, 422×1 |
| admin | GET | `/admin/donors` | 34 | 200×10, 401×4, 403×20 |
| admin | GET | `/admin/emergencies` | 29 | 200×3, 401×4, 403×21, 422×1 |
| admin | GET | `/admin/facilities` | 37 | 200×12, 401×4, 403×20, 422×1 |
| admin | GET | `/admin/facilities/{facility_id}` | 40 | 200×15, 401×4, 403×20, 404×1 |
| admin | POST | `/admin/facilities/{facility_id}/revoke-verification` | 28 | 200×2, 401×4, 403×20, 409×2 |
| admin | GET | `/admin/graph/pathway-log` | 25 | 200×1, 401×4, 403×20 |
| admin | POST | `/admin/graph/resync` | 32 | 401×4, 403×24, 503×4 |
| admin | GET | `/admin/system/health` | 31 | 200×7, 401×4, 403×20 |
| admin | GET | `/admin/system/metrics` | 24 | 200×2, 401×5, 403×17 |
| admin | GET | `/admin/transport-routes` | 27 | 200×2, 401×4, 403×21 |
| admin | POST | `/admin/transport-routes` | 51 | 201×19, 400×2, 401×4, 403×24, 409×2 |
| admin | POST | `/admin/transport-routes/{route_id}/disrupt` | 34 | 200×8, 401×4, 403×20, 409×2 |
| admin | POST | `/admin/transport-routes/{route_id}/recover` | 27 | 200×2, 401×4, 403×20, 409×1 |
| admin | GET | `/admin/users` | 62 | 200×25, 401×4, 403×28, 422×5 |
| admin | GET | `/admin/users/stats` | 26 | 200×2, 401×4, 403×20 |
| admin | POST | `/admin/users/{user_id}/reactivate` | 32 | 200×4, 401×4, 403×24 |
| admin | PATCH | `/admin/users/{user_id}/role` | 31 | 200×2, 400×1, 401×4, 403×24 |
| admin | POST | `/admin/users/{user_id}/suspend` | 40 | 200×9, 400×1, 401×4, 403×24, 422×2 |
| auth | POST | `/auth/change-password` | 14 | 200×4, 401×8, 403×1, 429×1 |
| auth | POST | `/auth/forgot-password` | 14 | 202×13, 403×1 |
| auth | POST | `/auth/login` | 1889 | 200×1868, 401×15, 403×2, 429×4 |
| auth | POST | `/auth/logout` | 64 | 204×62, 403×2 |
| auth | GET | `/auth/me` | 84 | 200×59, 401×25 |
| auth | POST | `/auth/register` | 89 | 201×61, 403×1, 409×15, 422×8, 429×3, 503×1 |
| auth | POST | `/auth/reset-password` | 35 | 200×4, 400×13, 403×1, 429×17 |
| auth | GET | `/auth/session` | 3 | 200×3 |
| auth | POST | `/auth/verify-email` | 30 | 200×17, 400×11, 403×1, 422×1 |
| auth | POST | `/auth/verify-email/resend` | 6 | 202×5, 403×1 |
| blood-requests | GET | `/blood-requests` | 9 | 200×8, 401×1 |
| blood-requests | POST | `/blood-requests` | 296 | 200×10, 201×265, 401×2, 403×10, 404×1, 409×3, 422×3, 429×2 |
| blood-requests | GET | `/blood-requests/{request_id}` | 60 | 200×44, 401×2, 404×12, 422×2 |
| blood-requests | POST | `/blood-requests/{request_id}/cancel` | 27 | 200×13, 401×1, 403×1, 404×5, 409×6, 422×1 |
| blood-requests | POST | `/blood-requests/{request_id}/fulfill` | 15 | 200×6, 401×1, 403×1, 404×4, 409×3 |
| blood-requests | POST | `/blood-requests/{request_id}/respond` | 62 | 200×47, 400×4, 401×1, 403×8, 404×1, 409×1 |
| certificates | GET | `/certificates` | 15 | 200×4, 401×3, 403×8 |
| certificates | POST | `/certificates` | 76 | 201×58, 401×2, 403×9, 404×3, 409×3, 503×1 |
| certificates | GET | `/certificates/verify/{code}` | 9 | 200×5, 404×4 |
| certificates | GET | `/certificates/{certificate_id}` | 21 | 200×5, 401×3, 404×12, 422×1 |
| certificates | GET | `/certificates/{certificate_id}/download` | 36 | 200×9, 401×24, 404×1, 422×1, 503×1 |
| donations | GET | `/donations` | 9 | 200×4, 401×1, 403×4 |
| donations | GET | `/donations/{donation_id}` | 11 | 200×2, 401×1, 403×8 |
| donations | POST | `/donations/{donation_id}/reject` | 17 | 200×8, 401×1, 403×7, 409×1 |
| donations | POST | `/donations/{donation_id}/verify` | 75 | 200×62, 401×1, 403×9, 404×1, 409×2 |
| donors | GET | `/donors/me` | 22 | 200×13, 401×2, 403×7 |
| donors | PUT | `/donors/me` | 229 | 200×223, 401×1, 403×5 |
| donors | GET | `/donors/me/appointments` | 9 | 200×3, 401×1, 403×5 |
| donors | POST | `/donors/me/appointments` | 31 | 201×20, 400×3, 401×1, 403×6, 404×1 |
| donors | POST | `/donors/me/appointments/{appointment_id}/cancel` | 14 | 200×3, 401×1, 403×5, 404×4, 409×1 |
| donors | PUT | `/donors/me/availability` | 12 | 200×6, 401×1, 403×5 |
| donors | GET | `/donors/me/donations` | 10 | 200×5, 401×1, 403×4 |
| donors | POST | `/donors/me/donations` | 151 | 200×37, 201×96, 400×2, 401×2, 403×9, 404×1, 409×1, 422×3 |
| donors | GET | `/donors/me/requests` | 10 | 200×5, 401×1, 403×4 |
| donors | GET | `/donors/me/responses` | 21 | 200×14, 401×2, 403×5 |
| emergencies | GET | `/emergencies` | 3 | 200×2, 401×1 |
| emergencies | POST | `/emergencies` | 209 | 200×22, 201×166, 400×3, 401×1, 403×7, 422×8, 429×2 |
| emergencies | GET | `/emergencies/active` | 9 | 200×3, 401×2, 403×4 |
| emergencies | GET | `/emergencies/{emergency_id}` | 51 | 200×37, 401×1, 404×13 |
| emergencies | POST | `/emergencies/{emergency_id}/acknowledge` | 21 | 200×15, 401×1, 403×3, 409×2 |
| emergencies | POST | `/emergencies/{emergency_id}/assistance-found` | 8 | 200×3, 401×1, 403×1, 404×3 |
| emergencies | POST | `/emergencies/{emergency_id}/cancel` | 18 | 200×7, 401×1, 403×1, 404×7, 409×2 |
| emergencies | GET | `/emergencies/{emergency_id}/match-candidates` | 14 | 200×12, 401×1, 404×1 |
| emergencies | GET | `/emergencies/{emergency_id}/match-strategies` | 3 | 200×1, 401×1, 404×1 |
| emergencies | POST | `/emergencies/{emergency_id}/overrides` | 10 | 201×2, 401×1, 403×7 |
| emergencies | POST | `/emergencies/{emergency_id}/overrides/{override_id}/revoke` | 8 | 200×2, 401×1, 403×5 |
| emergencies | POST | `/emergencies/{emergency_id}/pathway` | 19 | 200×14, 401×1, 403×3, 404×1 |
| emergencies | POST | `/emergencies/{emergency_id}/pathway/release` | 3 | 200×1, 401×1, 403×1 |
| emergencies | POST | `/emergencies/{emergency_id}/pathway/reserve` | 3 | 200×1, 401×1, 403×1 |
| emergencies | GET | `/emergencies/{emergency_id}/pathways` | 3 | 200×2, 401×1 |
| emergencies | POST | `/emergencies/{emergency_id}/resolve` | 18 | 200×9, 401×1, 403×1, 404×4, 409×3 |
| emergencies | GET | `/emergencies/{emergency_id}/resource-pathways` | 16 | 200×3, 401×1, 403×4, 404×3, 422×5 |
| emergencies | POST | `/emergencies/{emergency_id}/respond` | 44 | 200×31, 400×3, 401×1, 403×6, 404×1, 409×2 |
| events | GET | `/events/stream` | 17 | 200×15, 401×2 |
| family | GET | `/family/links` | 12 | 200×10, 401×2 |
| family | POST | `/family/links` | 77 | 201×61, 401×1, 403×7, 404×4, 409×4 |
| family | POST | `/family/links/{link_id}/accept` | 41 | 200×28, 401×1, 403×8, 404×1, 409×3 |
| family | POST | `/family/links/{link_id}/reject` | 19 | 200×7, 401×1, 403×7, 404×1, 409×3 |
| family | POST | `/family/links/{link_id}/revoke` | 19 | 200×9, 401×1, 403×6, 404×1, 409×2 |
| forecasts | GET | `/forecasts` | 6 | 200×2, 401×1, 403×3 |
| forecasts | POST | `/forecasts` | 15 | 201×6, 401×1, 403×8 |
| forecasts | GET | `/forecasts/{run_id}` | 7 | 200×2, 401×1, 403×3, 404×1 |
| health | GET | `/health` | 8 | 200×8 |
| health | GET | `/health/live` | 4 | 200×4 |
| health | GET | `/health/ready` | 5 | 200×4, 503×1 |
| hospitals | GET | `/hospitals/directory` | 15 | 200×13, 401×2 |
| hospitals | GET | `/hospitals/me` | 172 | 200×165, 401×1, 403×6 |
| hospitals | PUT | `/hospitals/me` | 471 | 200×465, 401×1, 403×5 |
| hospitals | GET | `/hospitals/me/activity` | 6 | 200×1, 401×1, 403×4 |
| hospitals | GET | `/hospitals/me/emergencies` | 6 | 200×1, 401×1, 403×4 |
| hospitals | GET | `/hospitals/me/inventory` | 64 | 200×59, 401×1, 403×4 |
| hospitals | PUT | `/hospitals/me/inventory` | 16 | 200×9, 401×1, 403×5, 422×1 |
| hospitals | POST | `/hospitals/me/inventory/add` | 177 | 200×165, 401×1, 403×5, 409×4, 422×2 |
| hospitals | POST | `/hospitals/me/inventory/allocate` | 27 | 200×16, 401×1, 403×5, 409×5 |
| hospitals | GET | `/hospitals/me/inventory/components` | 15 | 200×6, 401×1, 403×8 |
| hospitals | POST | `/hospitals/me/inventory/expire` | 10 | 200×2, 401×1, 403×5, 409×2 |
| hospitals | GET | `/hospitals/me/inventory/log` | 7 | 200×2, 401×1, 403×4 |
| hospitals | POST | `/hospitals/me/inventory/reconcile` | 8 | 200×1, 401×1, 403×5, 409×1 |
| hospitals | POST | `/hospitals/me/inventory/release` | 10 | 200×3, 401×1, 403×5, 409×1 |
| hospitals | POST | `/hospitals/me/inventory/reserve` | 38 | 200×28, 401×1, 403×5, 409×4 |
| hospitals | GET | `/hospitals/me/requests` | 11 | 200×6, 401×1, 403×4 |
| hospitals | POST | `/hospitals/me/requests/{request_id}/allocate` | 52 | 200×32, 400×1, 401×1, 403×5, 404×2, 409×10, 422×1 |
| hospitals | GET | `/hospitals/me/requests/{request_id}/events` | 9 | 200×1, 401×1, 403×4, 404×3 |
| hospitals | POST | `/hospitals/me/requests/{request_id}/fulfill` | 17 | 200×6, 401×1, 403×5, 404×1, 409×4 |
| hospitals | POST | `/hospitals/me/requests/{request_id}/respond` | 14 | 200×4, 401×1, 403×5, 404×1, 422×3 |
| hospitals | GET | `/hospitals/me/resource-pathways` | 22 | 200×8, 401×1, 403×8, 422×5 |
| hospitals | GET | `/hospitals/me/routes` | 7 | 200×2, 401×1, 403×4 |
| hospitals | GET | `/hospitals/me/units` | 13 | 200×8, 401×1, 403×4 |
| hospitals | POST | `/hospitals/me/units/{unit_id}/discard` | 10 | 200×2, 401×1, 403×5, 409×2 |
| hospitals | POST | `/hospitals/me/units/{unit_id}/quarantine` | 10 | 200×2, 401×1, 403×5, 404×2 |
| hospitals | POST | `/hospitals/me/units/{unit_id}/release-quarantine` | 8 | 200×2, 401×1, 403×5 |
| hospitals | POST | `/hospitals/me/units/{unit_id}/thaw` | 12 | 200×2, 401×1, 403×5, 409×4 |
| matching | GET | `/blood-requests/{request_id}/match-candidates` | 8 | 200×5, 401×1, 404×2 |
| matching | GET | `/blood-requests/{request_id}/match-strategies` | 3 | 200×1, 401×1, 404×1 |
| matching | GET | `/map/data` | 8 | 200×4, 401×1, 403×3 |
| matching | GET | `/matching/blood-compatibility` | 3 | 200×1, 401×2 |
| meta | GET | `/` | 1 | 200×1 |
| notifications | GET | `/notifications` | 133 | 200×128, 401×1, 403×2, 422×2 |
| notifications | GET | `/notifications/preferences` | 7 | 200×5, 401×2 |
| notifications | PUT | `/notifications/preferences` | 15 | 200×9, 401×2, 403×1, 422×3 |
| notifications | POST | `/notifications/read-all` | 3 | 200×1, 401×1, 403×1 |
| notifications | GET | `/notifications/unread-count` | 6 | 200×5, 401×1 |
| notifications | POST | `/notifications/{notification_id}/read` | 16 | 200×11, 401×1, 403×1, 404×3 |
| pathways | GET | `/pathways/config` | 2 | 200×1, 401×1 |
| pathways | POST | `/pathways/simulate` | 9 | 200×2, 401×1, 403×5, 404×1 |
| patients | GET | `/patients/managed` | 18 | 200×12, 401×2, 403×4 |
| patients | GET | `/patients/me` | 15 | 200×3, 401×4, 403×4, 503×4 |
| patients | PUT | `/patients/me` | 8 | 200×2, 401×1, 403×4, 422×1 |
| shipments | GET | `/shipments` | 9 | 200×5, 401×1, 403×3 |
| shipments | POST | `/shipments` | 103 | 200×1, 201×88, 400×2, 401×1, 403×5, 409×6 |
| shipments | GET | `/shipments/by-code/{shipment_code}` | 9 | 200×3, 401×1, 403×5 |
| shipments | GET | `/shipments/units/{unit_code}` | 12 | 200×5, 401×1, 403×6 |
| shipments | GET | `/shipments/{shipment_id}` | 12 | 200×6, 401×1, 403×5 |
| shipments | POST | `/shipments/{shipment_id}/arrive` | 22 | 200×17, 401×1, 403×4 |
| shipments | POST | `/shipments/{shipment_id}/cancel` | 13 | 200×4, 401×1, 403×5, 409×3 |
| shipments | POST | `/shipments/{shipment_id}/delay` | 11 | 200×3, 401×1, 403×5, 409×2 |
| shipments | POST | `/shipments/{shipment_id}/dispatch` | 72 | 200×60, 401×1, 403×7, 409×4 |
| shipments | POST | `/shipments/{shipment_id}/fail` | 30 | 200×21, 401×1, 403×5, 409×3 |
| shipments | POST | `/shipments/{shipment_id}/in-transit` | 22 | 200×17, 401×1, 403×4 |
| shipments | POST | `/shipments/{shipment_id}/receive` | 30 | 200×15, 401×1, 403×6, 409×8 |
| shipments | POST | `/shipments/{shipment_id}/reconcile` | 34 | 200×12, 401×1, 403×8, 409×3, 422×10 |
| shipments | GET | `/shipments/{shipment_id}/telemetry` | 7 | 200×2, 401×1, 403×4 |
| shipments | POST | `/shipments/{shipment_id}/telemetry` | 31 | 200×22, 401×1, 403×4, 422×4 |
| shipments | POST | `/shipments/{shipment_id}/telemetry/simulate` | 10 | 200×2, 401×1, 403×7 |
| shipments | GET | `/shipments/{shipment_id}/timeline` | 10 | 200×4, 401×1, 403×5 |
| verifications | GET | `/admin/verifications` | 21 | 200×6, 401×2, 403×13 |
| verifications | GET | `/admin/verifications/{request_id}` | 24 | 200×5, 401×2, 403×10, 404×3, 422×4 |
| verifications | POST | `/admin/verifications/{request_id}/approve` | 27 | 200×10, 401×2, 403×9, 409×6 |
| verifications | GET | `/admin/verifications/{request_id}/documents/{index}` | 41 | 200×11, 401×1, 403×14, 404×6, 409×6, 422×1, 503×2 |
| verifications | POST | `/admin/verifications/{request_id}/documents/{index}/rescan` | 10 | 200×3, 401×1, 403×6 |
| verifications | POST | `/admin/verifications/{request_id}/reject` | 26 | 200×11, 401×2, 403×8, 409×3, 422×2 |
| verifications | POST | `/verifications` | 131 | 201×90, 401×3, 403×8, 409×8, 422×19, 429×1, 503×2 |
| verifications | GET | `/verifications/me` | 15 | 200×13, 401×2 |
