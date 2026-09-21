# Day 2 — Understand Saleor as an AI Environment

## Objective

Understand the system the AI OS will build against. Produce an architecture map you can hand to an AI later.

## Duties Outline

### 1. Map the Repo (60 min)

- [ ] List what each top-level project in `saleor-platform` is (`saleor`, `dashboard`, `storefront`, `notification`, `data`, `dev`).
- [ ] Identify `saleor/` source structure: `core/`, `graphql/`, `order/`, `product/`, `checkout/`, `payment/`, `webhooks/`, `plugins/`.
- [ ] Locate: entrypoint, Django app registry, settings, URL routes, WSGI/ASGI.
- [ ] Find where PostgreSQL connection, Redis and Celery are configured.

### 2. Explore Core Domain Objects (90 min)

For each: **Product, Customer, Order, Checkout, Payment, Inventory, Transaction, Webhook**

- [ ] Find its models file.
- [ ] Note the key models and their relationships (FKs).
- [ ] Note which GraphQL type exposes it (e.g. `Product`, `Order`, `Checkout`).
- [ ] Note which app owns it.

### 3. Understand the GraphQL API (60 min)

- [ ] Read the schema (`saleor/graphql/schema.graphql` or generate via `python manage.py get_graphql_schema`).
- [ ] Find the queries: `products`, `orders`, `customers`.
- [ ] Find the mutations: `checkoutCreate`, `orderCreate`, `checkoutComplete`, `paymentInitialize`.
- [ ] Note how auth works (token/user/permissions).
- [ ] Pick one query and one mutation; trace where they hit in the Python code.

### 4. Understand the PostgreSQL Schema (45 min)

- [ ] Run migrations.
- [ ] Inspect tables via `psql saleor` → `\dt`.
- [ ] Map: `product_product`, `order_order`, `checkout_checkout`, `payment_payment`, `account_user`.
- [ ] Note which fields an AI data agent would query for revenue analysis.

### 5. Understand Webhooks & Async (30 min)

- [ ] Find webhook models and how events are sent.
- [ ] Note key event types (order.updated, product.created).
- [ ] Understand how Celery/async tasks are used.

### 6. Trace One Complete Flow (60 min)

Pick checkout (or product listing). Trace and document end to end:

```text
User → HTTP (GraphQL) → URL → saleor/graphql resolver → service/use-case → DB query → response → client
```

- [ ] Run it live: seed demo data (`python manage.py populatedb`).
- [ ] Fire a query via GraphQL playground/curl.
- [ ] Watch logs and verify data appears in `psql`.

### 7. Write Architecture Notes (Day 2 deliverable)

Create `docs/architecture/` covering:

- [ ] Request flow (how a GraphQL call enters and leaves Saleor)
- [ ] Data flow (where each domain's data lives, relationships)
- [ ] API flow (key queries/mutations per domain)
- [ ] One business workflow (checkout → payment → order → webhook trigger)

### 8. Record Daily Learning Record (# Day 2)

- [ ] Problem / Concept / Understanding / Failure / Questions / Tomorrow

**Exit check — answer in your own words:** What is Saleor? Where does its data live? How does the API work? How will the AI OS communicate with it?