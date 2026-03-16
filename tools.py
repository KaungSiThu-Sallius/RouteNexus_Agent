import csv
import json
import os
import re
import requests
from functools import lru_cache
from google import genai
from google.genai import types

PROJECT_ID = "agentverse-488704"
LOCATION = "us-central1"


def get_llm_client():
    """Centralized LLM client creation for the RouteNexus system."""
    return genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
    )


def _parse_usd_amount(value: str) -> float:
    if not isinstance(value, str):
        return 0.0
    cleaned = re.sub(r"[^0-9.]", "", value)
    try:
        return float(cleaned) if cleaned else 0.0
    except Exception:
        return 0.0


def _format_usd_amount(value: float) -> str:
    return f"${value:,.2f}"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SHIPPING_DATA_PATH = os.path.join(DATA_DIR, "shipping_data.csv")
COMPLIANCE_DATA_PATH = os.path.join(DATA_DIR, "compliance_policies.csv")


@lru_cache(maxsize=128)
def get_region_risk_multiplier(region: str) -> float:
    """
    Use LLM to dynamically assess geopolitical and operational risk for a shipping region.
    Returns a multiplier (e.g., 1.0 = baseline, 1.3 = 30% higher risk).
    """
    try:
        client = get_llm_client()
        prompt = f"""You are a maritime risk analyst. Assess the current geopolitical and operational risk for shipping through: {region}

Consider:
- Geopolitical tensions (territorial disputes, conflicts, sanctions)
- Piracy and security threats
- Weather volatility and seasonal hazards
- Port congestion and infrastructure reliability
- Historical incident rates

Provide a risk multiplier as a single decimal number between 0.8 and 1.5, where:
- 1.0 = baseline global average risk
- < 1.0 = lower than average risk (stable, well-protected routes)
- > 1.0 = higher than average risk (elevated threats or volatility)

Respond with ONLY the decimal number, no explanation.

Example outputs: 1.22, 0.94, 1.34

Risk multiplier for {region}:"""
        
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=10)
        )
        multiplier_text = response.text.strip()
        multiplier = float(re.sub(r"[^0-9.]", "", multiplier_text))
        multiplier = max(0.8, min(1.5, multiplier))
        print(f"[LLM RISK] {region} → multiplier {multiplier}")
        return multiplier
    except Exception as e:
        print(f"[LLM RISK ERROR] {region}: {e}, defaulting to 1.0")
        return 1.0


@lru_cache(maxsize=256)
def extract_cargo_intent_from_query(message_text: str) -> str:
    """
    Use LLM to extract cargo type intent from user query.
    Returns a comma-separated list of cargo keywords to filter by, or empty string if no specific cargo mentioned.
    """
    try:
        client = get_llm_client()
        prompt = f"""You are a logistics query parser. Extract the cargo type(s) mentioned in this user query:

\"{message_text}\"

If the user mentions specific cargo types (e.g., semiconductors, crude oil, medical supplies, automotive parts), return them as a comma-separated list of lowercase keywords.

If the query is general and does not specify cargo types, return: NONE

Examples:
- "Analyze semiconductor cargo in Taiwan Strait" → semiconductor,chip,wafer
- "Check crude oil shipments in Red Sea" → crude,oil,petroleum
- "Assess risk for the Strait of Malacca" → NONE
- "Medical supplies through Suez Canal" → medical,medicine,pharmaceutical

Respond with ONLY the comma-separated keywords or NONE, no explanation.

Cargo keywords:"""
        
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=50)
        )
        result = response.text.strip()
        if result.upper() == "NONE":
            print(f"[LLM CARGO] No specific cargo detected in query")
            return ""
        print(f"[LLM CARGO] Extracted: {result}")
        return result.lower()
    except Exception as e:
        print(f"[LLM CARGO ERROR]: {e}, defaulting to empty")
        return ""


def normalize_inventory_result(region: str, parsed: dict) -> dict:
    shipments_exposed = parsed.get("shipments_exposed", 0)
    vessel_names = parsed.get("vessel_names", [])
    financial_exposure = parsed.get("financial_exposure_usd", "$0.00")
    critical_vessels = parsed.get("critical_vessels", 0)

    if not isinstance(vessel_names, list):
        vessel_names = []
    vessel_names = [str(name).strip() for name in vessel_names if str(name).strip()]

    try:
        shipments_exposed = int(shipments_exposed)
    except Exception:
        shipments_exposed = len(vessel_names)

    try:
        critical_vessels = int(critical_vessels)
    except Exception:
        critical_vessels = 0

    if shipments_exposed <= 0 and vessel_names:
        shipments_exposed = len(vessel_names)

    if shipments_exposed > 0 and not vessel_names:
        vessel_names = [f"{region} Cargo {i + 1}" for i in range(shipments_exposed)]

    if shipments_exposed <= 0:
        vessel_names = []
        critical_vessels = 0
        financial_exposure = "$0.00"

    financial_value = _parse_usd_amount(financial_exposure)
    if shipments_exposed > 0 and financial_value <= 0:
        financial_value = float(shipments_exposed) * 100000.0

    if critical_vessels > shipments_exposed:
        critical_vessels = shipments_exposed

    parsed["status"] = parsed.get("status", "SUCCESS")
    parsed["region"] = region
    parsed["shipments_exposed"] = shipments_exposed
    parsed["financial_exposure_usd"] = _format_usd_amount(financial_value)
    parsed["critical_vessels"] = critical_vessels
    parsed["vessel_names"] = vessel_names
    return parsed


def build_live_report(region: str, weather_raw: str, inventory_raw: str, policy_raw: str) -> dict:
    try:
        weather = json.loads(weather_raw) if isinstance(weather_raw, str) else weather_raw
    except Exception:
        weather = {"status": "ERROR", "message": str(weather_raw)}
    try:
        inventory = json.loads(inventory_raw) if isinstance(inventory_raw, str) else inventory_raw
    except Exception:
        inventory = {"status": "ERROR", "message": str(inventory_raw)}
    try:
        policy = json.loads(policy_raw) if isinstance(policy_raw, str) else policy_raw
    except Exception:
        policy = {"status": "ERROR", "message": str(policy_raw)}

    wind_speed = "Unknown"
    temp = "Unknown"
    weather_warning = "Unknown conditions"
    weekly_outlook = None
    weekly_warning = None

    if isinstance(weather, dict):
        live_data = weather.get("live_data", {})
        if isinstance(live_data, dict):
            wind_speed = live_data.get("wind_speed_knots", "Unknown")
            temp = live_data.get("temperature_celsius", "Unknown")
            weather_warning = live_data.get("warning", "Unknown conditions")
        
        forecast = weather.get("forecast_summary", {})
        if isinstance(forecast, dict):
            weekly_outlook = forecast.get("outlook")
            weekly_warning = forecast.get("weekly_warning")

    total_risk = "$0.00"
    critical_vessels = 0
    vessel_names = []
    
    if isinstance(inventory, dict):
        total_risk = inventory.get("financial_exposure_usd", "$0.00")
        try:
            critical_vessels = int(inventory.get("critical_vessels", 0))
        except (ValueError, TypeError):
            critical_vessels = 0
        v_names = inventory.get("vessel_names", [])
        if isinstance(v_names, list):
            vessel_names = v_names

    policy_status = policy.get("status", "ERROR")
    if policy_status == "POLICY_FOUND":
        policy_text = policy.get("relevant_policy", "Policy conflict detected.")
        policy_summary = f"WARNING: {policy_text}"
    elif policy_status == "CLEARED":
        policy_summary = "CLEARED: No policy conflicts detected."
    else:
        policy_summary = f"ERROR: {policy.get('message', 'Policy check failed.')}"

    mission_status = "READY"
    if policy_status == "ERROR":
        mission_status = "WARNING"
    if policy_status == "POLICY_FOUND" or critical_vessels > 0:
        mission_status = "CRITICAL"
    if isinstance(wind_speed, (int, float)) and float(wind_speed) > 30:
        mission_status = "CRITICAL"

    weather_summary = f"Wind {wind_speed} knots, temp {temp}°C. {weather_warning}"
    if weekly_outlook:
        weather_summary += f" {weekly_outlook}"
    if weekly_warning and weekly_warning not in weather_summary:
        weather_summary += f" {weekly_warning}."

    if policy_status == "POLICY_FOUND":
        recommendation = "Reroute requires compliance review before execution."
    elif mission_status == "CRITICAL":
        recommendation = "Escalate to operations and execute a controlled reroute with compliance approval."
    else:
        recommendation = "Conditions acceptable. Continue with monitored routing."

    if isinstance(vessel_names, list) and vessel_names:
        recommendation += f" Priority vessels impacted: {', '.join(str(v) for v in vessel_names[:3])}."

    return {
        "region": region,
        "mission_status": mission_status,
        "weather_summary": weather_summary,
        "total_risk_usd": total_risk,
        "policy_status": policy_summary,
        "final_recommendation": recommendation
    }


def format_chat_reply(user_input: str, data: dict) -> str:
    """Converts a raw JSON report into a human-readable conversational response."""
    question = user_input.lower()
    region = data.get("region", "the region")
    risk = data.get("mission_status", "UNKNOWN")
    weather = data.get("weather_summary", "No weather summary available.")
    financial = data.get("total_risk_usd", "N/A")
    policy = data.get("policy_status", "N/A")
    recommendation = data.get("final_recommendation", "No recommendation available.")

    if any(term in question for term in ["reroute", "another route", "different route", "should i reroute"]):
        if "warning" in policy.lower() or "not" in recommendation.lower() or "review" in recommendation.lower():
            return (
                f"Not yet. For **{region}**, rerouting should **not be executed immediately** without compliance review.\n\n"
                f"- **Current policy:** {policy}\n"
                f"- **Risk level:** {risk}\n"
                f"- **Recommendation:** {recommendation}"
            )
        return (
            f"Yes, rerouting is acceptable for **{region}** based on the latest report.\n\n"
            f"- **Risk level:** {risk}\n"
            f"- **Compliance:** {policy}\n"
            f"- **Recommendation:** {recommendation}"
        )

    if any(term in question for term in ["risk", "any risk", "safe", "danger"]):
        return (
            f"For cargo moving through **{region}**, the current risk level is **{risk}**.\n\n"
            f"- **Weather:** {weather}\n"
            f"- **Financial exposure:** {financial}\n"
            f"- **Compliance:** {policy}\n\n"
            f"**Recommendation:** {recommendation}"
        )

    if any(term in question for term in ["weather", "storm", "wind"]):
        return f"The current weather outlook for **{region}** is: {weather}"

    if any(term in question for term in ["compliance", "policy", "allowed", "permit"]):
        return f"The current compliance status for **{region}** is: {policy}"

    if any(term in question for term in ["money", "financial", "cost", "exposure", "value"]):
        return f"The current estimated financial exposure for **{region}** is **{financial}**."

    return (
        f"Here’s the latest assessment for **{region}**:\n\n"
        f"- **Risk level:** {risk}\n"
        f"- **Weather:** {weather}\n"
        f"- **Financial exposure:** {financial}\n"
        f"- **Compliance:** {policy}\n\n"
        f"**Recommendation:** {recommendation}"
    )


def get_live_marine_weather(latitude: float, longitude: float) -> str:
    print(f"\n[TOOL EXECUTION] DisruptionScout is pinging live weather satellite for coords: {latitude}, {longitude}...")
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}"
        f"&longitude={longitude}"
        "&current=temperature_2m,wind_speed_10m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
        "&forecast_days=7"
        "&timezone=auto"
        "&wind_speed_unit=kn"
    )

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        current = data.get("current", {})
        daily = data.get("daily", {})
        wind_speed = current.get("wind_speed_10m", "Unknown")
        temp = current.get("temperature_2m", "Unknown")
        daily_max_wind = daily.get("wind_speed_10m_max", []) or []
        daily_max_temp = daily.get("temperature_2m_max", []) or []
        daily_min_temp = daily.get("temperature_2m_min", []) or []

        weekly_peak_wind = max(daily_max_wind) if daily_max_wind else wind_speed
        weekly_max_temp = max(daily_max_temp) if daily_max_temp else temp
        weekly_min_temp = min(daily_min_temp) if daily_min_temp else temp

        try:
            weekly_peak_wind_value = float(weekly_peak_wind)
        except Exception:
            weekly_peak_wind_value = 0.0

        weekly_outlook = (
            f"7-day outlook: peak winds up to {weekly_peak_wind} knots, "
            f"temperature range {weekly_min_temp}°C to {weekly_max_temp}°C."
        )

        report = json.dumps({
            "location": {"lat": latitude, "lon": longitude},
            "status": "SUCCESS",
            "live_data": {
                "wind_speed_knots": wind_speed,
                "temperature_celsius": temp,
                "warning": "High risk for cargo vessels" if float(wind_speed) > 30 else "Normal conditions"
            },
            "forecast_summary": {
                "weekly_peak_wind_knots": weekly_peak_wind,
                "weekly_min_temp_celsius": weekly_min_temp,
                "weekly_max_temp_celsius": weekly_max_temp,
                "weekly_warning": "Elevated weather risk expected this week" if weekly_peak_wind_value > 25 else "No major weather escalation expected this week",
                "outlook": weekly_outlook,
            },
        })
        print(f"[TOOL RESPONSE] Data retrieved: {report}\n")
        return report
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})


def _read_csv_rows(file_path: str) -> list[dict]:
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _tokenize_query(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _extract_cargo_filters(message_text: str) -> set[str]:
    """
    Extract cargo filter keywords using LLM.
    Returns a set of lowercase keywords to match against cargo_type in CSV.
    """
    cargo_keywords_str = extract_cargo_intent_from_query(message_text)
    if not cargo_keywords_str:
        return set()
    return {kw.strip() for kw in cargo_keywords_str.split(",") if kw.strip()}


def check_inventory_exposure(message_text: str, region: str) -> str:
    print(f"[DATASET] InventoryAnalyst scanning shipping_data.csv for region: {region}")
    try:
        rows = _read_csv_rows(SHIPPING_DATA_PATH)
        if not rows:
            raise FileNotFoundError(f"Missing shipping dataset at {SHIPPING_DATA_PATH}")

        query_tokens = _tokenize_query(message_text)
        cargo_filters = _extract_cargo_filters(message_text)
        region_lower = region.lower()
        matched_rows = []

        for row in rows:
            row_region = str(row.get("current_region", "")).lower()
            row_corridor = str(row.get("route_corridor", "")).lower()
            row_cargo = str(row.get("cargo_type", "")).lower()
            region_match = region_lower in row_region or region_lower in row_corridor
            cargo_match = (
                row_cargo in cargo_filters
                if cargo_filters
                else (not query_tokens or any(token in row_cargo for token in query_tokens))
            )
            if region_match and cargo_match:
                matched_rows.append(row)

        if not matched_rows:
            for row in rows:
                row_region = str(row.get("current_region", "")).lower()
                row_corridor = str(row.get("route_corridor", "")).lower()
                if region_lower in row_region or region_lower in row_corridor:
                    matched_rows.append(row)

        if not matched_rows:
            parsed = normalize_inventory_result(region, {
                "status": "NO_MATCH",
                "region": region,
                "shipments_exposed": 0,
                "financial_exposure_usd": "$0.00",
                "critical_vessels": 0,
                "vessel_names": [],
            })
            return json.dumps(parsed)

        shipments_exposed = len(matched_rows)
        financial_total = 0.0
        critical_vessels = 0
        vessel_names = []
        region_multiplier = get_region_risk_multiplier(region)
        for row in matched_rows:
            row_value = _parse_usd_amount(str(row.get("value_usd", "0")))
            financial_total += row_value * region_multiplier
            if str(row.get("priority", "")).lower() == "critical":
                critical_vessels += 1
            vessel_name = str(row.get("vessel_name", "")).strip()
            if vessel_name:
                vessel_names.append(vessel_name)

        parsed = normalize_inventory_result(region, {
            "status": "SUCCESS",
            "region": region,
            "shipments_exposed": shipments_exposed,
            "financial_exposure_usd": _format_usd_amount(financial_total),
            "critical_vessels": critical_vessels,
            "vessel_names": vessel_names[:10],
        })
        print(f"[DATASET] InventoryAnalyst matched {shipments_exposed} rows for {region}")
        return json.dumps(parsed)
    except Exception as e:
        return json.dumps({
            "status": "ERROR",
            "region": region,
            "shipments_exposed": 0,
            "financial_exposure_usd": "$0.00",
            "critical_vessels": 0,
            "vessel_names": [],
            "message": str(e),
        })


def check_policy_compliance(message_text: str, region: str) -> str:
    print(f"[DATASET] ComplianceGuardian scanning compliance_policies.csv for region: {region}")
    try:
        rows = _read_csv_rows(COMPLIANCE_DATA_PATH)
        if not rows:
            raise FileNotFoundError(f"Missing compliance dataset at {COMPLIANCE_DATA_PATH}")

        query_tokens = _tokenize_query(f"{message_text} {region}")
        best_row = None
        best_score = -1

        for row in rows:
            region_value = str(row.get("region", "")).lower()
            cargo_value = str(row.get("cargo_keyword", "")).lower()
            category_value = str(row.get("category", "")).lower()
            policy_text = str(row.get("policy_text", "")).lower()

            score = 0
            if region.lower() in region_value or region_value in region.lower():
                score += 5
            if cargo_value and cargo_value in message_text.lower():
                score += 4
            score += sum(1 for token in query_tokens if token in policy_text or token in category_value)

            if score > best_score:
                best_score = score
                best_row = row

        if not best_row or best_score <= 0:
            return json.dumps({"status": "CLEARED", "message": "No policy conflicts detected."})

        return json.dumps({
            "status": "POLICY_FOUND",
            "policy_id": best_row.get("policy_id", "POL-UNKNOWN") if best_row else "POL-UNKNOWN",
            "region": best_row.get("region", region) if best_row else region,
            "category": best_row.get("category", "General") if best_row else "General",
            "severity": best_row.get("severity", "MEDIUM") if best_row else "MEDIUM",
            "relevant_policy": best_row.get("policy_text", "Policy review required.") if best_row else "Policy review required.",
            "confidence_score": float(round(float(min(0.99, 0.45 + (float(best_score) * 0.05))), 2)),
        })
    except Exception as e:
        return json.dumps({
            "status": "ERROR",
            "message": str(e),
        })


def infer_region_and_coords(message_text: str) -> tuple[str, tuple[float, float]]:
    try:
        # Reusing the project configuration typical for this file
        client = get_llm_client()
        prompt = f"""You are a geocoding analyst for a logistics system.
Extract the primary maritime region or choke point mentioned in the user's message, and provide its approximate latitude and longitude.

User message:"{message_text}"

Respond EXACTLY with a JSON object in this format:
{{
  "region": "Region Name",
  "lat": 12.34,
  "lon": 56.78
}}

If no specific region is recognized or specified, default to:
{{
  "region": "Strait of Malacca",
  "lat": 4.0,
  "lon": 100.5
}}

Do not include markdown blocks or any other text, just the JSON object.
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=100,
            ),
        )
        text = getattr(response, "text", None) or ""
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            parsed = json.loads(match.group())
            return parsed.get("region", "Strait of Malacca"), (float(parsed.get("lat", 4.0)), float(parsed.get("lon", 100.5)))
    except Exception as e:
        print(f"[LLM GEOCODE ERROR]: {e}")
    return "Strait of Malacca", (4.0, 100.5)


def synthesize_report_with_llm(message_text: str, region: str, weather_raw: str, inventory_raw: str, policy_raw: str) -> dict:
    """Uses LLM to synthesize tool outputs into a structured mission report."""
    fallback = build_live_report(region, weather_raw, inventory_raw, policy_raw)
    prompt = f"""
You are LogisticsDirector for RouteNexus.
Use the live tool outputs below to produce exactly one JSON object and nothing else.

User mission:
{message_text}

Region:
{region}

Weather tool output:
{weather_raw}

Inventory tool output:
{inventory_raw}

Policy tool output:
{policy_raw}

Return exactly this schema:
{{
  "region": "region name",
  "mission_status": "CRITICAL or WARNING or READY",
  "weather_summary": "brief weather explanation",
  "total_risk_usd": "financial exposure amount",
  "policy_status": "compliance summary (Must start with 'CLEARED:' if status is CLEARED, else 'WARNING:')",
  "final_recommendation": "short actionable recommendation"
}}
"""
    try:
        client = get_llm_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=300,
            ),
        )
        text = getattr(response, "text", None) or ""
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return fallback
        parsed = json.loads(match.group())
        required_keys = {
            "region",
            "mission_status",
            "weather_summary",
            "total_risk_usd",
            "policy_status",
            "final_recommendation",
        }
        if not required_keys.issubset(parsed.keys()):
            return fallback
        return parsed
    except Exception:
        return fallback


def should_reanalyze_command(user_input: str) -> bool:
    """Classifies user intent: True if a new analysis is needed, False for follow-ups."""
    try:
        client = get_llm_client()
        prompt = f"""You are an intent classification assistant for a logistics system.
Determine if the user's message is asking to run a new logistics analysis (e.g., checking a completely new route, assessing risk for a new location, analyzing a new cargo scenario).

User message:
"{user_input}"

Respond with EXACTLY "TRUE" if they are providing a new location or explicit scenario that requires a fresh data pull.
Respond with EXACTLY "FALSE" if they are asking a follow-up question (like "should I reroute?", "is the risk high?", "what should I do?"), asking for advice based on the current report, or engaging in general chat.
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=10,
            ),
        )
        text = (getattr(response, "text", None) or "").strip().upper()
        return "TRUE" in text
    except Exception as e:
        print(f"[LLM INTENT ERROR]: {e}")
        # Fallback to stricter keyword check if LLM fails
        lowered = user_input.lower()
        return any(t in lowered for t in ["analyze ", "assess ", "check ", "new route", "alternative route", "via "])


def generate_chat_reply_with_llm(user_input: str, data: dict) -> str:
    """Generates a natural chat reply based on the mission report data."""
    fallback = format_chat_reply(user_input, data)
    prompt = f"""
You are the LogisticsDirector chatting with an operations user.
Answer naturally and concisely based on the latest mission report.
Your answer should be professional, data-driven, and actionable.

If the user asks about a reroute, check the 'policy_status' and 'final_recommendation' in the report carefully. 
If there is a compliance warning or the recommendation says to review, advise against immediate execution.

User question:
{user_input}

Mission report:
{json.dumps(data, indent=2)}
"""
    try:
        client = get_llm_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=220,
            ),
        )
        text = getattr(response, "text", None) or ""
        cleaned = text.strip()
        return cleaned or fallback
    except Exception:
        return fallback