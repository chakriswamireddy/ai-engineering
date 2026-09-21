# Day 2 Notes — Understand Saleor as an AI Environment

> Study notes answering the `docs/day-plan/day-2.md` outline.
> Everything below was verified against the **running local stack** (Saleor `3.23.34`) and its source (extracted from the `ghcr.io/saleor/saleor:3.23` image).
> Source paths in this document are relative to the Saleor repo checkout (`saleor/...`).

---

## 1. Map the Repo

### 1.1 `saleor-platform` — the local orchestration repo

This repository is **only the launcher**. It runs prebuilt Saleor images with `docker compose` — it does not contain Saleor source code. Service list from `docker-compose.yml`:

| Service | Image | Port | Role |
|---|---|---|---|
| `api` | `ghcr.io/saleor/saleor:3.23` | 8000 | The Saleor Core GraphQL API (uvicorn) |
| `worker` | same image, `celery -A saleor worker -B` | — | Celery worker + beat (async jobs, webhooks) |
| `dashboard` | `ghcr.io/saleor/saleor-dashboard:3.23` | 9010 | Admin UI (SPA) |
| `db` | `postgres:15-alpine` | 5432 | PostgreSQL (user/pass: `saleor`/`saleor`) |
| `cache` | `valkey/valkey:8.1-alpine` | 6379 | Redis-compatible cache + Celery broker |
| `jaeger` | `jaegertracing/jaeger` | 16686/4317/4318 | Tracing UI + OTLP receiver |
| `mailpit` | `axllent/mailpit` | 1025/8025 | Test SMTP + web UI |

- `api` and `worker` share one image and one media volume. API code lives inside `/app` in the container.
- The `dashboard` at port **9010** (not 9000 as older README text says).

### 1.2 `saleor` — the Core application structure

Saleor Core is a **Django** application (Python). Layout under `saleor/`:

```
saleor/
├── asgi/               ASGI entrypoint (uvicorn serves this)
├── urls.py             ROOT_URLCONF — routes ^graphql/$
├── settings.py         all Django/Celery settings
├── celeryconf.py       Celery app
├── core/               cross-cutting: auth, jwt, http_client, tasks, models
├── graphql/            ALL GraphQL types, queries, mutations (schema engine)
│   ├── api.py          assembles the full schema (Query/Mutation mixins)
│   ├── schema.graphql  checked-in generated SDL (the full API contract)
│   └── <domain>/       per-domain schema.py, types/, mutations/, resolvers.py
├── <domain>/           business logic + ORM models
│   ├── account/        users, addresses, groups
│   ├── product/        products, variants, categories, collections
│   ├── order/          orders, lines, fulfillments, events
│   ├── checkout/       checkouts, lines, delivery
│   ├── payment/        legacy Payment + modern TransactionItem
│   ├── warehouse/      warehouses, stock, reservations, allocations
│   ├── webhook/        webhook definitions + event types
│   ├── channel/        sales channels
│   ├── shipping/       shipping methods/zones
│   ├── discount/       promotions, sales, vouchers
│   ├── app/            third-party apps (installed via manifest)
│   └── ...             attribute, giftcard, invoice, menu, page, tax, etc.
```

**One-app-one-domain rule:** each Django app owns its domain's models *and* its GraphQL types. Dependencies flow across apps via foreign keys (see §2).

---

## 2. Core Domain Objects

Every table follows Django's default convention **`<app_label>_<modelname_lowercased>`** (no `db_table` overrides exist). The GraphQL type name for each domain is what an AI would query.

### 2.1 Product (`saleor/product/models.py`)

| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `Product` | `product_product` | `product_type`, `category`, `default_variant`, `name`, `slug`, `search_vector` | `Product` |
| `ProductVariant` | `product_productvariant` | FK `product`, `sku`, `track_inventory`, `weight` | `ProductVariant` |
| `ProductChannelListing` | `product_productchannellisting` | FK `product` + FK `channel`, `is_published`, `published_at` | `ProductChannelListing` |
| `ProductVariantChannelListing` | `product_productvariantchannellisting` | FK `variant` + FK `channel`, `price`, `cost_price`, `discounted_price` | `ProductVariantChannelListing` |
| `ProductType` | `product_producttype` | `name`, `slug`, `kind`, `is_shipping_required` | `ProductType` |
| `Category` | `product_category` | MPTT self-FK `parent`, `name`, `slug` | `Category` |
| `Collection` | `product_collection` | M2M `products` (via `CollectionProduct`) | `Collection` |
| `ProductMedia` | `product_productmedia` | FK `product`, `image`, `type` | `ProductMedia` |

Key relationship: **Product → ProductVariant → ProductVariantChannelListing → Channel**. Price lives on the *variant + channel* pair, never on the product itself.

### 2.2 Customer (`saleor/account/models.py`)

| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `User` | `account_user` | `email` (unique, USERNAME_FIELD), `is_staff`, `is_active`, `is_confirmed`, `uuid`; M2M `addresses`; FK `default_billing_address`/`default_shipping_address` → `Address`; FK `customer_type`; denormalized `number_of_orders` | `User` |
| `Address` | `account_address` | flat denormalized postal fields, `country` (ISO) | `Address` |
| `Group` | `account_group` | M2M `permissions`, M2M `channels` (channel-limited groups) | `Group` |
| `CustomerEvent` | `account_customerevent` | FK `order`, FK `user`, FK `app`, `type`, `parameters` (JSON) | `CustomerEvent` |

`CustomerType` (`account_customertype`) is new in 3.23 — a single default "customer type" label per store.

### 2.3 Order (`saleor/order/models.py`)

| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `Order` | `order_order` | `id` (UUID PK) + **human `number`**; `status`/`authorize_status`/`charge_status`; FK `user`, FK `billing_address`/`shipping_address` → `Address`, FK `channel`, FK `shipping_method`, FK `collection_point`, FK `voucher`; M2M `gift_cards`; **`checkout_token` (string, not an FK)** | `Order` |
| `OrderLine` | `order_orderline` | FK `order`, **FK `variant` (SET_NULL)** → `ProductVariant`; denormalized `product_name`, `variant_name`, `product_sku`; `quantity`, `quantity_fulfilled`, `unit_price`, `total_price` | `OrderLine` |
| `Fulfillment` | `order_fulfillment` | FK `order`, `status`, `tracking_number`, refund fields | `Fulfillment` |
| `FulfillmentLine` | `order_fulfillmentline` | FK `order_line`, FK `fulfillment`, FK `stock` → `warehouse.Stock` | `FulfillmentLine` |
| `OrderEvent` | `order_orderevent` | FK `order`, FK `user`, FK `app`, `type`, `parameters` | `OrderEvent` |
| `OrderGrantedRefund` | `order_ordergrantedrefund` | FK `order`, FK `transaction_item` → `payment.TransactionItem`, `amount`, `status` | `OrderGrantedRefund` |

Notable behaviour: deleting a variant does **not** delete order history — the line survives with denormalized names (variant FK is `SET_NULL`).

### 2.4 Checkout (`saleor/checkout/models.py`)

| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `Checkout` | `checkout_checkout` | **`token` (UUID PK)**; `email`; FK `user` (CASCADE), FK `channel`, FK `billing_address`/`shipping_address`, FK `shipping_method`, FK `collection_point`; `currency`; money fields `total`, `subtotal`, `shipping_price` | `Checkout` |
| `CheckoutLine` | `checkout_checkoutline` | FK `checkout` (CASCADE), **FK `variant` (CASCADE)** → `ProductVariant`, `quantity`, `is_gift`, `total_price` | `CheckoutLine` |
| `CheckoutDelivery` | `checkout_checkoutdelivery` | FK `checkout`, cached delivery: `name`, `price`, `is_external`, `is_valid` | `Delivery` |
| `CheckoutMetadata` | `checkout_checkoutmetadata` | OneToOne `checkout` — split out so `SELECT ... FOR UPDATE` on the checkout row during completion doesn't carry metadata | — |

Key asymmetry vs OrderLine: **CheckoutLine.variant is CASCADE** — a checkout line dies with its variant. Checkout is an *intent*; Order is the *fact*.

### 2.5 Payment & Transaction (`saleor/payment/models.py`)

Saleor has **two payment stacks running in parallel**:

**Modern (preferred) — app-driven, PSP references:**
| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `TransactionItem` | `payment_transactionitem` | FK `checkout` (SET_NULL) **or** FK `order` (PROTECT) — one at a time; FK `app`, FK `user`; `psp_reference`, `idempotency_key`, `available_actions`; amount columns `authorized`/`charged`/`refunded`/`canceled` (+`_pending`); denormalized card fields | `TransactionItem` |
| `TransactionEvent` | `payment_transactionevent` | FK `transaction`, `type`, `amount`, `include_in_calculations`, `psp_reference` | `TransactionEvent` |

**Legacy:**
| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `Payment` | `payment_payment` | FK `checkout` (SET_NULL) / FK `order` (PROTECT), `gateway`, `charge_status`, `total`, `captured_amount` | `Payment` |
| `Transaction` | `payment_transaction` | FK `payment`, `kind`, `is_success`, `amount`, `error` | `Transaction` |

### 2.6 Inventory / Warehouse (`saleor/warehouse/models.py`)

| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `Warehouse` | `warehouse_warehouse` | FK `address` (PROTECT), M2M `channels` (via `ChannelWarehouse`), M2M `shipping_zones`; `click_and_collect_option`, `is_private` | `Warehouse` |
| `Stock` | `warehouse_stock` | FK `warehouse` + FK `product_variant`, `quantity`, `quantity_allocated`; unique `(warehouse, product_variant)` | `Stock` |
| `Allocation` | `warehouse_allocation` | FK `order_line` → `order.OrderLine`, FK `stock`; **stock locked when an order is placed** | `Allocation` |
| `Reservation` | `warehouse_reservation` | FK `checkout_line` → `checkout.CheckoutLine`, FK `stock`, `quantity_reserved`; **stock held (non-locking) for in-progress checkouts** | — |

Inventory math: `available_quantity = stock.quantity − allocations` (computed with annotations, `warehouse/models.py:330`). Checkouts *reserve*, orders *allocate*.

### 2.7 Webhook (`saleor/webhook/models.py` + `saleor/core/models.py`)

| Model | Table | Key fields / FKs | GraphQL type |
|---|---|---|---|
| `Webhook` | `webhook_webhook` | FK `app` (CASCADE), `name`, `target_url` (`http/https/awssqs/gcpubsub`), `is_active`, `secret_key`, `subscription_query`, `custom_headers`, `identifier` | `Webhook` |
| `WebhookEvent` | `webhook_webhookevent` | FK `webhook`, `event_type` (string like `order_updated`) | `WebhookEvent` |
| `EventPayload` | `core_eventpayload` | `payload` text or file | `EventPayload` |
| `EventDelivery` | `core_eventdelivery` | FK `webhook`, FK `payload`, `status` (PENDING/SUCCESS/FAILED) | `EventDelivery` |
| `EventDeliveryAttempt` | `core_eventdeliveryattempt` | FK `delivery`, `status`, `response_status_code`, `duration` | `EventDeliveryAttempt` |

---

## 3. The GraphQL API

### 3.1 Endpoint

- **`POST http://localhost:8000/graphql/`** — routed in `saleor/urls.py:17`, served by `saleor/graphql/views.py:GraphQLView`.
- `GET` only renders the GraphQL Playground (introspection + docs UI) when `PLAYGROUND_ENABLED` (default `True`). The schema is browsable at `http://localhost:8000/graphql/`.
- Generated SDL is checked in at `saleor/graphql/schema.graphql` — the full API contract on disk.

### 3.2 How the schema is assembled

- **No single `schema.py`.** The full schema is built in `saleor/graphql/api.py`:
  - `Query` mixin inherits from ~23 per-domain mixins (e.g. `ProductQueries`, `OrderQueries`, `CheckoutQueries`).
  - `Mutation` mixin inherits the matching mutation mixins.
  - `build_federated_schema(Query, mutation=Mutation)` wraps graphene and injects Apollo Federation fields `_entities`/`_service`.
- Each domain mixin lives in `saleor/graphql/<domain>/schema.py`. Example: `products` field → `ProductQueries` in `saleor/graphql/product/schema.py:142`, resolver `resolve_products` at `schema.py:482`.

### 3.3 Main queries (root Query fields)

| Domain | Queries |
|---|---|
| Products | `products`, `product`, `categories`, `category`, `collections`, `collection`, `productType(s)`, `productVariant(s)` |
| Orders | `orders`, `order`, `draftOrders`, `ordersTotal`, `orderByToken` |
| Customers | `me`, `user`, `customers`, `staffUsers`, `address` |
| Checkout | `checkout`, `checkouts` |
| Payments | `payment(s)`, `transaction(s)` |
| Webhooks | `webhook`, `webhookEvents`, `webhookSamplePayload` |
| System | `shop`, `channel(s)`, `warehouse(s)`, `stock(s)`, `plugin(s)`, `translations` |

### 3.4 Main mutations

| Domain | Mutations |
|---|---|
| Checkout | `checkoutCreate`, `checkoutLinesAdd`, `checkoutDeliveryMethodUpdate`, `checkoutComplete`, `checkoutPaymentCreate` (legacy), `orderCreateFromCheckout` |
| Order | **`orderCreate` does NOT exist** — use `draftOrderCreate` → `draftOrderComplete`, `orderCreateFromCheckout`, or `orderBulkCreate`; plus `orderConfirm`, `orderCancel`, `orderFulfill`, `orderNoteAdd`, `orderMarkAsPaid`, `orderGrantRefundCreate` |
| Products | `productCreate/Update/Delete`, `categoryCreate`, `attributeCreate`, `exportProducts` |
| Payments | modern `paymentGatewayInitialize`, `transactionCreate/Update`, `transactionInitialize`, `transactionProcess`, `transactionRequestAction`, `transactionEventReport`; legacy `paymentInitialize`, `paymentCapture/Refund/Void` (deprecated) |
| Webhooks | `webhookCreate/Update/Delete`, `eventDeliveryRetry`, `webhookDryRun`, `webhookTrigger` |
| Auth | `tokenCreate`, `tokenRefresh`, `tokenVerify`, `tokensDeactivateAll` |

### 3.5 Authentication

- **JWT (RS256)**: `Authorization: Bearer <token>`. Tokens created via `tokenCreate` (`saleor/core/jwt.py`, access TTL default 5 min, refresh 30 days).
- JWKS published at `http://localhost:8000/.well-known/jwks.json` (`saleor/urls.py:52`).
- **Apps authenticate with a static 30-char `auth_token`** instead (`saleor/graphql/app/dataloaders/utils.py`).
- Anonymous traffic is allowed only if storefront traffic is enabled (`SiteSettings.allow_storefront_traffic`); otherwise 401.
- **Permissions are per-field.** Query fields use `PermissionsField`/`ConnectionField` (e.g. `orders` requires `MANAGE_ORDERS`); mutations declare `permissions=(...)` in `Meta`, enforced in `BaseMutation.check_permissions` (`saleor/graphql/core/mutations.py`). Introspection is on.

> AI trap to remember: public, permission-less queries are almost all product/anonymous fields. Anything touching orders/customers/checkouts needs a token with the right permission — an AI agent must authenticate deliberately.

---

## 4. PostgreSQL Schema

Live from the running DB (`docker exec saleor-platform-db-1 psql -U saleor`):

- **147 tables**, 3.2.x schema, all in `public`, owned by `saleor`.
- Naming: `<app>_<model>` — `product_product`, `order_order`, `checkout_checkout`, `account_user`, `payment_transactionitem`, `warehouse_stock`, `webhook_webhook`, `core_eventdelivery`.
- M2M and FKs follow Django conventions (`order_order_gift_cards`, `account_user_addresses`, `discount_promotionrule_variants`).

Counts from the seeded demo database (`populatedb`):

```
products           29
variants           81
users              45
orders             20
checkouts           3
channels            2
webhooks            0
payments            0
transaction_items  20
```

Money is stored as `numeric` with a separate `currency` column (never floating point). JSON is stored in PostgreSQL `jsonb` (e.g. `parameters`, `custom_headers`). The search fields (`search_vector`, `tsvector`) power product/page search.

Example useful tables for an AI data agent:
- Revenue: `order_order` (money, `status`), `order_orderline` (`total_price`, `quantity`), joined on `order_id`.
- Product performance: `product_productvariantchannellisting` (`price`, `cost_price`, `prior_price`), `product_productvariant` (`sku`), `product_product` (`name`).
- Customers: `account_user` (`email`, `is_staff`, `is_active`), `order_order.user_id` counts.

---

## 5. Webhooks & Async

### 5.1 The async webhook flow (e.g. `order_updated`)

```
Mutation / business action
   → call_order_event(manager, ORDER_UPDATED, order)        # graphql/order/mutations/utils.py
   → PluginsManager.order_updated                            # plugins/manager.py
   → WebhookPlugin.order_updated                             # plugins/webhook/plugin.py
   → trigger_webhooks_async(...)                             # webhook/transport/asynchronous/transport.py
        → find webhooks for the order's channel
        → build EventPayload + EventDelivery(status=PENDING) # persisted in DB
        → send_webhook_request_async.apply_async(...)        # Celery task enqueued
   → Celery worker executes:
        create EventDeliveryAttempt
        POST to target_url (requests_hardened HTTP client,
             headers: Saleor-Event, Saleor-Signature = HMAC-SHA256(secret_key))
        on 2xx → delivery SUCCESS; on failure → retry (max 5, backoff 10s)
```

Important: webhooks are **not** Django signals fired on model save. They are triggered explicitly from application/action code through the plugin manager.

### 5.2 Event types

- Async events (`WebhookEventAsyncType`, `saleor/webhook/event_types.py`): `ORDER_CREATED`, `ORDER_UPDATED`, `ORDER_FULLY_PAID`, `CHECKOUT_CREATED`, `CHECKOUT_UPDATED`, `PRODUCT_CREATED`, etc.
- **There is no `checkout_completed` event** — completing a checkout emits the `order_created` event.
- Sync events (`WebhookEventSyncType`): `PAYMENT_*`, `CHECKOUT_CALCULATE_TAXES`, `SHIPPING_LIST_METHODS_FOR_CHECKOUT`, `TRANSACTION_*_REQUESTED`. These run **during** the request, not via Celery.

### 5.3 Celery & the broker

- Celery app: `saleor/celeryconf.py`, all tasks inherit `RestrictWriterDBTask` (writer-DB guard).
- **Valkey (Redis)** serves two roles from the same URL: Celery broker and Django cache. Cache used for introspection results, storefront-traffic flag, dataloaders.
- Beat schedule (`settings.py`) includes repo-specific jobs: expiry sweeps (checkouts, orders, reservations), search-vector updates, `checkout-automatic-completion`, promotion recalculation.
- Export formats are async too: CSV exports, event payload cleanup (`delete-outdated-event-data`).

---

## 6. Trace One Complete Flow

### Live trace: `products` query

Request fired against the running stack:

```
POST http://localhost:8000/graphql/
{"query":"{ products(first: 3, channel: \"default-channel\") { edges { node { id name } } totalCount } }"}
```

Response:

```json
{"data":{"products":{"edges":[
  {"node":{"id":"UHJvZHVjdDoxNTI=","name":"Apple Juice"}},
  {"node":{"id":"UHJvZHVjdDoxMzQ=","name":"Monospace Tee"}},
  {"node":{"id":"UHJvZHVjdDoxMzA=","name":"Paul's Balance 420"}}
],"totalCount":29}},
 "extensions":{"cost":{"requestedQueryCost":3,"maximumAvailable":50000}}}
```

### Code path

| Step | Where |
|---|---|
| 1. uvicorn serves ASGI app | `saleor/asgi/__init__.py` (wrapped: gzip, CORS, healthcheck, telemetry) |
| 2. URL routing | `saleor/urls.py:17` → `GraphQLView.as_view(backend=backend, schema=schema)` |
| 3. Request handling | `saleor/graphql/views.py` — parse body, parse+cache query document |
| 4. Auth on context | `saleor/graphql/context.py` (anonymous here) |
| 5. Root field → resolver | `ProductQueries.resolve_products` — `saleor/graphql/product/schema.py:482` |
| 6. Data resolver | `saleor/graphql/product/resolvers.py:116` — `Product.objects.visible_to_user(...)` |
| 7. SQL | Django ORM → PostgreSQL (`product_product` JOIN `product_productchannellisting`) |
| 8. Serialization | graphene schema types → JSON (orjson), plus cost extension |
| 9. Response | HTTP 200 JSON to client |

The business logic layer (domain service/functions) sits between resolvers and models — checkouts/orders have thick action modules (e.g. `saleor/checkout/complete_checkout.py`, `saleor/order/actions.py`).

---

## 7. Architecture Notes (Day 2 deliverable)

### 7.1 Request flow

```
Client (Dashboard 9010 or API client)
   └─ HTTP/JSON ─▶ uvicorn (api:8000)
                      └─ ASGI middleware (gzip/CORS/telemetry)
                         └─ Django URLs → ^graphql/$
                            └─ GraphQLView (parse, validate, auth, cost-check)
                               └─ graphql-core execution → root resolvers
                                  └─ domain resolvers → service functions
                                     └─ Django ORM → PostgreSQL
```

### 7.2 Data flow

```
checkout.Checkout ──complete──▶ order.Order (Order.checkout_token = checkout.token string)
   │                                  │
   │ FK variant                      └─ OrderLine ──variant(SET_NULL)──▶ ProductVariant
   └─ CheckoutLine ──variant(CASCADE)
                                          Order ──Allocation──▶ warehouse.Stock (locks qty)
Checkout ──Reservation──▶ warehouse.Stock (holds qty)
Payment sits beside both: checkout (SET_NULL FK) or order (PROTECT FK)
Channel is the ledger axis: product/variant listings and orders are all channel-scoped
```

### 7.3 API flow

- **Root Query fields** per domain expose *reads* with permission-gated resolvers.
- **Root Mutation fields** expose *writes*; every mutation is a `BaseMutation` (input → validation → service call → output/errors).
- **Subscriptions** exist for webhook payload templates (`webhook.subscription_query`) — not for browsers.
- Duplicated top-level objects like `Product`, `Order`, `Checkout` in SDL match the domain models 1:1; the mapping between GraphQL field and resolver is the apprenticeship's navigation map. `schema.graphql` is the ground truth a RAG indexer should chunk.

### 7.4 One business workflow: checkout → payment → order

1. `checkoutCreate` (anonymous or attached to user) — creates `checkout_checkout` row with a `token`.
2. `checkoutLinesAdd` — adds `CheckoutLine`s (FK to variant).
3. `checkoutDeliveryMethodUpdate` — picks the `Delivery` (shipping method / warehouse click-and-collect).
4. `paymentGatewayInitialize` + `transactionInitialize` → an app records `payment_transactionitem` (authorized state) against the checkout.
5. `checkoutComplete` — the turning point (`saleor/checkout/complete_checkout.py`):
   - locks the checkout row (`SELECT ... FOR UPDATE`)
   - creates the `Order` (copying `token` into `checkout_token`)
   - converts `CheckoutLine` → `OrderLine` (denormalized names/skus)
   - moves `Reservation` → `Allocation` on `warehouse_stock`
   - re-parents the `TransactionItem` from checkout → order
   - fires `ORDER_CREATED` (and `ORDER_UPDATED`/`ORDER_FULLY_PAID`) events → webhook plugin → Celery → HTTPS POST.
6. Result: a durable `Order`, a locked `OrderLine`, allocated stock, a transaction bound to the order, and async webhook deliveries to any subscribed app.

### 7.5 Failure modes to remember (for later days)

- Missing/expired `Checkout` (tokened lookup) → `checkout_complete` error before any order is made.
- Deleting a variant: order history survives, checkout lines break.
- Deleting a channel is rejected (`PROTECT`) once orders/checkouts reference it.
- Zero webhooks in demo DB — the pipeline only activates when an app is installed.
- JWT expires in ~5 minutes — an AI agent holding a token must refresh.

---

## 8. Daily Learning Record

### Problem
The AI OS needs to *understand* the system it will operate on before it can answer questions or call tools about it.

### Concept
Boundaries of a real commerce platform: GraphQL API surface, ORM/data layer, async side-effects (webhooks/workers), and their interconnection.

### Understanding
Saleor is a Django monolith whose only outward contract is GraphQL. Domains are Django apps; data is PostgreSQL; async work is Celery over Redis; third-party integration is via webhooks (async events) and transaction flows (sync schemas). There are two payment stacks (legacy `Payment` and modern `TransactionItem`), and checkout→order is a single synchronous mutation with a compensating reservation→allocation hand-off.

### Implementation
Nothing built on Day 2 — all exploration (verified against live DB + live GraphQL):

```
curl -s -X POST http://localhost:8000/graphql/ -H 'Content-Type: application/json' \
  -d '{"query":"{ products(first: 3, channel: \"default-channel\") { totalCount } }"}'
```

### Failure
No `orderCreate` mutation exists (documented in the plan) — discovered while listing mutation fields. No `checkout_completed` webhook event either. Both would have been assumed from convention.

### Debugging
Conventions can't be trusted; checked `schema.graphql` and the async event catalog (`saleor/webhook/event_types.py`) directly.

### Evaluation
Success criteria: can name tables, GraphQL fields, permission requirements, and the async webhook path for each of the 8 domains, and can retrace the `products` query from HTTP to SQL.

### Architecture
No architecture change to the AI OS. New mental map of Saleor: GraphQL → resolvers → services → models → PostgreSQL; Celery for webhooks/beats; Redis for cache+bus.

### Questions
- How exactly does `complete_checkout` transactionally reconcile payment state against `Order.charge_status`?
- Which GraphQL resolvers hit the replica DB vs writer DB (visible in `resolve_products`)? How does an AI agent stay on read replicas safely?
- Where do sync webhook schemas (`TRANSACTION_*_REQUESTED`) time out relative to the async event queue?

### Tomorrow
Day 3 — LLM fundamentals: build the first `User → AI Gateway → LLM → Response` endpoint and run the context/no-context/bad-context experiment.