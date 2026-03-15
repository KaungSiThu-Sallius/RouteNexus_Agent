import streamlit as st
import asyncio
import os
import json
import re
import tempfile
from cloud_sql_session import CloudSQLSessionService
import threading
import queue
import datetime
from tools import get_live_marine_weather, check_inventory_exposure, check_policy_compliance
from agent import infer_region_and_coords, synthesize_report_with_llm, generate_chat_reply_with_llm

# 1. Environment Configuration
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agentverse-488704")
LOCATION = os.getenv("DB_REGION", "asia-southeast1")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", f"{PROJECT_ID}:{LOCATION}:routenexus-db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "")

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
st.set_page_config(page_title="RouteNexus Command", page_icon="🌐", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .sidebar .sidebar-content { background-color: #161b22; }
    .stCodeBlock { background-color: #1e1e1e !important; }
    [data-testid="stMetricLabel"] { font-size: 0.9rem !important; color: #9da5b1 !important; }
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; white-space: nowrap !important; }

    /* Chat section header */
    .chat-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e0e0e0;
        padding: 8px 0 4px 0;
        border-bottom: 1px solid #333;
        margin-bottom: 10px;
    }
    .session-item {
        padding: 10px 14px; margin-bottom: 8px; border-radius: 6px; cursor: pointer;
        background-color: #161b22; border: 1px solid #30363d; transition: background-color 0.2s;
        color: #c9d1d9; font-size: 0.9em; font-weight: 500;
    }
    .session-item:hover { background-color: #21262d; }
    .session-selected { background-color: #238636 !important; border-color: #2ea043; }
    
    /* Force Sidebar Collapse Button to always be visible instead of hover-only */
    [data-testid="stSidebarCollapseButton"] {
        opacity: 1 !important;
        visibility: visible !important;
        display: flex !important;
    }
    /* Hide the "Collapse this sidebar" tooltip text */
    [data-testid="stSidebarCollapseButton"]::before {
        content: "" !important;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🌐 RouteNexus: Global Logistics Swarm")
st.markdown("---")

# ── Global Async Loop & DB Connection Cache ──────────────────────────────────
@st.cache_resource
def get_global_db_service():
    """
    Creates a dedicated background event loop running in a daemon thread.
    This ensures the Cloud SQL Connector (which establishes a TLS tunnel and 
    caches IAM certs) is initialized EXACTLY ONCE upon startup, rather than
    suffering a 3-second TLS handshake penalty per Streamlit widget click.
    """
    loop = asyncio.new_event_loop()
    def _run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=_run_loop, daemon=True).start()

    # Pre-warm the database service inside that background loop
    future = asyncio.run_coroutine_threadsafe(
        CloudSQLSessionService.create(
            instance_connection_name=INSTANCE_NAME,
            db_user=DB_USER,
            db_password=DB_PASS
        ), 
        loop
    )
    try:
        svc = future.result()
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

# ── Session State ─────────────────────────────────────────────────────────────
if "current_session_id" not in st.session_state: st.session_state.current_session_id = str(int(asyncio.run(asyncio.sleep(0)) or 0)) # placeholder
if "swarm_output"      not in st.session_state: st.session_state.swarm_output      = None
if "trace_logs"        not in st.session_state: st.session_state.trace_logs        = ">>> [SYSTEM] Ready for chat deployment...\n"
if "chat_history"      not in st.session_state: st.session_state.chat_history      = []
if "session_ready"     not in st.session_state: st.session_state.session_ready     = False
if "cached_history"    not in st.session_state: st.session_state.cached_history    = None
if "history_needs_refresh" not in st.session_state: st.session_state.history_needs_refresh = True
if "human_approval"    not in st.session_state: st.session_state.human_approval    = None
if "mission_input"     not in st.session_state: st.session_state.mission_input     = "Analyze the Strait of Malacca. Check live weather risks, our internal financial exposure, and verify if a reroute is compliant with company policy."
if "local_sessions"    not in st.session_state: st.session_state.local_sessions    = {}

# Initialize unique session ID if fresh
if st.session_state.current_session_id == "0":
    import time
    st.session_state.current_session_id = str(int(time.time()))

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
            except Exception:
                pass

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
    
    # Capture the string value on the main Streamlit thread
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

async def _fetch_history_async():
    if not GLOBAL_SVC:
        return None
    return await GLOBAL_SVC.list_sessions(app_name=APP_NAME, user_id=USER_ID)

def fetch_history():
    return run_in_bg(_fetch_history_async())

async def _restore_session_async(sess_id):
    if not GLOBAL_SVC:
        return None
    return await GLOBAL_SVC.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=sess_id)

def restore_session_history(sess_id):
    return run_in_bg(_restore_session_async(sess_id))

async def _delete_all_history_async():
    if not GLOBAL_SVC:
        return
    history = await GLOBAL_SVC.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    if history and history.sessions:
        for sess in history.sessions:
            await GLOBAL_SVC.delete_session(app_name=APP_NAME, user_id=USER_ID, session_id=sess.id)

def delete_all_history():
    run_in_bg(_delete_all_history_async())


def save_local_session_snapshot():
    st.session_state.local_sessions[st.session_state.current_session_id] = {
        "chat_history": list(st.session_state.chat_history),
        "swarm_output": st.session_state.swarm_output,
        "trace_logs": st.session_state.trace_logs,
        "mission_input": st.session_state.mission_input,
        "session_ready": st.session_state.session_ready,
    }


def load_local_session_snapshot(session_id: str):
    snapshot = st.session_state.local_sessions.get(session_id)
    if not snapshot:
        return
    st.session_state.current_session_id = session_id
    st.session_state.chat_history = list(snapshot.get("chat_history", []))
    st.session_state.swarm_output = snapshot.get("swarm_output")
    st.session_state.trace_logs = snapshot.get("trace_logs", ">>> [SYSTEM] Ready for chat deployment...\n")
    st.session_state.mission_input = snapshot.get("mission_input", st.session_state.mission_input)
    st.session_state.session_ready = snapshot.get("session_ready", False)
    st.session_state.human_approval = None

with st.sidebar:
    st.header("Chat History")
    
    # New Chat Button
    if st.button("➕ New Chat", type="primary", use_container_width=True):
        import time
        if st.session_state.chat_history or st.session_state.swarm_output:
            save_local_session_snapshot()
        st.session_state.current_session_id = str(int(time.time()))
        st.session_state.swarm_output = None
        st.session_state.trace_logs = ">>> [SYSTEM] Ready for chat deployment...\n"
        st.session_state.chat_history = []
        st.session_state.session_ready = False
        st.session_state.human_approval = None
        st.session_state.mission_input = "Analyze the Strait of Malacca. Check live weather risks, our internal financial exposure, and verify if a reroute is compliant with company policy."
        save_local_session_snapshot()
        st.rerun()

    if st.button("🗑️ Clear All History", type="secondary", use_container_width=True):
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
            st.rerun()

    st.markdown("---")
    
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
                sessions_to_render = [
                    type("LocalSession", (), {"id": session_id})()
                    for session_id in local_session_ids
                ]

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
                        st.markdown(f'<div class="session-item session-selected">{label}</div>', unsafe_allow_html=True)
                    else:
                        if st.button(label, key=f"btn_{sess.id}", use_container_width=True):
                            if st.session_state.chat_history or st.session_state.swarm_output:
                                save_local_session_snapshot()
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
                            else:
                                load_local_session_snapshot(sess.id)
                            st.rerun()
        except Exception as e:
            st.error(f"Could not load history: {e}")

# ── Dashboard Layout ──────────────────────────────────────────────────────────
st.write(f"📝 **Current Chat ID:** `{st.session_state.current_session_id}`")
mission = st.text_area(
    "Chat Command:",
    height=90,
    key="mission_input",
)

col1, col2 = st.columns([1, 1.2])

with col1:
    st.subheader("Agentic Trace (A2A Logs)")
    log_box = st.empty()
    log_box.code(st.session_state.trace_logs, language="bash")

with col2:
    st.subheader("Strategic Intelligence")
    output_box = st.empty()

    # ── Initial Chat Button ──────────────────────────────────────────────────
    if not st.session_state.session_ready:
        if st.button("🚀 Engage Swarm", type="primary"):
            with st.spinner("Swarm agents collaborating..."):
                structured, raw_text = send_message(mission, log_box, st.session_state.current_session_id)
                st.session_state.swarm_output = structured
                st.session_state.session_ready = True
                st.session_state.history_needs_refresh = True
                st.session_state.human_approval = None

                # Add to chat history
                st.session_state.chat_history.append({"role": "user",      "content": mission})
                st.session_state.chat_history.append({"role": "assistant", "content": raw_text or "Chat complete."})
                save_local_session_snapshot()

                st.rerun()
    else:
        st.success("✅ **Swarm Engaged.** Use the Director Chat below for follow-up orders.")

    # ── Persistent Result Display ─────────────────────────────────────────────
    if st.session_state.swarm_output:
        data = st.session_state.swarm_output

        with output_box.container():
            if "error" in data:
                st.error(f"Format Error: {data['error']}")
                st.info(data.get("raw", ""))
            else:
                st.success("### ✅ Chat Report Generated")

                m1, m2, m3 = st.columns(3)
                m1.metric("Risk Level",  data.get("mission_status", "N/A"))
                full_financial_value = data.get("total_risk_usd", "$0.00")
                compact_financial_value = format_compact_currency(full_financial_value)
                m2.metric("Financials", compact_financial_value)

                # Show a short badge in the metric card; full detail below
                policy_raw = data.get("policy_status", "N/A")
                if "CLEARED" in str(policy_raw).upper():
                    compliance_badge = "✅ SUCCESS"
                elif "POLICY" in str(policy_raw).upper() or "WARNING" in str(policy_raw).upper():
                    compliance_badge = "⚠️ WARNING"
                elif "ERROR" in str(policy_raw).upper():
                    compliance_badge = "❌ DANGER"
                else:
                    compliance_badge = "⚠️ WARNING"
                m3.metric("Compliance", compliance_badge)

                st.markdown("---")

                # Compliance detail block
                if "CLEARED" in str(policy_raw).upper():
                    st.success(f"🛡️ **Compliance:** {policy_raw}")
                elif "ERROR" in str(policy_raw).upper():
                    st.error(f"❌ **Compliance Error:** {policy_raw}")
                else:
                    st.warning(f"⚠️ **Compliance Policy Triggered:** {policy_raw}")

                # Weather & Recommendation
                st.markdown(f"🌊 **Weather:** {data.get('weather_summary', 'No summary available')}")
                st.markdown(f"💰 **Financial Exposure:** {full_financial_value}")
                
                # Try multiple keys for recommendation just in case
                rec = data.get('final_recommendation') or data.get('recommendation') or data.get('recommendations') or "No specific reroute recommendation provided by Swarm."
                st.info(f"💡 **Recommendation:** {rec}")


                st.markdown("---")
                if st.session_state.human_approval == "approved":
                    st.success("✅ **CHAT APPROVED:** Orders transmitted to vessel fleet.")
                elif st.session_state.human_approval == "rejected":
                    st.error("❌ **CHAT REJECTED:** Holding position.")
                else:
                    st.warning("⚠️ **Human Approval Required**")
                    btn_col1, btn_col2 = st.columns(2)

                    if btn_col1.button("✅ Approve Reroute", use_container_width=True):
                        st.session_state.human_approval = "approved"
                        st.rerun()

                    if btn_col2.button("❌ Reject & Revise", use_container_width=True):
                        st.session_state.human_approval = "rejected"
                        st.rerun()

# ── Chat Interface ─────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 💬 Director Chat")
st.caption("Talk directly to the LogisticsDirector. Ask follow-up questions or request a re-analysis with new constraints.")

# Only show chat after first mission run
if not st.session_state.session_ready:
    st.info("🚀 Run the initial chat above to activate the chat interface.")
else:
    # Render chat history (skip the very first exchange which is shown in the dashboard)
    for msg in st.session_state.chat_history[2:]:   # skip first user+assistant pair
        with st.chat_message(msg["role"], avatar="🧑‍✈️" if msg["role"] == "user" else "🤖"):
            st.markdown(msg["content"])

    # Chat input
    if user_input := st.chat_input("E.g. 'The risk is too high, find an alternative port via Lombok Strait'"):
        # Show user message immediately
        with st.chat_message("user", avatar="🧑‍✈️"):
            st.markdown(user_input)

        with st.chat_message("assistant", avatar="🤖"):
            response_placeholder = st.empty()
            response_placeholder.markdown("_Swarm agents thinking..._")

            lowered_input = user_input.lower()
            should_reanalyze = any(
                term in lowered_input
                for term in [
                    "reanalyze",
                    "re-analyze",
                    "analyze",
                    "new route",
                    "alternative route",
                    "alternative port",
                    "reroute",
                    "find another",
                    "different port",
                    "different route",
                    "via ",
                ]
            )

            if should_reanalyze:
                with st.spinner("Director re-analysing..."):
                    structured, raw_text = send_message(user_input, log_box, st.session_state.current_session_id)
                    if structured and "error" not in structured:
                        reply = generate_chat_reply_with_llm(user_input, structured)
                    else:
                        reply = raw_text or "Analysis complete. Check the trace log for details."
                    response_placeholder.markdown(reply)

                    if structured:
                        st.session_state.swarm_output = structured
            else:
                current_report = st.session_state.swarm_output or {}
                reply = generate_chat_reply_with_llm(user_input, current_report)
                response_placeholder.markdown(reply)

            st.session_state.chat_history.append({"role": "user",      "content": user_input})
            st.session_state.chat_history.append({"role": "assistant",  "content": reply})
            save_local_session_snapshot()

        st.rerun()