import vertexai
from vertexai.generative_models import GenerativeModel

PROJECT_ID = "agentverse-488704"  
LOCATION = "us-central1"

vertexai.init(project=PROJECT_ID, location=LOCATION)

print("Loading Gemini 3.1 Pro Preview...")
model = GenerativeModel("gemini-2.5-flash")

print("Sending test prompt: 'What is the most critical chokepoint in global shipping?'\n")
response = model.generate_content("What is the most critical chokepoint in global shipping? Answer in one short sentence.")

print("VERTEX AI RESPONSE")
print(response.text)
print("==========================")