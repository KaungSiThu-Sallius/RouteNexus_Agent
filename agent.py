import os
import json
import re
import vertexai
from google import genai
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.genai import types
from tools import get_live_marine_weather, check_inventory_exposure, check_policy_compliance

PROJECT_ID = "agentverse-488704"
MODEL_NAME = "gemini-2.5-flash-lite"

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

vertexai.init(project=PROJECT_ID, location="us-central1")

weather_tool = FunctionTool(get_live_marine_weather)
inventory_tool = FunctionTool(check_inventory_exposure)
policy_tool = FunctionTool(check_policy_compliance)

# Agent 1: DisruptionScout
scout = LlmAgent(
    name="DisruptionScout",
    model=MODEL_NAME,
    description="Analyzes live marine weather and disruption signals.",
    instruction="""When asked about a route or region, use the live weather tool and summarize operational weather risk.""",
    tools=[weather_tool],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=180,
    ),
)

# Agent 2: InventoryAnalyst
analyst = LlmAgent(
    name="InventoryAnalyst",
    model=MODEL_NAME,
    description="Estimates cargo and vessel financial exposure.",
    instruction="""When asked about a mission and region, call the inventory analysis tool and summarize cargo exposure, affected vessels, and urgency.""",
    tools=[inventory_tool],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=180,
    ),
)

# Agent 3: ComplianceGuardian
guardian = LlmAgent(
    name="ComplianceGuardian",
    model=MODEL_NAME,
    description="Assesses logistics policy and compliance constraints.",
    instruction="""When asked about a mission and region, call the compliance analysis tool and summarize whether routing is cleared or requires review.""",
    tools=[policy_tool],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=180,
    ),
)

director = LlmAgent(
    name="LogisticsDirector",
    model=MODEL_NAME,
    description="Coordinates the RouteNexus logistics analysis swarm.",
    instruction="""Coordinate weather, inventory, and compliance analysis for logistics missions and produce an operationally clear final answer.""",
    tools=[weather_tool, inventory_tool, policy_tool],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=500,
    ),
)


def infer_region_and_coords(message_text: str) -> tuple[str, tuple[float, float]]:
    text = message_text.lower()
    if "taiwan strait" in text or "taiwan" in text or "taipei" in text or "kaohsiung" in text:
        return "Taiwan Strait", (24.0, 119.5)
    if "south china sea" in text or "manila" in text or "hong kong" in text:
        return "South China Sea", (15.0, 115.0)
    if "east china sea" in text or "shanghai" in text or "okinawa" in text:
        return "East China Sea", (29.0, 125.0)
    if "sea of japan" in text or "busan" in text or "japan sea" in text:
        return "Sea of Japan", (39.0, 135.0)
    if "myanmar" in text or "yangon" in text or "thailand" in text or "bangkok" in text or "andaman" in text:
        return "Andaman Sea", (14.0, 98.0)
    if "thai" in text or "gulf of thailand" in text:
        return "Gulf of Thailand", (12.5, 101.0)
    if "london" in text or "uk" in text or "united kingdom" in text or "english channel" in text:
        return "English Channel", (50.0, 0.0)
    if "atlantic" in text or ("us" in text and "london" in text):
        return "North Atlantic", (41.0, -45.0)
    if "malacca" in text:
        return "Strait of Malacca", (4.0, 100.5)
    if "singapore" in text:
        return "Strait of Malacca", (4.0, 100.5)
    if "red sea" in text:
        return "Red Sea", (20.0, 38.0)
    if "suez" in text:
        return "Suez Canal", (30.0, 32.3)
    if "panama" in text:
        return "Panama Canal", (9.0, -79.6)
    return "Strait of Malacca", (4.0, 100.5)


def get_llm_client():
    return genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location="us-central1",
    )


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

    wind_speed = weather.get("live_data", {}).get("wind_speed_knots", "Unknown")
    temp = weather.get("live_data", {}).get("temperature_celsius", "Unknown")
    weather_warning = weather.get("live_data", {}).get("warning", "Unknown conditions")
    forecast = weather.get("forecast_summary", {})
    weekly_outlook = forecast.get("outlook")
    weekly_warning = forecast.get("weekly_warning")
    total_risk = inventory.get("financial_exposure_usd", "$0.00")
    critical_vessels = inventory.get("critical_vessels", 0)
    vessel_names = inventory.get("vessel_names", [])

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

    if vessel_names:
        recommendation += f" Priority vessels impacted: {', '.join(vessel_names[:3])}."

    return {
        "region": region,
        "mission_status": mission_status,
        "weather_summary": weather_summary,
        "total_risk_usd": total_risk,
        "policy_status": policy_summary,
        "final_recommendation": recommendation,
    }


def format_chat_reply(user_input: str, data: dict) -> str:
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


def synthesize_report_with_llm(message_text: str, region: str, weather_raw: str, inventory_raw: str, policy_raw: str) -> dict:
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
  "policy_status": "compliance summary",
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


def generate_chat_reply_with_llm(user_input: str, data: dict) -> str:
    fallback = format_chat_reply(user_input, data)
    question = user_input.lower()
    if any(term in question for term in ["reroute", "another route", "different route", "should i reroute"]):
        return fallback
    prompt = f"""
You are the LogisticsDirector chatting with an operations user.
Answer naturally and concisely based on the latest mission report.
Do not output JSON.

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