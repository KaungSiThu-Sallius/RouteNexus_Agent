import os
import vertexai
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.genai import types
from tools import (
    get_live_marine_weather, 
    check_inventory_exposure, 
    check_policy_compliance, 
    PROJECT_ID
)

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
        max_output_tokens=200,
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

# Agent 4: LogisticsDirector
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
