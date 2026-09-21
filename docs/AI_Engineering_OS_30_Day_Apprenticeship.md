# 30-Day AI Engineering Apprenticeship
## AI Engineering Operating System — Built Around Saleor

**Version:** 1.0  
**Duration:** 30 days  
**Primary Focus:** AI Engineering  
**Underlying System:** Saleor Commerce  
**Learning Model:** Apprenticeship, not a fixed syllabus  
**Recommended Effort:** 4–6 focused hours/day

---

# 1. Purpose

This document defines the intensive 30-day plan for building the **AI Engineering Operating System (AI Engineering OS)** around the open-source Saleor Commerce platform.

The objective is not to complete a collection of disconnected AI tutorials.

The objective is to spend 30 days **building one progressively more capable system**, while learning each concept when the system creates a genuine need for it.

The central philosophy is:

> **The project creates the learning requirement. The learning requirement creates the next engineering task.**

We will therefore not blindly follow a predetermined sequence of technologies.

Instead:

```text
Project Problem
      ↓
Why does this problem exist?
      ↓
Concept required to solve it
      ↓
Understand the concept
      ↓
Implement a simple version
      ↓
Integrate it into the OS
      ↓
Test it
      ↓
Break it deliberately
      ↓
Debug it
      ↓
Evaluate it
      ↓
Improve it
      ↓
Next project problem
```

---

# 2. What We Are Building

The actual project is the **AI Engineering OS**.

Saleor is the real-world software system that the AI OS understands and operates on.

```text
                         USER
                           |
                           v
                  +------------------+
                  |   AI Gateway     |
                  +--------+---------+
                           |
                           v
                  +------------------+
                  |  Agent Runtime   |
                  +--------+---------+
                           |
          +----------------+----------------+
          |                |                |
          v                v                v
      Knowledge          Data          Engineering
        Agent            Agent             Agent
          |                |                |
          +----------------+----------------+
                           |
              +------------+------------+
              |            |            |
              v            v            v
             RAG         Tools        Memory
              |            |            |
              |       +----+----+       |
              |       |    |    |       |
              v       v    v    v       v
          Saleor Docs  SQL  Git      State
              |       Saleor API
              +----------+----------+
                         |
                         v
                       SALEOR
                         |
              +----------+----------+
              |          |          |
              v          v          v
           GraphQL   PostgreSQL  Webhooks
```

By Day 30, the system should be capable of:

- understanding Saleor documentation
- searching Saleor source code
- retrieving grounded information
- querying Saleor business data
- calling controlled tools
- performing multi-step agent workflows
- maintaining task state
- using basic memory
- evaluating AI outputs
- tracing AI execution
- enforcing basic security controls
- running as a deployable AI application

---

# 3. What "Complete in 30 Days" Means

30 days does **not** mean expert mastery of every AI discipline.

It means:

### Engineering capability

You can build a meaningful AI Engineering platform.

### Conceptual capability

You can explain why its major components exist and how they work.

### Practical capability

You can modify, debug and extend the system without blindly copying framework examples.

### Foundation capability

You have enough understanding to continue going deeper into advanced AI Engineering topics.

The expected outcome is:

```text
30 Days
   |
   +--> Working AI Engineering OS
   |
   +--> Real Saleor integration
   |
   +--> RAG
   |
   +--> Tool-using agents
   |
   +--> Text-to-SQL
   |
   +--> Memory/state
   |
   +--> Evaluation
   |
   +--> Observability
   |
   +--> Security foundation
   |
   +--> Production architecture understanding
```

---

# 4. Learning Rules

## Rule 1 — Do not memorize frameworks

Understand the underlying mechanism before depending on a framework.

For example:

```text
Embeddings
   ↓
Understand vectors
   ↓
Understand similarity
   ↓
Implement basic retrieval
   ↓
Use vector database
```

Not:

```text
Install framework
↓
Copy RAG tutorial
↓
Declare RAG complete
```

---

## Rule 2 — Build before abstracting

The first implementation should often be deliberately simple.

Then improve it.

Example:

```text
Naive retrieval
      ↓
Vector retrieval
      ↓
Hybrid retrieval
      ↓
Reranking
      ↓
Evaluated retrieval
```

---

## Rule 3 — Break everything

A system that works for one happy-path query is not finished.

We deliberately create:

- bad questions
- ambiguous questions
- irrelevant documents
- malicious prompts
- incorrect SQL
- unavailable tools
- API failures
- model failures
- retrieval failures

Then diagnose them.

---

## Rule 4 — Measure AI behavior

We should not say:

> "It seems better."

We should ask:

- How much better?
- Better according to which metric?
- On which dataset?
- At what cost?
- With what latency?
- What failure cases remain?

---

## Rule 5 — Security before autonomy

Agents start with read-only tools.

Write operations come later.

Dangerous actions require:

- authorization
- validation
- audit logging
- human approval where appropriate

---

## Rule 6 — No premature complexity

Do not introduce:

- Kubernetes
- microservices
- multi-agent systems
- distributed queues
- multiple vector databases

unless the project has a real reason to need them.

---

## Rule 7 — Explain every architectural decision

For every major component, be able to answer:

> Why does this exist?

> Why did we choose this implementation?

> What alternatives did we consider?

> What are its failure modes?

---

# 5. Daily Operating Model

Recommended daily commitment:

**4–6 focused hours/day**

Suggested allocation:

```text
+----------------------+----------------+
| Activity             | Daily Target   |
+----------------------+----------------+
| Concept learning     | 45–60 min      |
| Implementation       | 2–3 hours      |
| Debugging/experiments| 45–60 min      |
| AI tutoring          | 30–60 min      |
| Documentation        | 20–30 min      |
+----------------------+----------------+
```

This is flexible.

A difficult engineering problem may consume an entire day.

That is acceptable.

The goal is understanding, not checking boxes.

---

# 6. AI Tutor Protocol

The AI tutor should not simply provide finished code.

The interaction should generally follow:

```text
Tutor presents problem
        ↓
Learner proposes solution
        ↓
Tutor challenges assumptions
        ↓
Concept is derived
        ↓
Learner implements
        ↓
Tutor reviews
        ↓
System is tested
        ↓
Failure is introduced
        ↓
Learner diagnoses
        ↓
Tutor explains deeper mechanism
        ↓
Implementation is improved
```

When the learner is stuck, the tutor should progressively provide:

1. a question
2. a hint
3. a smaller example
4. conceptual explanation
5. pseudocode
6. implementation guidance
7. complete reference implementation only when necessary

---

# 7. Day-by-Day Plan

# DAY 1 — Project Setup and Reality Check

## Objective

Get the real environment working.

## Learn

- What Saleor is
- Saleor architecture
- AI Engineering OS architecture
- GraphQL basics
- PostgreSQL role
- Docker role
- repository structure

## Build

- clone/run Saleor
- create AI OS repository
- establish local development environment
- create initial AI OS API
- document architecture

## Deliverable

```text
Saleor
   ↕
AI Engineering OS
```

running locally.

## Questions You Must Be Able to Answer

- What is Saleor?
- Where does its data live?
- How does the API work?
- How will our AI system communicate with it?
- Why are we keeping AI OS separate from Saleor core?

---

# DAY 2 — Understand Saleor as an AI Environment

## Objective

Understand the system we are building against.

## Explore

- products
- customers
- orders
- checkout
- payments
- inventory
- transactions
- webhooks
- GraphQL schema
- PostgreSQL schema
- repository structure

## Trace

At least one complete flow:

```text
User
 ↓
API
 ↓
Saleor
 ↓
Database
 ↓
Response
```

## Deliverable

Architecture notes covering:

- request flow
- data flow
- API flow
- one business workflow

---

# DAY 3 — LLM Fundamentals

## Objective

Understand what happens when an application calls an LLM.

## Learn

- tokens
- tokenization
- context
- inference
- temperature
- structured output
- streaming
- model limitations
- latency
- token cost

## Build

```text
User
 ↓
AI Gateway
 ↓
LLM
 ↓
Response
```

## Important Experiment

Ask the same question with:

- no context
- incorrect context
- correct context
- excessive context

Observe the differences.

## Deliverable

First working AI endpoint.

---

# DAY 4 — Model Gateway

## Objective

Stop coupling the application to one model provider.

## Learn

- model abstraction
- provider abstraction
- retries
- timeouts
- fallback
- configuration
- token accounting

## Build

```text
Application
     |
     v
AI Gateway
     |
 +---+---+
 |       |
Model A Model B
```

## Deliverable

A reusable model interface.

---

# DAY 5 — Embeddings

## Objective

Understand semantic representation.

## Learn

- vectors
- dimensions
- embeddings
- semantic similarity
- cosine similarity
- nearest-neighbor search

## First-Principles Exercise

Implement basic vector similarity yourself.

```text
Document
   ↓
Embedding
   ↓
Vector

Query
   ↓
Embedding
   ↓
Vector

Vector ↔ Vector
      ↓
Similarity
```

## Deliverable

A minimal semantic search prototype.

---

# DAY 6 — Build the First RAG Pipeline

## Objective

Make the AI understand Saleor documentation.

## Source

Start with Saleor documentation.

## Build

```text
Documents
 ↓
Parse
 ↓
Chunk
 ↓
Embed
 ↓
Store
 ↓
Retrieve
 ↓
LLM
```

## Learn

- chunking
- overlap
- metadata
- top-K retrieval
- context construction

## Deliverable

First working Saleor RAG assistant.

---

# DAY 7 — RAG Debugging

## Objective

Understand why naive RAG fails.

## Experiments

Create questions that:

- have obvious answers
- require multiple documents
- contain ambiguous terminology
- have similar documents
- cannot be answered from the corpus

## Investigate

- irrelevant retrieval
- missing retrieval
- excessive context
- duplicated context
- hallucination

## Deliverable

Failure report containing:

```text
Question
Retrieved documents
Expected documents
Failure reason
Proposed fix
```

---

# DAY 8 — Retrieval Improvements

## Objective

Improve retrieval quality.

## Learn

- metadata filtering
- query rewriting
- multi-query retrieval
- hybrid search
- keyword + semantic retrieval

## Build

At least one improved retrieval strategy.

## Deliverable

Baseline vs improved retrieval comparison.

---

# DAY 9 — Reranking and Context Construction

## Objective

Improve the final evidence passed to the model.

## Learn

- rerankers
- relevance scoring
- context ordering
- context compression
- lost-in-the-middle problem

## Build

```text
Retriever
   ↓
Candidate documents
   ↓
Reranker
   ↓
Best evidence
   ↓
Context builder
   ↓
LLM
```

## Deliverable

Measured retrieval improvement.

---

# DAY 10 — Code RAG

## Objective

Make the AI understand Saleor source code.

## Learn

Why source code differs from ordinary documents.

Understand:

- files
- functions
- classes
- imports
- dependencies
- symbols
- repositories

## Build

Code ingestion pipeline.

## Example Questions

- Where is checkout handled?
- Where is order creation implemented?
- Which module handles payments?

## Deliverable

Saleor Code Assistant.

---

# DAY 11 — Tool Calling

## Objective

Move from "answering" to "doing".

## Build Tools

```text
search_saleor_docs()
search_saleor_code()
get_product()
get_order()
get_customer()
```

## Learn

- tool schema
- function calling
- arguments
- tool results
- validation

## Deliverable

First tool-using agent.

---

# DAY 12 — Agent Loop

## Objective

Understand the core agent mechanism.

Build:

```text
User request
    ↓
Reason
    ↓
Choose tool
    ↓
Execute
    ↓
Observe result
    ↓
Reason again
    ↓
Choose next action
    ↓
Final answer
```

## Learn

- agent state
- observations
- tool selection
- termination
- retries

## Deliverable

Multi-step Saleor investigation agent.

---

# DAY 13 — Agent Failure Modes

## Objective

Understand where agents fail.

Test:

- wrong tool selection
- invalid arguments
- infinite loops
- missing information
- contradictory tool results
- hallucinated tools
- tool failure

## Build

- timeouts
- max iterations
- error handling
- fallback behavior

## Deliverable

Agent failure-handling layer.

---

# DAY 14 — Data Agent

## Objective

Teach AI to reason over Saleor's structured data.

## Learn

- schema grounding
- Text-to-SQL
- SQL generation
- SQL validation
- read-only access

## Build

```text
Question
 ↓
Schema
 ↓
SQL
 ↓
Validation
 ↓
Database
 ↓
Result
 ↓
Explanation
```

## Example

> Which products generated the most revenue last month?

## Deliverable

Read-only Saleor Data Agent.

---

# DAY 15 — Safe Text-to-SQL

## Objective

Make database agents safer and more reliable.

## Attack the system with:

- DELETE queries
- UPDATE queries
- invalid SQL
- expensive queries
- nonexistent tables
- ambiguous business definitions

## Build

- SQL parser/validation
- read-only connection
- query timeout
- row limits
- allow/deny rules

## Deliverable

Safe SQL execution layer.

---

# DAY 16 — Agent Planning

## Objective

Understand task decomposition.

Example:

```text
"Investigate why customer X could not complete checkout."

        ↓

1. Find customer
2. Find checkout
3. Inspect order state
4. Inspect payment
5. Search relevant documentation
6. Form hypothesis
7. Verify evidence
8. Respond
```

## Learn

- planning
- decomposition
- execution state
- checkpoints
- retry strategy

## Deliverable

Planner + executor workflow.

---

# DAY 17 — Memory and State

## Objective

Understand what an agent needs to remember.

Start with:

- working state
- conversation state
- task state

Then introduce persistent memory only if useful.

## Learn

- state vs memory
- short-term memory
- episodic memory
- semantic memory

## Deliverable

Agent state/checkpoint system.

---

# DAY 18 — Persistent Memory

## Objective

Add controlled long-term memory.

Potential memories:

```text
Previous investigations
Known Saleor concepts
User preferences within the application
Past successful workflows
```

## Learn

- memory retrieval
- memory relevance
- expiration
- deletion
- privacy

## Deliverable

Basic persistent memory subsystem.

---

# DAY 19 — Multi-Agent Architecture

## Objective

Determine whether multiple agents are actually useful.

Do not assume they are.

Compare:

```text
Single Agent
     vs
Specialized Agents
```

Potential design:

```text
              Orchestrator
                   |
       +-----------+-----------+
       |           |           |
       v           v           v
   Knowledge      Data     Engineering
     Agent       Agent        Agent
```

## Deliverable

Either:

- justified single-agent architecture

or:

- justified multi-agent architecture

The justification matters more than the number of agents.

---

# DAY 20 — Evaluation Fundamentals

## Objective

Stop judging AI quality manually.

## Build

Evaluation dataset containing:

- question
- expected evidence
- expected answer
- expected tools
- failure category

## Learn

- golden datasets
- regression testing
- deterministic evaluation
- LLM-as-judge limitations

## Deliverable

First evaluation harness.

---

# DAY 21 — RAG Evaluation

## Objective

Measure retrieval.

Track appropriate metrics such as:

- Recall@K
- Precision@K
- MRR
- retrieval relevance

Also evaluate:

- citation correctness
- groundedness
- answer relevance

## Deliverable

RAG benchmark.

---

# DAY 22 — Agent Evaluation

## Objective

Measure agent behavior.

Track:

- task success
- tool selection
- tool argument correctness
- number of steps
- unnecessary calls
- failure recovery
- cost
- latency

## Deliverable

Agent benchmark.

---

# DAY 23 — AI Observability

## Objective

Understand what happens inside an AI request.

Capture:

```text
Request
 ↓
Model
 ↓
Retrieval
 ↓
Documents
 ↓
Tool calls
 ↓
Tool results
 ↓
Final answer
```

Track:

- latency
- tokens
- cost
- errors
- model
- prompt version

## Deliverable

Traceable AI requests.

---

# DAY 24 — Distributed AI Observability

## Objective

Understand cross-component execution.

Trace:

```text
User
 ↓
AI API
 ↓
Agent
 ↓
RAG
 ↓
Vector DB
 ↓
Saleor API
 ↓
PostgreSQL
 ↓
Response
```

## Learn

- correlation IDs
- distributed traces
- structured logging
- metrics

## Deliverable

End-to-end request trace.

---

# DAY 25 — Prompt Injection

## Objective

Attack the system.

Test:

### Direct prompt injection

```text
Ignore all previous instructions...
```

### Indirect prompt injection

Malicious instructions hidden inside retrieved documents.

### Tool manipulation

Attempts to trick the agent into calling a dangerous tool.

## Deliverable

Threat model and initial defenses.

---

# DAY 26 — Agent Security

## Objective

Build permission boundaries.

Implement concepts such as:

```text
Agent
  ↓
Policy
  ↓
Permission check
  ↓
Tool
```

Rules:

- read-only by default
- explicit tool permissions
- input validation
- output validation
- audit logs

## Deliverable

Basic agent security layer.

---

# DAY 27 — Human-in-the-Loop

## Objective

Introduce controlled autonomy.

Example:

```text
Agent wants to refund customer
          ↓
Policy check
          ↓
Approval required
          ↓
Human approves
          ↓
Tool executes
          ↓
Audit event
```

## Learn

- approval workflows
- reversible vs irreversible actions
- confidence
- authorization

## Deliverable

Human approval mechanism.

---

# DAY 28 — Production Architecture

## Objective

Understand how the system becomes production-ready.

Review:

- Docker
- queues
- caching
- rate limiting
- async workers
- secrets
- model fallback
- retries
- failure recovery
- database scaling
- vector storage
- object storage

Map the local system to a production architecture.

## Deliverable

Production architecture diagram.

---

# DAY 29 — Deployment

## Objective

Deploy a working version.

Focus on:

- containerization
- environment variables
- secrets
- application deployment
- database
- observability
- health checks

AWS can be introduced at architectural depth first and implemented where practical.

## Deliverable

Accessible deployed AI OS or a production-like local deployment.

---

# DAY 30 — Integration, Evaluation and Defense

## Objective

Bring the entire system together.

Run end-to-end scenarios.

### Scenario 1 — Knowledge

> How does Saleor handle checkout?

### Scenario 2 — Code

> Where is this behavior implemented?

### Scenario 3 — Data

> Which products generated the most revenue?

### Scenario 4 — Customer investigation

> Why did this customer fail to complete checkout?

### Scenario 5 — Engineering investigation

> Why is this test failing?

### Scenario 6 — Operational investigation

> Why did latency increase?

### Scenario 7 — Controlled action

> Perform an approved operation.

For every scenario record:

- input
- reasoning/workflow
- retrieval
- tools
- evidence
- output
- latency
- cost
- evaluation score
- failure cases

## Final Deliverable

A complete technical demonstration of the AI Engineering OS.

---

# 8. Concepts Covered

The 30-day plan prioritizes AI Engineering.

## AI Foundations

- tokens
- embeddings
- inference
- model behavior
- context windows

## LLM Engineering

- prompting
- structured outputs
- model abstraction
- streaming
- token economics

## Retrieval

- embeddings
- vector similarity
- vector databases
- chunking
- metadata
- hybrid search
- reranking
- query rewriting
- contextual retrieval

## RAG

- ingestion
- retrieval
- context construction
- grounding
- citations
- evaluation

## Agents

- tools
- tool schemas
- agent loops
- planning
- state
- retries
- termination
- multi-step reasoning
- specialized agents

## Data Agents

- Text-to-SQL
- schema grounding
- query validation
- safe execution
- structured reasoning

## Memory

- working memory
- conversation state
- episodic memory
- semantic memory
- persistence
- retention

## Evaluation

- golden datasets
- regression testing
- retrieval metrics
- answer quality
- agent evaluation
- cost
- latency

## Production AI

- AI gateway
- model routing
- fallback
- observability
- tracing
- security
- human approval
- deployment

---

# 9. What We Are Deliberately Not Prioritizing

Because the target is one month, the following are not primary objectives.

## Deep ML theory

We will understand the concepts required for AI Engineering, but will not spend days deriving every optimization algorithm.

## Training large models

We will understand:

- training vs inference
- fine-tuning
- parameter-efficient tuning
- model serving

But will not attempt to train a production LLM.

## Kubernetes

Understand its role.

Do not spend the month becoming a Kubernetes administrator.

## Advanced distributed systems

Understand the architectural principles required for AI systems.

Go deeper later.

## Multi-agent hype

Only use multi-agent architecture if it solves an actual problem.

---

# 10. First-Principles Requirements

The following concepts should be understood deeply enough to implement simplified versions without a framework:

1. cosine similarity
2. basic vector retrieval
3. chunking
4. prompt construction
5. tool-calling loop
6. basic agent state
7. SQL validation
8. evaluation dataset
9. request tracing

The production implementation may later use specialized libraries.

---

# 11. Framework Policy

Frameworks should be introduced after understanding the problem.

Example:

```text
Need
 ↓
Simple implementation
 ↓
Understand limitations
 ↓
Framework
 ↓
Compare implementations
```

For each major framework, answer:

- What problem does it solve?
- What abstraction does it introduce?
- What does it hide?
- What happens underneath?
- What are its trade-offs?
- When should we not use it?

---

# 12. Project Documentation Requirements

Every significant learning milestone should produce documentation.

Maintain:

```text
docs/
|
+-- architecture/
|
+-- concepts/
|   +-- embeddings.md
|   +-- rag.md
|   +-- agents.md
|   +-- memory.md
|   +-- evaluation.md
|
+-- experiments/
|
+-- failures/
|
+-- decisions/
|
+-- evaluations/
|
+-- security/
|
+-- deployment/
```

---

# 13. Daily Learning Record

At the end of every day, record:

```text
# Day N

## Problem

What did the system need?

## Concept

What concept was required?

## Understanding

What did I learn?

## Implementation

What did I build?

## Failure

What broke?

## Debugging

Why did it break?

## Evaluation

How did I measure it?

## Architecture

What changed?

## Questions

What do I still not understand?

## Tomorrow

What problem should we solve next?
```

---

# 14. Weekly Checkpoints

## End of Week 1

You should understand:

- Saleor
- LLM basics
- model gateway
- embeddings
- basic RAG

And have:

```text
Saleor
 +
AI Gateway
 +
Basic RAG
```

---

## End of Week 2

You should understand:

- advanced retrieval
- code RAG
- tools
- agents
- Text-to-SQL
- safe SQL
- planning

And have:

```text
RAG
 +
Tools
 +
Agent
 +
Data Agent
```

---

## End of Week 3

You should understand:

- state
- memory
- multi-step workflows
- evaluation
- observability

And have:

```text
Agents
 +
Memory
 +
Evaluation
 +
Tracing
```

---

## End of Week 4

You should understand:

- AI security
- human-in-the-loop
- production architecture
- deployment
- operational trade-offs

And have:

```text
AI Engineering OS
 +
Security
 +
Evaluation
 +
Observability
 +
Deployment
```

---

# 15. End-of-Month Technical Defense

At the end of the month, the learner should be able to explain the architecture to another engineer.

The explanation should cover:

## Why Saleor?

Because it provides a real software system with:

- source code
- APIs
- database
- business workflows
- webhooks
- operational complexity

## Why AI Engineering OS?

Because the objective is to build an AI layer capable of understanding and operating over a real software system.

## Why RAG?

Because the AI needs grounded access to Saleor documentation and source code.

## Why tools?

Because retrieving information alone is insufficient for system interaction.

## Why agents?

Because some tasks require multiple tool calls and decisions.

## Why Text-to-SQL?

Because important Saleor information exists in structured databases.

## Why memory?

Because multi-step and recurring workflows require persistent state.

## Why evaluation?

Because AI output quality cannot be assumed.

## Why observability?

Because AI systems need to be debugged like software systems.

## Why security?

Because an AI with tools can become an operational security risk.

## Why human approval?

Because not every action should be autonomous.

---

# 16. Final 30-Day Architecture

```text
                                  USER
                                    |
                                    v
                            +---------------+
                            |   Web / API   |
                            +-------+-------+
                                    |
                                    v
                            +---------------+
                            |  AI Gateway   |
                            +-------+-------+
                                    |
                                    v
                            +---------------+
                            | Orchestrator  |
                            +-------+-------+
                                    |
             +----------------------+----------------------+
             |                      |                      |
             v                      v                      v
      Knowledge Agent          Data Agent          Engineering Agent
             |                      |                      |
             +----------------------+----------------------+
                                    |
                                    v
                            +---------------+
                            | Agent Runtime |
                            +-------+-------+
                                    |
              +---------------------+---------------------+
              |                     |                     |
              v                     v                     v
           Retrieval              Tools                Memory
              |                     |                     |
              |             +-------+-------+             |
              |             |       |       |             |
              v             v       v       v             v
          Vector DB      Saleor    SQL    GitHub        State
                           API
                            |
                            v
                          SALEOR
                            |
                  +---------+---------+
                  |         |         |
                  v         v         v
               GraphQL  PostgreSQL Webhooks

                            |
                            v
                    +---------------+
                    |  Evaluation   |
                    +-------+-------+
                            |
                            v
                    +---------------+
                    | Observability |
                    +-------+-------+
                            |
                            v
                    +---------------+
                    |   Security    |
                    +-------+-------+
                            |
                            v
                    +---------------+
                    |  Deployment   |
                    +---------------+
```

---

# 17. Definition of Success

The 30-day apprenticeship is successful if, by the end:

### You can build

A working AI Engineering OS around Saleor.

### You can explain

The major AI Engineering concepts used by the system.

### You can debug

RAG failures, tool failures, agent failures and model failures.

### You can evaluate

Whether the system actually improved.

### You can reason

About architectural trade-offs.

### You can extend

The system without rebuilding it from scratch.

### You can defend

Why each major component exists.

---

# 18. What Happens After Day 30?

Day 30 is **not the end of the project**.

It is the end of the intensive foundation.

After Day 30, deeper tracks can be pursued based on weaknesses discovered during the apprenticeship:

```text
                 Day 30
                    |
       +------------+------------+
       |            |            |
       v            v            v
   Advanced RAG   Agents      ML/Deep Learning
       |            |            |
       v            v            v
   Multimodal     Multi-Agent   Fine-Tuning
       |            |            |
       +------------+------------+
                    |
                    v
              Production AI
                    |
       +------------+------------+
       |            |            |
       v            v            v
      AWS      Distributed     Scaling
                    Systems
```

The system remains the same.

We deepen it rather than starting another project.

---

# 19. Final Principle

This project is deliberately different from a conventional curriculum.

We are not saying:

> "Day 1 is Python, Day 2 is ML, Day 3 is RAG..."

We are saying:

> **"Here is a real software system. What would an intelligent engineering layer need in order to understand and operate it?"**

Then we solve that problem.

If the answer requires embeddings, we learn embeddings.

If it requires RAG, we build RAG.

If RAG fails, we learn retrieval.

If retrieval is insufficient, we build agents.

If agents become unreliable, we learn evaluation.

If agents become dangerous, we learn security.

If the system becomes difficult to debug, we build observability.

If the system needs scale, we learn distributed systems.

This keeps the entire month connected to one objective:

> **Build an AI Engineering Operating System while becoming capable of explaining and engineering every major component that makes it work.**

---

# 20. Day 1 Starting Point

Do not start by installing an AI framework.

Start by answering:

1. What is Saleor?
2. What happens when a user creates an order?
3. Where is that data stored?
4. How does GraphQL reach the backend?
5. What are the important Saleor entities?
6. What should the AI Engineering OS be responsible for?
7. What should remain Saleor's responsibility?
8. How will the two systems communicate?

Then get the local environment running.

**The first engineering problem is not AI.**

It is:

> **"Can I understand and operate the real system that my AI Engineering OS will eventually reason about?"**

Once that is working, the apprenticeship begins.
