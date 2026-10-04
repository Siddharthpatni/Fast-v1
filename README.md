# 🚀 FastMile — Unified Mobility Platform for LLM-Guided Routing

> **WiSe 2026 · AI Team Projects (ATP)**
> Machine Learning and COgnitive SoftwaRE (CORE) Research Group
> TU Clausthal × Babeș-Bolyai University, Cluj, Romania

---

## 📌 What Is FastMile?

FastMile is an **intermodal mobility platform** that uses **Large Language Models (LLMs)** to guide route optimization across multiple transport modes in a single door-to-door journey.

### The Problem

In polycentric regions, commuters travel from surrounding towns into employment centres using a mix of:

- 🚌 Public transport
- 🚗 Car-sharing
- 🛴 Micromobility (e-scooters, bikes)
- 🚐 On-demand ride services

The **private car still dominates** because it's the most reliable option from front door to destination. Existing multimodal solutions fail at providing **convenient and predictable transfers** across the whole journey chain.

### The Solution

FastMile provides a unified platform that:

1. **Chains multiple transport modes** into a single seamless journey
2. **Uses LLMs to interpret disruption reports** (free-text incident data) in real-time
3. **Reroutes dynamically** when disruptions occur mid-journey
4. **Recommends capacity-aware options**, even at peak times

---

## 🚦 Running FastMile

```bash
make init          # copy .env, build, start PostGIS + Redis + backend + frontend
make pull-model    # once: download the local LLM (needs Ollama running on your machine)
open http://localhost:5173
```

Search a start and destination (or click the map twice). In **Disruptions**, add a notice or pick an example; if it hits your selected trip, the yellow notice offers **Find new connections**.

Everything runs in Docker, detached (`make up`). The frontend calls `/api` on its own origin and Vite forwards it to the backend, so the app works the same in Docker, in the headless e2e browser, and with a local `npm run dev` (which forwards to `localhost:8000`).

| Command | What it does |
|---|---|
| `make test` | Backend tests, inside the backend container (no LLM or Jev calls) |
| `make e2e` | End-to-end UI tests in headless Chromium, in Docker (official Playwright image) against the running stack; screenshots in `e2e/artifacts/` |
| `make eval` | Scores disruption extraction on `data/sample_disruptions`: LLM vs rule baseline, plus Jev severity |
| `make train-capacity CSV=/data/raw/occupancy.csv` | Trains the occupancy model from real utilisation data |
| `make train-wait CSV=/data/raw/trips.csv` | Trains the on-demand wait model from operator trip data |
| `make real-data` | Downloads the free gtfs.de feeds and cuts them to the Harz/Braunschweig region (refresh weekly; each snapshot covers about a month) |
| `make dev-data` | Regenerates the dev fixtures used by the tests |
| `make migrate-create MSG="…"` | After changing `app/models`: generates an Alembic migration; it is applied on the next backend start (or `make migrate`) |
| `make db-reset` | Fresh, empty database (drops all stored disruptions) |

API docs: http://localhost:8000/docs

### How the code maps to the architecture

| Brief | Code | Notes |
|---|---|---|
| Region | `make real-data` → `data/gtfs_region` | Own timetable for Lower Saxony and around (bbox 51.30,8.80,53.20,11.70: Harz, Hannover, Bremen, Göttingen, Wolfsburg, Magdeburg), local + regional + long-distance gtfs.de feeds: 37.5k stops, ~1.2 GB RAM, 0.1 s per search. Bigger needs more RAM: the whole of Germany does not fit |
| Provider data | `services/ingestion/` | GTFS static, GTFS-Realtime (delays, cancellations, service alerts), GBFS v2/v3 (bikes, scooters, car-sharing), on-demand zones |
| FastMile Core | `services/routing/` | Connection Scan Algorithm per service day; first/last mile by walk, bike, scooter, car-sharing, on-demand; door-to-door options. Offered plans are re-timed and drawn along real streets via OSRM (`streets.py`) |
| Optimisation check | `services/optimisation/` | Every candidate is checked for vehicle availability, predicted capacity, transfer slack and active disruptions, then scored and ranked. Chains on a line a report recommends instead are noted and preferred |
| Language model | `services/llm/` | Reads free-text reports into lines, stops, time window and effect, and proposes the lines the report recommends instead (`alternative_lines`), which the optimisation check prefers. Key and models are checked at startup without using quota (`/api/health` shows `ready`). Rule-based fallback doubles as the eval baseline |
| Fast decisions | `services/decisions/` | Jev (TypeSafe AI): severity + needs-reroute per report, reroute strategy for broken trips |
| ML | `services/ml/` | Occupancy and on-demand wait models (gradient boosting). Transparent heuristics until trained on real data |
| Special case | `services/disruptions.py`, `services/replanning.py`, `POST /api/routes/replan` | Locate the traveller, re-check the remaining chain, replan from their position |
| Automatic disruptions | `services/alerts.py` | Every realtime refresh, regional GTFS-RT service alerts become disruptions (alert IDs and periods used directly; the LLM reads the text only when the effect is unknown); withdrawn alerts are resolved |
| Germany-wide trips | `services/routing/transitous.py`, `_plan` in `api/__init__.py` | Trips with an end outside the loaded region go to Transitous (MOTIS, national DELFI timetable, live delays). Its itineraries pass the same check: reported disruptions match by line name inside the region and by stop position |
| Search and arrive-by | `services/geocoding.py`, `plan_arrive_by` in `planner.py` | Region stops, then stops, addresses and places across Germany via Transitous (Photon as fallback). Arrive-by binary-searches the latest departure with the forward planner |
| Departures board | `services/departures.py`, `GET /api/departures` | Next departures at any stop: own timetable with live delays in the region, Transitous elsewhere; call-ahead trips (GTFS `pickup_type=2`) and reported disruptions marked |
| Languages, legal | `app/i18n.py`, `frontend/src/i18n.ts`, `GET /api/legal` | English and German messages (`lang`); Impressum and privacy notice from `OPERATOR_*` settings |
| Rate limits | `ratelimit.py` | Per-IP limits in Redis on endpoints that spend LLM, Jev or geocoder quota |

### Models used

| Job | Model | Config |
|---|---|---|
| Disruption extraction (German/English → JSON) | Free OpenRouter models first, then `deepseek/deepseek-v4-flash` (paid, ~$0.0002 per report, capped per day; 77% fully correct on the 30 dev reports, best of 4 cheap models tested). Ollama or OpenAI also supported | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_PAID_MODEL`, `LLM_PAID_DAILY_LIMIT` |
| Transit routing, own region | FastMile's Connection Scan Algorithm (no ML) on gtfs.de + GTFS-RT | `GTFS_PATH`, `GTFS_RT_URL` |
| Transit routing, rest of Germany; place search | Transitous / MOTIS (free, no key) | `TRANSITOUS_URL` |
| Street times and geometry | OSRM on the FOSSGIS public servers (free, no key) | `STREET_ROUTING`, `STREET_ROUTING_URL` |
| Severity, reroute decisions | Jev (`jev-latest`) | `JEV_API_KEY` |
| Occupancy, on-demand wait | scikit-learn HistGradientBoosting | trained with `make train-*` |

Both LLM providers use schema-constrained decoding, so output always parses. With `LLM_PROVIDER=none` or no key, everything still runs on the rule-based extractor and effect-based severity.

### Data

**Real data (default in `.env`):**

| Feed | Source | Notes |
|---|---|---|
| Timetables | gtfs.de free feeds (local + regional + long-distance), data from DELFI e.V. | Cut to the box 51.30–53.20 N, 8.80–11.70 E by `make real-data` into `data/gtfs_region` (not in git: ~200 MB) |
| Realtime | `https://realtime.gtfs.de/realtime-free.pb` | ~50 MB nationwide; refreshed every 5 min (`GTFS_RT_REFRESH_SECONDS`) |
| Bike-sharing | nextbike Braunschweig GBFS | The only public sharing feed in the region; Goslar and Clausthal have none |
| On-demand | none yet | The zones in `data/on_demand_zones.json` are invented, so they are off until the operator's data arrives |

The map credits the data providers named in the feed's `attributions.txt`, as their licence requires.

**Development fixtures:** `data/gtfs_dev`, `data/gbfs_dev` and `data/on_demand_zones.json` hold approximate stops and invented timetables. The tests always use them, whatever `.env` says. Put raw exports under `data/raw/` (mounted at `/data/raw`, git-ignored).

---

## 🏗️ System Architecture

The system integrates several layers:

| Layer | Description |
|---|---|
| **Data Ingestion** | Real-time feeds from public transport, on-demand services, car-sharing, micromobility providers |
| **LLM Processing** | Interprets unstructured operational text (disruption reports, event announcements) |
| **Route Optimization** | Combines ML models with analytical routing algorithms |
| **Web Application** | User-facing app built on live data from multiple providers |

### Data Sources

| Source | Used For |
|---|---|
| Public transport timetables, stops & real-time data | Intermodal connections that work in practice |
| Utilisation of lines and vehicles | Capacity-aware recommendations, including peak times |
| Trip data from on-demand services | Fleet state, pooling opportunities, demand patterns |
| Car-sharing & micromobility offers | Additional modes within a single journey chain |
| Disruption reports & event announcements | Triggers for dynamic re-routing (arrives as free text) |

> **Note:** The project uses **real usage data — not synthetic data**.

---

## 🛠️ Skills Actually Required

### Must-Have (Hard Requirements)

| Skill | Why |
|---|---|
| **Python** (comfortable, not beginner) | Core language for the entire stack — ML pipelines, backend, data processing |
| **Git & collaborative repo workflows** | Shared codebase across an international team (Germany + Romania) |
| **Machine Learning fundamentals** | Building models on live data, optimization, prediction |
| **NLP / LLM understanding** | Interpreting unstructured disruption text with language models |

### Should-Have (Will Need to Learn/Use)

| Skill | Why |
|---|---|
| **Route optimization / graph algorithms** | Combining ML with analytical route optimisation |
| **API integration** | Consuming real-time data from multiple transport providers |
| **Web development** | Building the user-facing application on live data |
| **Data engineering** | Ingesting, cleaning, and processing multi-source real-time feeds |
| **Working across time zones / cultures** | International team across TU Clausthal and Babeș-Bolyai University |

### What You'll Learn On the Job

- Interpreting unstructured operational text with language models
- Combining ML with analytical route optimisation
- Building a web app on live, multi-provider data
- Taking a research question through to a running system

---

## 🤖 Can It Be Vibe-Coded?

### TL;DR: **Partially — but the core cannot.**

Here's a breakdown:

| Component | Vibe-Codeable? | Reasoning |
|---|---|---|
| **Web frontend / UI** | ✅ Yes | Standard web app — dashboards, maps, route display. AI tools excel here. |
| **API boilerplate & CRUD** | ✅ Yes | Data ingestion endpoints, basic server setup, etc. |
| **LLM prompt engineering** | 🟡 Partially | You can iterate on prompts with AI help, but understanding *what* the model needs to extract from disruption text requires domain knowledge. |
| **Route optimization engine** | ❌ No | This is the research core — combining ML with graph-based analytical routing on live, multi-modal data. Requires deep understanding of the problem space. |
| **Real-time data pipeline** | 🟡 Partially | Scaffolding can be generated, but handling edge cases with live multi-provider data (failures, format mismatches, latency) needs hands-on debugging. |
| **ML model training & evaluation** | ❌ No | Capacity prediction, demand patterns, pooling optimization — these require iterative experimentation on real data with domain understanding. |
| **Disruption handling logic** | ❌ No | The core innovation — dynamically re-routing across modes when incidents occur. This is research-level problem-solving. |

### The Verdict

**~30% vibe-codeable, ~70% requires real engineering & ML skill.**

You can vibe-code the shell (frontend, boilerplate, basic integrations), but the **LLM-guided routing**, **real-time optimization**, and **disruption handling** are the actual project — and those demand genuine understanding of ML, optimization, and systems thinking.

This is a **research project**, not a product sprint. If you can't reason about *why* the model makes a routing decision, you can't debug or improve it.

---

## 📅 Key Dates

| Event | Date |
|---|---|
| **Intensive Week — Cluj, Romania** | ~11 Nov – 19 Nov 2026 |
| **Intensive Week — Goslar, Germany** | ~01 Feb – 07 Feb 2027 |

> Flight and accommodation costs for the Cluj intensive week are covered.

---

## 📬 Contact

| Person | Role | Email |
|---|---|---|
| **Prof. Dr. Christian Bartelt** | Project Lead | — |
| **Paul König** | Supervisor | paul.koenig@tu-clausthal.de |

🔗 [More info & videos → core-network.ai/ai-team-projects](https://www.core-network.ai/ai-team-projects)
