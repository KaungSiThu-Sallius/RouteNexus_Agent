import streamlit as st
import streamlit.components.v1 as components
import asyncio
import os
import json
import re
import tempfile
from cloud_sql_session import CloudSQLSessionService
import threading
import queue
import datetime
from tools import (
    get_live_marine_weather, 
    check_inventory_exposure, 
    check_policy_compliance, 
    infer_region_and_coords,
    synthesize_report_with_llm, 
    generate_chat_reply_with_llm, 
    should_reanalyze_command
)

# 1. Environment Configuration
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agentverse-488704")
LOCATION = os.getenv("DB_REGION", "asia-southeast1")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", f"{PROJECT_ID}:{LOCATION}:routenexus-db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "password")
DB_NAME = os.getenv("DB_NAME", "postgres")

credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
if credentials_json and not credentials_path:
    temp_credentials_path = os.path.join(tempfile.gettempdir(), "routenexus-gcp-creds.json")
    with open(temp_credentials_path, "w", encoding="utf-8") as credentials_file:
        credentials_file.write(credentials_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_credentials_path

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

# ── App Config ──────────────────────────────────────────────────────────────
if "sidebar_state" not in st.session_state:
    st.session_state.sidebar_state = "expanded"

st.set_page_config(
    page_title="RouteNexus | Strategic Command", 
    page_icon="📍", 
    layout="wide",
    initial_sidebar_state=st.session_state.sidebar_state
)

st.title("RouteNexus Dashboard")
st.caption("RouteNexus intelligence for weather, exposure, compliance, and director coordination.")
st.markdown("""
<style>
:root {
    --bg: #17181c;
    --bg-elevated: #22232a;
    --bg-soft: #2b2d36;
    --line: rgba(255, 255, 255, 0.08);
    --text: #f5f5f7;
    --muted: #b0b3bd;
    --muted-strong: #d5d7de;
    --airbnb-red: #ff385c;
    --airbnb-red-dark: #e31c5f;
    --success: #31c48d;
    --warning: #f5a524;
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] > .main {
    background:
        radial-gradient(circle at top left, rgba(255,56,92,0.12), transparent 24%),
        linear-gradient(180deg, #17181c 0%, #1d1f26 100%) !important;
    color: var(--text) !important;
}
[data-testid="stHeader"] {
    background: rgba(23,24,28,0.82) !important;
    border-bottom: 1px solid var(--line) !important;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #20222a 0%, #1b1d24 100%) !important;
    border-right: 1px solid var(--line) !important;
}
[data-testid="stSidebar"] * {
    color: var(--text);
}
h1, h2, h3, label, .stMarkdown, .stCaption, p {
    color: var(--text) !important;
}
.stTextArea textarea,
[data-testid="stChatInputTextArea"] textarea {
    background: rgba(34,35,42,0.92) !important;
    color: var(--text) !important;
    border: 1px solid rgba(255,255,255,0.05) !important;
    border-radius: 16px !important;
    box-shadow: none !important;
}
.stTextArea textarea::placeholder,
[data-testid="stChatInputTextArea"] textarea::placeholder {
    color: var(--muted) !important;
}
.stButton > button {
    background: linear-gradient(135deg, var(--airbnb-red) 0%, var(--airbnb-red-dark) 100%) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 14px !important;
    box-shadow: 0 10px 24px rgba(255,56,92,0.28) !important;
    font-weight: 700 !important;
}
.stButton > button:hover {
    filter: brightness(1.03) !important;
}
[data-testid="stSidebar"] .stButton > button[kind="secondary"] {
    background: transparent !important;
    color: var(--muted-strong) !important;
    border: 1px solid rgba(255,255,255,0.14) !important;
    box-shadow: none !important;
}
[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover {
    background: rgba(255,255,255,0.04) !important;
}
[data-testid="stExpander"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--line) !important;
    border-radius: 18px !important;
}
[data-testid="stCodeBlock"] {
    background: #14161b !important;
    border: 1px solid var(--line) !important;
    border-radius: 16px !important;
}
[data-testid="stAlert"] {
    background: rgba(52, 86, 130, 0.22) !important;
    border: 1px solid rgba(96, 165, 250, 0.22) !important;
    color: var(--text) !important;
}
.sidebar-section-header {
    color: var(--muted-strong);
    font-size: 1rem;
    font-weight: 800;
    letter-spacing: 0.06em;
    margin: 0.3rem 0 0.8rem 0;
}
.sidebar-brand {
    font-size: 1.85rem;
    line-height: 1;
    font-weight: 900;
    letter-spacing: -0.04em;
    color: #ff4d6d;
    margin: 0.1rem 0 1rem 0;
    text-align: center;
    width: 100%;
}
.sidebar-divider {
    height: 1px;
    background: var(--line);
    margin: 0.7rem 0 0.85rem 0;
}
.sidebar-history-item {
    margin-bottom: 0.35rem;
}
.sidebar-history-item .stButton > button {
    background: rgba(255,255,255,0.03) !important;
    color: var(--text) !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    box-shadow: none !important;
    text-align: left !important;
    margin-bottom: 0 !important;
    padding: 0.72rem 0.95rem !important;
    border-radius: 14px !important;
    min-height: 0 !important;
    line-height: 1.4 !important;
    font-weight: 600 !important;
}
.sidebar-history-item .stButton > button:hover {
    background: rgba(255,255,255,0.05) !important;
    border-color: rgba(255,255,255,0.16) !important;
}
.active-session-box {
    background: linear-gradient(135deg, rgba(255,56,92,0.16) 0%, rgba(227,28,95,0.18) 100%);
    border: 1px solid rgba(255,56,92,0.30);
    color: #ffffff;
    padding: 0.72rem 0.95rem;
    border-radius: 14px;
    font-weight: 600;
    margin-bottom: 0.35rem;
    line-height: 1.4;
    min-height: 0;
    box-sizing: border-box;
}
.session-meta,
.section-chip {
    color: var(--muted) !important;
}
.panel-title {
    color: var(--text) !important;
    font-size: 1rem;
    font-weight: 800;
    margin-bottom: 0.35rem;
}
.panel-copy {
    color: var(--muted) !important;
    font-size: 0.94rem;
    margin-bottom: 0.75rem;
}
.glass-panel,
.insight-panel,
.chat-shell,
.metric-box-custom {
    background: linear-gradient(180deg, rgba(34,35,42,0.96) 0%, rgba(28,29,36,0.96) 100%) !important;
    border: 1px solid rgba(255,255,255,0.05) !important;
    border-radius: 18px !important;
    box-shadow: 0 10px 20px rgba(0,0,0,0.18) !important;
}
.glass-panel,
.insight-panel,
.chat-shell {
    padding: 1rem 1.05rem;
}
.metric-box-custom {
    padding: 1rem !important;
}
.insight-title,
.chat-header {
    color: var(--text) !important;
    font-weight: 800;
}
.insight-label,
.assistant-label,
.section-chip {
    color: var(--muted) !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 800;
}
.section-chip {
    display: inline-block;
    padding-bottom: 0.35rem;
    border-bottom: 2px solid rgba(255,56,92,0.85);
    margin-bottom: 0.7rem;
}
.insight-row {
    padding: 1.05rem 0;
    border-bottom: 1px solid var(--line);
}
.insight-row:last-child {
    border-bottom: none;
}
.insight-value {
    color: var(--muted-strong) !important;
    line-height: 1.9;
    margin-top: 0.35rem;
}
.trace-shell {
    background: linear-gradient(180deg, rgba(34,35,42,0.96) 0%, rgba(28,29,36,0.96) 100%) !important;
    border: 1px solid var(--line) !important;
    border-radius: 18px !important;
    padding: 0.25rem !important;
}
.trace-shell [data-testid="stCodeBlock"] {
    margin-top: 0 !important;
}
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 12px;
}
.metric-title {
    color: #8f93a1;
    font-size: 0.75rem;
    text-transform: uppercase;
    font-weight: 700;
    margin-bottom: 8px;
}
.metric-value {
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--text);
}
.metric-subtle {
    color: var(--muted);
    font-size: 0.85rem;
}
[data-testid="stTextArea"] label,
[data-testid="stChatInput"] label {
    color: var(--muted-strong) !important;
}
[data-testid="stExpander"] summary {
    color: var(--text) !important;
}
@media (max-width: 900px) {
    .metrics-grid {
        grid-template-columns: 1fr;
    }
}
.status-badge {
    border-radius: 999px;
    font-weight: 700;
}
.status-clear {
    background: rgba(49,196,141,0.18);
    color: #8ef0c1;
    border: 1px solid rgba(49,196,141,0.20);
}
.status-warning {
    background: rgba(245,165,36,0.16);
    color: #ffd37a;
    border: 1px solid rgba(245,165,36,0.20);
}
.status-critical {
    background: rgba(255,56,92,0.16);
    color: #ff9db1;
    border: 1px solid rgba(255,56,92,0.22);
}
.chat-bubble-user {
    background: linear-gradient(135deg, var(--airbnb-red) 0%, var(--airbnb-red-dark) 100%) !important;
    color: #ffffff !important;
    padding: 0.95rem 1.1rem;
    border-radius: 20px 20px 8px 20px;
    max-width: 78%;
    margin: 0.3rem 0 0.9rem auto;
    box-shadow: 0 16px 30px rgba(255,56,92,0.24);
}
.chat-bubble-assistant {
    background: rgba(255,255,255,0.05) !important;
    color: var(--text) !important;
    border: 1px solid rgba(255,255,255,0.08);
    padding: 0.95rem 1.1rem;
    border-radius: 20px 20px 20px 8px;
    max-width: 82%;
    margin: 0.3rem auto 0.9rem 0;
}
[data-testid="stSidebarCollapsedControl"] {
    opacity: 1 !important;
    visibility: visible !important;
}
[data-testid="stSidebarCollapsedControl"] button {
    opacity: 1 !important;
    visibility: visible !important;
}
</style>
""", unsafe_allow_html=True)

# ── Global Async Loop & DB Connection Cache ──────────────────────────────────
@st.cache_resource
def get_global_db_service():
    """
    Creates a dedicated background event loop running in a daemon thread.
    This ensures the Cloud SQL Connector is initialized EXACTLY ONCE upon startup,
    bound to a single stable event loop — preventing loop mismatch errors.
    """
    loop = asyncio.new_event_loop()
    def _run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=_run_loop, daemon=True).start()

    future = asyncio.run_coroutine_threadsafe(
        CloudSQLSessionService.create(
            instance_connection_name=INSTANCE_NAME,
            db_user=DB_USER,
            db_password=DB_PASS
        ),
        loop
    )
    try:
        svc = future.result(timeout=30)
    except Exception as e:
        print(f"[WARN] Cloud SQL session service unavailable: {e}")
        svc = None
    return loop, svc

GLOBAL_LOOP, GLOBAL_SVC = get_global_db_service()

def run_in_bg(coroutine):
    """Synchronously wait for a coroutine to finish on the background loop."""
    return asyncio.run_coroutine_threadsafe(coroutine, GLOBAL_LOOP).result()


def format_compact_currency(value: str) -> str:
    cleaned = re.sub(r"[^0-9.]", "", str(value))
    try:
        amount = float(cleaned) if cleaned else 0.0
    except Exception:
        return str(value)

    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:,.0f}"

# ── Constants ────────────────────────────────────────────────────────────────
APP_NAME   = "RouteNexus_App"
USER_ID    = "user_001"

def scroll_to_top():
    """Inject JS to scroll the main page area back to the top before rerun."""
    components.html(
        "<script>window.parent.document.querySelector('section.main').scrollTo(0, 0);</script>",
        height=0,
    )

# ── Session State ─────────────────────────────────────────────────────────────
# Initialize unique session ID from query params if available, else fresh
qp_sid = st.query_params.get("session_id")
if "current_session_id" not in st.session_state:
    if qp_sid:
        st.session_state.current_session_id = qp_sid
    else:
        import time
        st.session_state.current_session_id = str(int(time.time()))

if "swarm_output"      not in st.session_state: st.session_state.swarm_output      = None
if "trace_logs"        not in st.session_state: st.session_state.trace_logs        = ">>> [SYSTEM] Ready for chat deployment...\n"
if "chat_history"      not in st.session_state: st.session_state.chat_history      = []
if "session_ready"     not in st.session_state: st.session_state.session_ready     = False
if "cached_history"    not in st.session_state: st.session_state.cached_history    = None
if "history_needs_refresh" not in st.session_state: st.session_state.history_needs_refresh = True
if "human_approval"    not in st.session_state: st.session_state.human_approval    = None
if "mission_input"     not in st.session_state: st.session_state.mission_input     = "Analyze the Strait of Malacca. Check live weather risks, our internal financial exposure, and verify if a reroute is compliant with company policy."
if "local_sessions"    not in st.session_state: st.session_state.local_sessions    = {} # Keep for session-to-session transient state if needed, but not for "storage"



# ── Core: Send a message and stream the response ──────────────────────────────
async def _send_message_async(message_text: str, session_id: str, current_trace_logs: str, q: queue.Queue) -> tuple[dict | None, str | None, str]:
    """Internal async logic running on the background loop."""
    if GLOBAL_SVC:
        try:
            existing = await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
            if not existing:
                await GLOBAL_SVC.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
        except Exception:
            existing = None

    trace_log = current_trace_logs + f"\n\n>>> [USER] {message_text}\n>>> [SYSTEM] Routing to Swarm...\n"
    q.put(trace_log)

    final_data = None
    final_text = None

    try:
        region, (lat, lon) = infer_region_and_coords(message_text)

        trace_log += "\n>>> [LogisticsDirector] System Online. Initializing RouteNexus Swarm..."
        q.put(trace_log)

        trace_log += "\n>>> [DisruptionScout] taking control..."
        q.put(trace_log)
        trace_log += f"\n    [TOOL] Firing: get_live_marine_weather (lat={lat}, lon={lon})"
        q.put(trace_log)
        weather_raw = await asyncio.to_thread(get_live_marine_weather, lat, lon)

        trace_log += "\n>>> [InventoryAnalyst] taking control..."
        q.put(trace_log)
        trace_log += f"\n    [TOOL] Firing: check_inventory_exposure (region='{region}')"
        q.put(trace_log)
        inventory_raw = await asyncio.to_thread(check_inventory_exposure, message_text, region)

        trace_log += "\n>>> [ComplianceGuardian] taking control..."
        q.put(trace_log)
        trace_log += f"\n    [TOOL] Firing: check_policy_compliance (region='{region}')"
        q.put(trace_log)
        policy_raw = await asyncio.to_thread(check_policy_compliance, message_text, region)

        trace_log += "\n>>> [RouteExecutor] taking control..."
        q.put(trace_log)

        final_data = synthesize_report_with_llm(message_text, region, weather_raw, inventory_raw, policy_raw)
        final_text = json.dumps(final_data, indent=2)

        if GLOBAL_SVC:
            try:
                sess = await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
                if sess:
                    from google.adk.events.event import Event
                    from google.adk.events.event_actions import EventActions
                    import time
                    evt = Event(
                        author="system",
                        actions=EventActions(state_delta={
                            "mission_report": final_data,
                            "chat_command": message_text,
                        }),
                        timestamp=time.time(),
                    )
                    await GLOBAL_SVC.append_event(sess, evt)
            except Exception as e:
                print(f"[DB ERROR] Failed to save mission report: {e}")

        trace_log += "\n\n>>> [SYSTEM] Swarm Standby. Response complete."
        q.put(trace_log)
    except Exception as e:
        error_msg = str(e)
        trace_log += f"\n\n>>> [ERROR] Swarm Execution Failed:\n{error_msg}"
        q.put(trace_log)
        final_data = {"error": "Swarm Failed", "raw": error_msg}
        final_text = error_msg

    return final_data, final_text, trace_log

def send_message(message_text: str, log_placeholder, session_id: str):
    q = queue.Queue()
    current_logs = st.session_state.trace_logs

    async def _runner():
        return await _send_message_async(message_text, session_id, current_logs, q)

    future = asyncio.run_coroutine_threadsafe(_runner(), GLOBAL_LOOP)

    while not future.done():
        try:
            log_update = q.get(timeout=0.1)
            log_placeholder.code(log_update, language="bash")
        except queue.Empty:
            pass

    while not q.empty():
        log_update = q.get()
        log_placeholder.code(log_update, language="bash")

    data, text, full_log = future.result()
    log_placeholder.code(full_log, language="bash")
    st.session_state.trace_logs = full_log
    return data, text

def fetch_history():
    if not GLOBAL_SVC: return None
    async def _f(): return await GLOBAL_SVC.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    return run_in_bg(_f())

def restore_session_history(sess_id):
    if not GLOBAL_SVC: return None
    async def _f(): return await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=sess_id)
    return run_in_bg(_f())

def reconstruct_chat_from_session(session_obj):
    """Reconstruct chat_history, swarm_output, and mission_input from session events."""
    if not session_obj:
        return [], None, None, None
    
    chat_history = []
    swarm_output = None
    human_approval = None
    mission_input = None
    
    try:
        events = session_obj.events if hasattr(session_obj, 'events') else []
        for event in events:
            # Extract chat messages from Content events
            if hasattr(event, 'content') and event.content:
                content_obj = event.content
                if isinstance(content_obj, dict):
                    role = content_obj.get('role', 'user')
                    parts = content_obj.get('parts', [])
                    if parts and isinstance(parts, list) and len(parts) > 0:
                        text = parts[0].get('text', '') if isinstance(parts[0], dict) else str(parts[0])
                        chat_role = 'user' if role == 'user' else 'assistant'
                        chat_history.append({'role': chat_role, 'content': text})
            
            # Extract state from EventActions
            if hasattr(event, 'actions') and event.actions:
                if hasattr(event.actions, 'state_delta') and event.actions.state_delta:
                    state = event.actions.state_delta
                    if 'mission_report' in state:
                        swarm_output = state['mission_report']
                    if 'human_approval' in state:
                        human_approval = state['human_approval']
                    if 'chat_command' in state:
                        mission_input = state['chat_command']
    except Exception as e:
        print(f"[RESTORE ERROR] Failed to reconstruct chat: {e}")
    
    # Fallback for mission_input if not explicitly in state_delta
    if not mission_input and chat_history:
        for msg in chat_history:
            if msg['role'] == 'user':
                mission_input = msg['content']
                break

    return chat_history, swarm_output, human_approval, mission_input


# Startup Restore Logic
if qp_sid and not st.session_state.get("session_restored", False):
    if GLOBAL_SVC:
        max_retries = 3
        retry_delay = 1.0
        restored = False
        
        with st.spinner("Restoring session from Cloud SQL..."):
            for attempt in range(max_retries):
                try:
                    async def _get(): return await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=qp_sid)
                    session_obj = run_in_bg(_get())
                    if session_obj:
                        chat_history, swarm_output, human_approval, mission_input = reconstruct_chat_from_session(session_obj)
                        st.session_state.chat_history = chat_history if chat_history else []
                        st.session_state.swarm_output = swarm_output
                        st.session_state.human_approval = human_approval
                        if mission_input:
                            st.session_state.mission_input = mission_input
                        st.session_state.session_ready = True if (chat_history or swarm_output) else False
                        st.session_state.trace_logs = ">>> [SYSTEM] Session restored from Cloud SQL.\n"
                        st.session_state.session_restored = True
                        restored = True
                        break
                except Exception as e:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay)
                        continue
                    else:
                        st.warning(f"Could not sync with history (Network Error). Proceeding with a fresh session.")
                        st.session_state.session_restored = True
        
        if not restored:
            st.session_state.session_restored = True
    else:
        st.session_state.session_restored = True

async def _delete_all_history_async():
    if not GLOBAL_SVC:
        return
    history = await GLOBAL_SVC.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    if history and history.sessions:
        for sess in history.sessions:
            await GLOBAL_SVC.delete_session(app_name=APP_NAME, user_id=USER_ID, session_id=sess.id)

def save_chat_message(session_id: str, role: str, content: str):
    if not GLOBAL_SVC: return
    async def _worker():
        try:
            sess = await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
            if sess:
                from google.adk.events.event import Event
                import time
                c_role = "user" if role == "user" else "model"
                evt_content = {"role": c_role, "parts": [{"text": content}]}
                evt = Event(author=role, content=evt_content, timestamp=time.time())
                await GLOBAL_SVC.append_event(sess, evt)
        except Exception as e:
            print(f"[DB WARN] Failed to save chat msg: {e}")
    run_in_bg(_worker())

def delete_all_history():
    if not GLOBAL_SVC: return
    async def _worker():
        history = await GLOBAL_SVC.list_sessions(app_name=APP_NAME, user_id=USER_ID)
        if history and history.sessions:
            for sess in history.sessions:
                await GLOBAL_SVC.delete_session(app_name=APP_NAME, user_id=USER_ID, session_id=sess.id)
    run_in_bg(_worker())

def save_approval(session_id: str, approval_status: str):
    if not GLOBAL_SVC: return
    async def _worker():
        try:
            sess = await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
            if sess:
                from google.adk.events.event import Event
                from google.adk.events.event_actions import EventActions
                import time
                evt = Event(
                    author="system",
                    actions=EventActions(state_delta={"human_approval": approval_status}),
                    timestamp=time.time(),
                )
                await GLOBAL_SVC.append_event(sess, evt)
        except Exception as e:
            print(f"[DB WARN] Failed to save approval: {e}")
    run_in_bg(_worker())


# Removed local snapshot functions as they were being confused with "local storage"
# Persistence is now strictly database-backed.

with st.sidebar:
    # Sidebar Header
    st.markdown("<div class='sidebar-brand'>RouteNexus</div>", unsafe_allow_html=True)

    # Cloud SQL Connection Status
    if GLOBAL_SVC is None:
        st.error("⚠️ Cloud SQL is Offline. Sessions will not be persisted permanently.")
        if st.button("Retry Connection", use_container_width=True):
            st.cache_resource.clear()
            st.rerun()

    # New Chat Button
    st.markdown('<div class="sidebar-primary-actions">', unsafe_allow_html=True)
    if st.button("New Chat", type="primary", use_container_width=True):
        import time
        st.session_state.current_session_id = str(int(time.time()))
        st.session_state.swarm_output = None
        st.session_state.trace_logs = ">>> [SYSTEM] Ready for chat deployment...\n"
        st.session_state.chat_history = []
        st.session_state.session_ready = False
        st.session_state.human_approval = None
        st.session_state.mission_input = "Analyze the Strait of Malacca. Check live weather risks, our internal financial exposure, and verify if a reroute is compliant with company policy."
        scroll_to_top()
        st.rerun()

    if st.button("Clear History", type="secondary", use_container_width=True):
        with st.spinner("Wiping history..."):
            delete_all_history()
            import time
            st.session_state.current_session_id = str(int(time.time()))
            st.session_state.swarm_output = None
            st.session_state.trace_logs = ">>> [SYSTEM] History cleared. Ready for new chat.\n"
            st.session_state.chat_history = []
            st.session_state.session_ready = False
            st.session_state.history_needs_refresh = True
            st.session_state.cached_history = None
            st.session_state.local_sessions = {}
            scroll_to_top()
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<div class='sidebar-divider'></div>", unsafe_allow_html=True)
    st.markdown("<div class='sidebar-section-header'>History</div>", unsafe_allow_html=True)
    
    with st.spinner("Fetching chats..."):
        try:
            if st.session_state.history_needs_refresh or st.session_state.cached_history is None:
                st.session_state.cached_history = fetch_history()
                st.session_state.history_needs_refresh = False
                
            history = st.session_state.cached_history
            
            if GLOBAL_SVC and getattr(history, "sessions", None):
                sessions_to_render = sorted(history.sessions, key=lambda x: x.id, reverse=True)
            else:
                local_session_ids = sorted(st.session_state.local_sessions.keys(), reverse=True)
                class LocalSession:
                    def __init__(self, sid): self.id = sid
                sessions_to_render = [LocalSession(sid) for sid in local_session_ids]

            if not sessions_to_render:
                st.info("No past chats yet.")
            else:
                for sess in sessions_to_render:
                    if sess.id == "health_check_test": continue # skip the tests
                    is_active = sess.id == st.session_state.current_session_id
                    timestamp_str = sess.id

                    # Format timestamp if sess.id is numeric
                    try:
                        dt = datetime.datetime.fromtimestamp(int(sess.id))
                        timestamp_str = dt.strftime("%b %d, %H:%M")
                    except: pass

                    label = f"Chat {timestamp_str}"
                    
                    if is_active:
                        st.markdown(f'<div class="active-session-box">{label}</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="sidebar-history-item">', unsafe_allow_html=True)
                        if st.button(label, key=f"btn_{sess.id}", use_container_width=True):
                            if GLOBAL_SVC:
                                with st.spinner("Restoring chat..."):
                                    st.session_state.current_session_id = sess.id
                                    st.session_state.session_ready = True
                                    st.session_state.human_approval = None
                                    st.session_state.swarm_output = None
                                    full_sess = restore_session_history(sess.id)
                                new_history = []
                                restored_report = None
                                restored_command = None
                                if getattr(full_sess, "events", None):
                                    for evt in full_sess.events:
                                        actions = getattr(evt, "actions", None)
                                        state_delta = getattr(actions, "state_delta", None) if actions else None
                                        if isinstance(state_delta, dict) and isinstance(state_delta.get("mission_report"), dict):
                                            restored_report = state_delta.get("mission_report")
                                        if isinstance(state_delta, dict) and isinstance(state_delta.get("chat_command"), str) and state_delta.get("chat_command").strip():
                                            restored_command = state_delta.get("chat_command").strip()
                                        if getattr(evt, "content", None) and getattr(evt.content, "parts", None):
                                            role = "user" if evt.content.role == "user" else "assistant"
                                            txt = "".join([p.text for p in evt.content.parts if hasattr(p, "text") and getattr(p, "text", None)])
                                            if txt.strip():
                                                if role == "user" and restored_command is None:
                                                    restored_command = txt
                                                new_history.append({"role": role, "content": txt})
                                st.session_state.chat_history = new_history
                                if restored_command:
                                    st.session_state.mission_input = restored_command
                                if restored_report:
                                    st.session_state.swarm_output = restored_report
                                else:
                                    for msg in reversed(new_history):
                                        jm = re.search(r"\{[\s\S]*\}", msg["content"])
                                        if jm:
                                            try:
                                                st.session_state.swarm_output = json.loads(jm.group())
                                                break
                                            except Exception:
                                                pass
                                st.session_state.trace_logs = f">>> [SYSTEM] Loaded session {sess.id} from Cloud SQL.\n"
                                scroll_to_top()
                                st.rerun()
                            else:
                                st.warning("Cloud SQL is offline. Session cannot be restored.")
                        st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('</div>', unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Could not load history: {e}")

# ── Dashboard Layout ──────────────────────────────────────────────────────────
st.markdown(f"<div class='session-meta'>Active Session <span style='color: #f5f5f7; font-weight: 700;'>{st.session_state.current_session_id}</span></div>", unsafe_allow_html=True)
st.markdown("<div class='section-chip'>Mission Input</div>", unsafe_allow_html=True)
mission = st.text_area(
    "Mission Parameters",
    height=120,
    key="mission_input",
    placeholder="Describe the logistics mission and region for analysis..."
)

col1, col2 = st.columns([1, 1.4])

with col1:
    st.markdown("<div class='section-chip'>Operations</div>", unsafe_allow_html=True)
    st.markdown("<div class='panel-title'>Agent Activity</div>", unsafe_allow_html=True)
    st.markdown("<div class='panel-copy'>Review the swarm execution trail and monitor how agents collaborate during each mission.</div>", unsafe_allow_html=True)
    log_box = st.empty()
    log_box.code(st.session_state.trace_logs, language="bash")

with col2:
    st.markdown("<div class='section-chip'>Output</div>", unsafe_allow_html=True)
    output_box = st.empty()

    if not st.session_state.session_ready:
        if st.button("Initialize Swarm Analysis", type="primary"):
            with st.spinner("Swarm agents collaborating..."):
                structured, raw_text = send_message(mission, log_box, st.session_state.current_session_id)
                st.session_state.swarm_output = structured
                st.session_state.session_ready = True
                st.session_state.history_needs_refresh = True
                st.session_state.human_approval = None

                # Add a professional summary to chat history instead of technical raw text
                st.session_state.chat_history.append({"role": "user",      "content": mission})
                director_intro = "Mission analysis complete. I've updated the dashboard with the latest metrics and strategic recommendations. Please review the risk levels and financial exposure above. I'm standing by for follow-up coordination."
                st.session_state.chat_history.append({"role": "assistant", "content": director_intro})
                
                # Save professional turn to Cloud SQL
                save_chat_message(st.session_state.current_session_id, "user", mission)
                save_chat_message(st.session_state.current_session_id, "assistant", director_intro)
                
                # No local snapshot

                st.rerun()
    else:
        st.markdown("<div class='status-badge status-clear' style='display: block; text-align: center; padding: 8px;'>Analysis Online</div>", unsafe_allow_html=True)

    if st.session_state.swarm_output:
        data = st.session_state.swarm_output

        with output_box.container():
            if "error" in data:
                st.error(f"Analysis Error: {data['error']}")
                st.info(data.get("raw", ""))
            else:
                exposure = data.get("financial_exposure") or data.get("total_risk_usd") or "$0"
                risk_level = data.get("mission_status", "N/A").replace("⚠️", "").strip().upper()
                compliance_status = data.get("compliance_status") or ("CLEARED" if "CLEARED" in str(data.get("policy_status", "")).upper() else "WARNING")
                region_name = data.get("region", "Global")
                policy_raw = data.get("policy_status", "N/A")

                # Use first word only for risk level to keep it short
                if risk_level:
                    risk_level = risk_level.split()[0]
                
                display_region = region_name
                if len(display_region) > 20:
                    display_region = display_region.split("/")[0] if "/" in display_region else display_region[:20]

                # Metrics Grid
                st.markdown(f"""
                <div class="metrics-grid">
                    <div class="metric-box-custom" style="border-top: 4px solid #FF385C;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div class="metric-title">Risk Level</div>
                            <span style="font-size: 1.2rem;">📍</span>
                        </div>
                        <div class="metric-value">{risk_level}</div>
                    </div>
                    <div class="metric-box-custom" style="border-top: 4px solid #7b8192;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div class="metric-title">Financial Exposure</div>
                            <span style="font-size: 1.2rem;">💰</span>
                        </div>
                        <div class="metric-value">{exposure}</div>
                        <div class="metric-subtle">Estimated Liability</div>
                    </div>
                    <div class="metric-box-custom" style="border-top: 4px solid #E61E4D;">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div class="metric-title">Compliance Status</div>
                            <span style="font-size: 1.2rem;">⚖️</span>
                        </div>
                        <div class="metric-value">{compliance_status}</div>
                        <div class="metric-subtle">Policy Framework</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("<div class='insight-title'>Strategic Brief</div>", unsafe_allow_html=True)
                if "CLEARED" in str(policy_raw).upper():
                    compliance_markup = f"<span style='color: #008489;'>{policy_raw}</span>"
                elif "ERROR" in str(policy_raw).upper():
                    compliance_markup = f"<span style='color: #FF385C;'>{policy_raw}</span>"
                else:
                    compliance_markup = f"<span style='color: #B28B00;'>{policy_raw}</span>"
                st.markdown(f"""
                <div class='insight-row'>
                    <span class='insight-label'>Compliance</span>
                    <div class='insight-value'>{compliance_markup}</div>
                </div>
                <div class='insight-row'>
                    <span class='insight-label'>Weather Conditions</span>
                    <div class='insight-value'>{data.get('weather_summary', 'Information unavailable')}</div>
                </div>
                <div class='insight-row'>
                    <span class='insight-label'>Strategic Recommendation</span>
                    <div class='insight-value'>{data.get('final_recommendation', 'No specific recommendation provided.')}</div>
                </div>
                """, unsafe_allow_html=True)

                if st.session_state.human_approval == "approved":
                    st.markdown("<div class='status-badge status-clear' style='display: block; text-align: center; padding: 12px; margin-bottom: 10px; font-weight: 500;'>Transmission authorized. Orders sent to fleet.</div>", unsafe_allow_html=True)
                elif st.session_state.human_approval == "rejected":
                    st.markdown("<div class='status-badge status-critical' style='display: block; text-align: center; padding: 12px; margin-bottom: 10px; font-weight: 500;'>Request rejected. Standing by for revision.</div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div style='font-size: 0.9rem; font-weight: 500; color: #b0b3bd; margin-bottom: 12px; text-align: center;'>Executive authorization required for route execution</div>", unsafe_allow_html=True)
                    btn_col1, btn_col2 = st.columns(2)

                    if btn_col1.button("Authorize Reroute", use_container_width=True):
                        st.session_state.human_approval = "approved"
                        save_approval(st.session_state.current_session_id, "approved")
                        # No local snapshot
                        st.rerun()

                    if btn_col2.button("Revise Strategy", use_container_width=True):
                        st.session_state.human_approval = "rejected"
                        save_approval(st.session_state.current_session_id, "rejected")
                        # No local snapshot
                        st.rerun()

st.markdown("<div style='height:2rem;'></div><hr style='border: none; height: 1px; background: var(--line); margin-bottom: 2rem;'>", unsafe_allow_html=True)
st.markdown("<div class='section-chip'>Director Channel</div>", unsafe_allow_html=True)
st.markdown("<div class='chat-header'>Director Communications</div>", unsafe_allow_html=True)
st.caption("Strategic coordination with the LogisticsDirector. Request follow-up assessments or revisions.")

if not st.session_state.session_ready:
    st.info("No active analysis yet. You can still message the LogisticsDirector to start one.")

for msg in st.session_state.chat_history:
    role = msg["role"]
    content = msg["content"]
    if role == "user":
        st.markdown(f"""
            <div class="chat-bubble-user">
                {content}
            </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div class="chat-bubble-assistant">
                <div class="assistant-label">LogisticsDirector</div>
                {content}
            </div>
        """, unsafe_allow_html=True)

chat_prompt = "Input follow-up parameters..." if st.session_state.session_ready else "Describe your mission to start analysis..."
if user_input := st.chat_input(chat_prompt):
    st.markdown(f"""
        <div class="chat-bubble-user">
            {user_input}
        </div>
    """, unsafe_allow_html=True)

    response_placeholder = st.empty()
    response_placeholder.markdown("<p style='color: #717171; font-style: italic; margin-left: 10px;'>Director is coordinating swarm...</p>", unsafe_allow_html=True)

    reply = None
    try:
        should_reanalyze = (not st.session_state.session_ready) or should_reanalyze_command(user_input)

        if should_reanalyze:
            with st.spinner("Synthesizing swarm intelligence..."):
                structured, raw_text = send_message(user_input, log_box, st.session_state.current_session_id)
                if structured and "error" not in structured:
                    reply = generate_chat_reply_with_llm(user_input, structured)
                else:
                    reply = raw_text or "Analysis complete. I've updated the dashboard."

                if structured:
                    st.session_state.swarm_output = structured
                st.session_state.session_ready = True
                st.session_state.history_needs_refresh = True
        else:
            current_report = st.session_state.swarm_output or {}
            reply = generate_chat_reply_with_llm(user_input, current_report)

        response_placeholder.markdown(f"""
            <div class="chat-bubble-assistant">
                <div class="assistant-label">LogisticsDirector</div>
                {reply}
            </div>
        """, unsafe_allow_html=True)

        st.session_state.chat_history.append({"role": "user", "content": user_input})
        st.session_state.chat_history.append({"role": "assistant", "content": reply})

        save_chat_message(st.session_state.current_session_id, "user", user_input)
        save_chat_message(st.session_state.current_session_id, "assistant", reply)

    except Exception as e:
        response_placeholder.error(f"Execution Error: {e}")
        # Still record the user message even if the assistant failed
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        save_chat_message(st.session_state.current_session_id, "user", user_input)

    st.rerun()

# End of App
