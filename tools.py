import csv
import json
import os
import re
import requests
from functools import lru_cache
from google import genai
from google.genai import types


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
        client = genai.Client(vertexai=True, project="agentverse-488704", location="us-central1")
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
        client = genai.Client(vertexai=True, project="agentverse-488704", location="us-central1")
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
            "policy_id": best_row.get("policy_id", "POL-UNKNOWN"),
            "region": best_row.get("region", region),
            "category": best_row.get("category", "General"),
            "severity": best_row.get("severity", "MEDIUM"),
            "relevant_policy": best_row.get("policy_text", "Policy review required."),
            "confidence_score": round(min(0.99, 0.45 + (best_score * 0.05)), 2),
        })
    except Exception as e:
        return json.dumps({
            "status": "ERROR",
            "message": str(e),
        })