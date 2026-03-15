# RouteNexus

RouteNexus is a Streamlit-based logistics risk analysis app built for hackathon demos.

It combines:
- real marine weather data
- local compliance policies from CSV
- local shipping exposure data from CSV
- a visible multi-agent structure in `agent.py`
- persistent chat/session history in Cloud SQL

## What the app does

A user enters a logistics command such as:
- route risk analysis
- reroute evaluation
- cargo exposure review
- compliance review

The app then produces a structured report with:
- risk level
- compact financial exposure
- compliance outcome
- weather summary
- reroute recommendation

## Current architecture

### UI layer
- `app.py`

Handles:
- Streamlit interface
- chat flow
- session state
- sidebar chat history
- live trace log display
- dashboard rendering

### Agent layer
- `agent.py`

Contains visible agents for judge/demo purposes:
- `DisruptionScout`
- `InventoryAnalyst`
- `ComplianceGuardian`
- `LogisticsDirector`

Also contains:
- region inference
- final report synthesis
- director chat reply generation

### Tool layer
- `tools.py`

Contains the operational tools:
- `get_live_marine_weather()`
- `check_inventory_exposure()`
- `check_policy_compliance()`

## Data files

### Shipping dataset
- `data/shipping_data.csv`

This is an artificial demo dataset designed to simulate large-scale logistics activity.

It includes fields such as:
- vessel ID
- vessel name
- current region
- route corridor
- cargo type
- cargo value
- priority
- status
- origin port
- destination port

### Compliance dataset
- `data/compliance_policies.csv`

This file contains editable compliance rules.

You can add new rows here to extend policy coverage without changing Python code.

Fields:
- `policy_id`
- `region`
- `cargo_keyword`
- `category`
- `severity`
- `policy_text`

## Live vs local components

### Real API
- weather uses the Open-Meteo API

### Local CSV-backed logic
- inventory exposure uses `shipping_data.csv`
- compliance checks use `compliance_policies.csv`

### Cloud persistence
- chat/session history is stored through Cloud SQL using:
  - `cloud_sql_session.py`

## How a request flows

1. The user enters a command in the Streamlit UI.
2. `app.py` sends the command into the RouteNexus flow.
3. `agent.py` infers the region and coordinates.
4. `tools.py` fetches live weather from Open-Meteo.
5. `tools.py` scans `shipping_data.csv` for cargo exposure.
6. `tools.py` scans `compliance_policies.csv` for matching policy rules.
7. `agent.py` synthesizes the final report.
8. The app shows the result in the dashboard and stores history in Cloud SQL.

## Running the app

From the project folder:

```bash
streamlit run app.py
```

## Project files

- `app.py` — Streamlit app and UI flow
- `agent.py` — visible agent structure and orchestration helpers
- `tools.py` — real weather + local CSV tools
- `cloud_sql_session.py` — Cloud SQL-backed session storage
- `data/shipping_data.csv` — local shipping dataset
- `data/compliance_policies.csv` — local compliance dataset
- `sample_prompts.txt` — example prompts for demos

## Example prompts

See:
- `sample_prompts.txt`

## Notes for judges

This project intentionally exposes the agent and tool layers clearly:
- agents are defined in `agent.py`
- tools are defined in `tools.py`
- datasets are visible in `data/`

The current build is designed to demonstrate:
- agent-style orchestration
- real API integration
- editable local policy datasets
- scalable shipping dataset integration pattern
- chat/session persistence

## Editing compliance rules

To add a new compliance rule:
1. Open `data/compliance_policies.csv`
2. Add a new row with region, cargo keyword, severity, and policy text
3. Re-run the app or submit a matching prompt

## Editing shipping data

To add more cargo records:
1. Open `data/shipping_data.csv`
2. Add new shipping rows
3. Use prompts that reference the target region or cargo type

## Demo tips

Best prompts for demo:
- Taiwan Strait semiconductor cargo
- Red Sea crude oil risk
- Strait of Malacca reroute scenario
- Myanmar/Thailand cargo screening scenario

## Status

Current project state:
- weather = live API
- inventory = local CSV
- compliance = local CSV
- history = Cloud SQL
- UI = Streamlit

