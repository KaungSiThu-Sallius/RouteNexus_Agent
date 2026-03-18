import csv
import json
import os
import re
import requests
from functools import lru_cache
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

def get_llm_model(temperature=0.0, max_output_tokens=4096):
    """Centralized LLM model creation using environment-based config."""
    p = os.getenv("GOOGLE_CLOUD_PROJECT", "agentverse-488704")
    l = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    vertexai.init(project=p, location=l)
    return GenerativeModel("gemini-2.5-flash")

def _parse_usd_amount(value: str) -> float:
    if not isinstance(value, str):
        return 0.0
    cleaned = re.sub(r"[^0-9.]", "", value)
    try:
        return float(cleaned) if cleaned else 0.0
    except Exception:
        return 0.0

def _format_usd_amount(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    return f"${value:,.2f}"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SHIPPING_DATA_PATH = os.path.join(DATA_DIR, "shipping_data.csv")
COMPLIANCE_DATA_PATH = os.path.join(DATA_DIR, "compliance_policies.csv")

# Removed lru_cache to ensure dynamic LLM-based risk multipliers per request
def get_region_risk_multiplier(region: str) -> float:
    try:
        model = get_llm_model()
        prompt = f"Assess maritime risk multiplier (0.8-1.5) for region: {region}. Return ONLY the number."
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.0, max_output_tokens=1024)
        )
        multiplier_text = response.text.strip()
        multiplier = float(re.sub(r"[^0-9.]", "", multiplier_text))
        multiplier = max(0.8, min(1.5, multiplier))
        print(f"[LLM RISK] {region} → multiplier {multiplier}")
        return multiplier
    except Exception as e:
        # Stable hash-based fallback so different regions get different multipliers
        import hashlib
        h = int(hashlib.md5(region.encode()).hexdigest(), 16)
        fallback = 1.0 + (h % 30) / 100.0 # 1.00 to 1.30
        print(f"[LLM RISK ERROR] {region}: {e}, defaulting to hash-based {fallback:.2f}")
        return fallback

# Removed lru_cache to ensure dynamic cargo extraction per query
def extract_cargo_intent_from_query(message_text: str) -> str:
    try:
        model = get_llm_model()
        prompt = f"Extract cargo type keywords from: \"{message_text}\". Return comma-separated lowercase keywords or 'NONE'."
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.0, max_output_tokens=1024)
        )
        result = response.text.strip()
        if result.upper() == "NONE":
            return ""
        return result.lower()
    except Exception:
        return ""

def normalize_inventory_result(region: str, parsed: dict) -> dict:
    raw_names = parsed.get("vessel_names", [])
    if not isinstance(raw_names, list): raw_names = []
    vessel_names = [str(n).strip() for n in raw_names if str(n).strip()]
    
    shipments_count = len(vessel_names) or int(float(parsed.get("shipments_exposed", 0)))
    financial_val = _parse_usd_amount(parsed.get("financial_exposure_usd", "$0"))
    if shipments_count > 0 and financial_val <= 0:
        financial_val = float(shipments_count) * 125000.0
        
    return {
        "status": parsed.get("status", "SUCCESS"),
        "region": region,
        "shipments_exposed": shipments_count,
        "financial_exposure_usd": _format_usd_amount(financial_val),
        "critical_vessels": int(float(parsed.get("critical_vessels", 0))),
        "vessel_names": vessel_names
    }

def build_live_report(region: str, weather_raw: str, inventory_raw: str, policy_raw: str) -> dict:
    def safe_load(raw):
        if isinstance(raw, dict): return raw
        try: return json.loads(raw)
        except: return {}

    weather = safe_load(weather_raw)
    inventory = safe_load(inventory_raw)
    policy = safe_load(policy_raw)

    live_weather = weather.get("live_data", {})
    wind = live_weather.get("wind_speed_knots", 0)
    
    exposure = inventory.get("financial_exposure_usd", "$0")
    policy_msg = policy.get("message", "Check complete.")
    policy_status = policy.get("status", "CLEARED")
    
    policy_summary = f"{'WARNING:' if policy_status != 'CLEARED' else 'CLEARED:'} {policy_msg}"
    
    weather_msg = f"Wind {wind} knots. {live_weather.get('warning', '')}"
    if "temperature_celsius" in live_weather:
        weather_msg += f" Temperature: {live_weather['temperature_celsius']}°C"
        
    return {
        "region": region,
        "mission_status": "READY" if policy_status == "CLEARED" else "WARNING",
        "weather_summary": weather_msg,
        "financial_exposure": exposure,
        "compliance_status": "CLEARED" if policy_status == "CLEARED" else "WARNING",
        "policy_status": policy_summary,
        "final_recommendation": "Monitor conditions."
    }

def synthesize_report_with_llm(message_text: str, region: str, weather_raw: str, inventory_raw: str, policy_raw: str) -> dict:
    fallback = build_live_report(region, weather_raw, inventory_raw, policy_raw)
    prompt = f"Synthesize a logistics report for region {region} based on these inputs: Weather:{weather_raw}, Inventory:{inventory_raw}, Policy:{policy_raw}. Return ONLY JSON matching this schema: {{\"region\":\"\",\"mission_status\":\"\",\"weather_summary\":\"\",\"total_risk_usd\":\"\",\"policy_status\":\"\",\"final_recommendation\":\"\"}}"
    try:
        model = get_llm_model(temperature=0.1)
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.1, max_output_tokens=4096)
        )
        match = re.search(r"\{[\s\S]*\}", response.text)
        if not match: return fallback
        parsed = json.loads(match.group())
        if "financial_exposure" not in parsed: parsed["financial_exposure"] = parsed.get("total_risk_usd", "$0")
        if "compliance_status" not in parsed:
            ps = str(parsed.get("policy_status", "")).upper()
            parsed["compliance_status"] = "CLEARED" if "CLEARED" in ps else "WARNING"
        return parsed
    except Exception as e:
        print(f"[LLM SYNTH ERROR] {e}")
        return fallback

def infer_region_and_coords(message_text: str) -> tuple[str, tuple[float, float]]:
    try:
        from geopy.geocoders import Nominatim
    except ImportError:
        Nominatim = None

    known_regions = []
    try:
        seen = set()
        for r in _read_csv_rows(SHIPPING_DATA_PATH):
            reg = r.get("current_region", "").strip()
            if reg and reg not in seen:
                known_regions.append(reg)
                seen.add(reg)
    except: pass
    
    # Try direct substring match first
    detected_region = "Unknown Region"
    lowered_msg = message_text.lower()
    for r in sorted(known_regions, key=len, reverse=True):
        if str(r).lower() in lowered_msg:
            detected_region = str(r)
            break
            
    # LLM-based inference if substring match fails
    if detected_region == "Unknown Region":
        try:
            model = get_llm_model()
            prompt = f"Given this query: \"{message_text}\" and this list of candidate maritime regions: {known_regions}, which ONE region is most relevant? Return ONLY the region name or 'None'."
            response = model.generate_content(
                prompt,
                generation_config=GenerationConfig(temperature=0.0, max_output_tokens=1024)
            )
            res = response.text.strip()
            if res in known_regions:
                detected_region = res
        except Exception: pass
            
    lat, lon = 0.0, 0.0
    
    if Nominatim and detected_region != "Unknown Region":
        try:
            geolocator = Nominatim(user_agent="RouteNexus_Agent_V1")
            location = geolocator.geocode(detected_region, timeout=5)
            if location:
                lat, lon = location.latitude, location.longitude
        except Exception as e:
            print(f"[GEOCODE ERROR] {e}")

    return detected_region, (lat, lon)

def should_reanalyze_command(user_input: str) -> bool:
    try:
        model = get_llm_model()
        prompt = f"Is this a request for a NEW logistics analysis? \"{user_input}\". Return ONLY 'TRUE' or 'FALSE'."
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.0, max_output_tokens=1024)
        )
        return "TRUE" in response.text.upper()
    except Exception as e:
        print(f"[LLM REANALYZE ERROR] {e}")
        lowered = user_input.lower()
        return any(t in lowered for t in ["analyze", "check", "assess", "new route"])

def generate_chat_reply_with_llm(user_input: str, data: dict) -> str:
    try:
        model = get_llm_model(temperature=0.2)
        # Add more context to prompt to help LLM stay focused
        context = json.dumps(data, indent=2)
        prompt = (
            f"You are the LogisticsDirector for RouteNexus. Use the following mission report "
            f"to answer the user's question concisely and professionally.\n\n"
            f"Mission Report:\n{context}\n\n"
            f"User Question: \"{user_input}\"\n\n"
            f"Instructions:\n"
            f"1. Be concise but informative.\n"
            f"2. Reference specific data from the report (weather, risk, etc.).\n"
            f"3. Do not use generic phrases like 'Analysis complete'.\n"
            f"4. If the report has an error, acknowledge it.\n\n"
            f"Response:"
        )
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(temperature=0.2, max_output_tokens=4096)
        )
        reply = response.text.strip()
        if not reply:
            raise ValueError("Empty response from LLM")
        return reply
    except Exception as e:
        print(f"[LLM CHAT ERROR] {e}")
        # Return a more helpful error related response rather than just a generic completion message
        return f"I encountered an issue coordinating with the intelligence swarm ({str(e)}). Please review the dashboard metrics above for the latest operational status."

def get_live_marine_weather(latitude: float, longitude: float) -> str:
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
        resp = requests.get(url, timeout=10)
        data = resp.json()
        curr = data.get("current", {})
        daily = data.get("daily", {})
        wind = curr.get("wind_speed_10m", 0)
        
        # Forecast data for enrichment
        daily_max_wind = daily.get("wind_speed_10m_max", [])
        peak_wind = max(daily_max_wind) if daily_max_wind else wind
        
        return json.dumps({
            "live_data": {
                "wind_speed_knots": wind, 
                "temperature_celsius": curr.get("temperature_2m", 0), 
                "warning": "Normal" if wind < 25 else "CAUTION: Elevated winds"
            },
            "forecast_summary": {
                "weekly_peak_wind_knots": peak_wind,
                "outlook": f"7-day outlook: winds up to {peak_wind} knots."
            },
            "status": "SUCCESS"
        })
    except:
        return json.dumps({"status": "ERROR"})

def _read_csv_rows(file_path: str) -> list[dict]:
    if not os.path.exists(file_path): return []
    with open(file_path, "r", encoding="utf-8") as f: return list(csv.DictReader(f))

def check_inventory_exposure(message_text: str, region: str) -> str:
    try:
        rows = _read_csv_rows(SHIPPING_DATA_PATH)
        matched = [r for r in rows if region.lower() in str(r.get("current_region","")).lower() or region.lower() in str(r.get("route_corridor","")).lower()]
        
        # New: Filter by cargo intent if present
        cargo_intent = extract_cargo_intent_from_query(message_text)
        if cargo_intent:
            keywords = [k.strip() for k in cargo_intent.split(",") if k.strip()]
            if keywords:
                matched = [r for r in matched if any(k in str(r.get("cargo_type","")).lower() for k in keywords)]
        
        exposure = sum(_parse_usd_amount(r.get("value_usd", "0")) for r in matched) * get_region_risk_multiplier(region)
        return json.dumps({
            "status": "SUCCESS",
            "region": region,
            "cargo_filter": cargo_intent or "NONE",
            "shipments_exposed": len(matched),
            "financial_exposure_usd": _format_usd_amount(exposure),
            "critical_vessels": sum(1 for r in matched if "critical" in str(r.get("priority","")).lower()),
            "vessel_names": [r.get("vessel_name") for r in matched[:10]]
        })
    except Exception as e:
        return json.dumps({"status": "ERROR", "message": str(e)})

def check_policy_compliance(message_text: str, region: str) -> str:
    try:
        rows = _read_csv_rows(COMPLIANCE_DATA_PATH)
        # Simple match for region
        for r in rows:
            if region.lower() in str(r.get("region","")).lower():
                return json.dumps({"status": "POLICY_FOUND", "message": r.get("policy_text"), "severity": r.get("severity")})
        return json.dumps({"status": "CLEARED", "message": "No specific conflicts."})
    except:
        return json.dumps({"status": "ERROR"})