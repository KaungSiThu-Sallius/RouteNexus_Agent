# RouteNexus: Intelligent Maritime Logistics Analysis

RouteNexus is a high-performance logistics command center that leverages a multi-agent AI system to provide real-time strategic analysis of maritime shipping routes. Powered by **Google Vertex AI** and **Streamlit**, it transforms complex logistics data into actionable insights.

---

## 🚀 Key Features

- **Intelligent Command Processing**: Natural language interface for complex logistics queries (e.g., "Analyze semiconductor risk in the Taiwan Strait").
- **Dynamic Risk Assessment**: Real-time evaluation of meteorlogical, geopolitical, and operational risks.
- **Context-Aware Financial Exposure**: Adaptive cargo value calculations filtered by specific query context (e.g., "oil", "electronics").
- **Automated Compliance Guardian**: Continuous monitoring against regional policy frameworks and cargo restrictions.
- **Strategic Multi-Agent System**: A collaborative swarm of agents including *DisruptionScout*, *InventoryAnalyst*, and *ComplianceGuardian*, orchestrated by a *LogisticsDirector*.

## 🛠 Technology Stack

- **Frontend**: Streamlit
- **AI/LLM**: Google Vertex AI (Gemini 2.5 Flash)
- **Data APIs**: Open-Meteo (Marine Weather)
- **Persistence**: Google Cloud SQL (PostgreSQL)
- **Geocoding**: GeoPy/Nominatim

---

## 🚦 Getting Started

### Prerequisites

- Python 3.9+
- Google Cloud Project with Vertex AI enabled
- Google Application Credentials configured

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/KaungSiThu-Sallius/RouteNexus_Agent.git
   cd RouteNexus_Agent
   ```

2. **Setup Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables**:
   Set your Google Cloud configurations:
   ```bash
   export GOOGLE_CLOUD_PROJECT="your-project-id"
   export GOOGLE_CLOUD_LOCATION="us-central1"
   ```

### Running the Application

```bash
streamlit run app.py
```

---

## 🚢 Data Structure

RouteNexus uses a flexible, CSV-backed data engine for demo-ready logistics simulation:

- **Shipping Data** (`data/shipping_data.csv`): Simulates vessel positions, cargo types, and financial values.
- **Compliance Policies** (`data/compliance_policies.csv`): Editable regional rules and cargo-specific restrictions.

---

## 🧩 Architecture

The application follows a modular "Tool-Agent-UI" architecture:

1. **UI Layer** (`app.py`): Manages the Streamlit dashboard, session state, and real-time trace logging.
2. **Agent Layer** (`agent.py`): Orchestrates specialized agents using Vertex AI for synthesis and reasoning.
3. **Tool Layer** (`tools.py`): Operationalizes data fetching from APIs (Weather) and local datasets (Inventory/Compliance).

---

## 📝 Sample Commands

Try these in the **Director Channel**:
- *"Analyze the Strait of Malacca"* (General Assessment)
- *"Check oil shipments in Suez"* (Filtered Financial Exposure)
- *"Status of electronics in Panama Canal"* (Specific Risk Analysis)

---

## ⚖️ License

Distributed under the MIT License. See `LICENSE` for more information.
