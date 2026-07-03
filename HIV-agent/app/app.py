import os
import time

from dotenv import load_dotenv

load_dotenv()

import asyncio
import contextlib
import queue
import threading

import logs
import streamlit as st

# Deprecated: this Streamlit interface is retained only for historical reference.
# The operational frontend is in ../frontend and should be used for current work.
# It still references the old Mistral-bound PoC path and must not be used as
# the current clinical runtime.


# Load CSS and JS
def load_css():
    css_file = os.path.join(os.path.dirname(__file__), "custom_styles.css")
    try:
        with open(css_file) as f:
            css_content = f.read()
        st.markdown(f"<style>{css_content}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass


load_css()

# Adjust layout when sidebar is collapsed
st.markdown(
    """
<script>
(function() {
    const update = () => {
        const body = document.body;
        if (!body) return;
        const sidebar = document.querySelector('[data-testid="stSidebar"]');
        if (!sidebar) {
            body.classList.add('kq-sidebar-collapsed');
            return;
        }
        const width = sidebar.getBoundingClientRect().width;
        if (width < 40) {
            body.classList.add('kq-sidebar-collapsed');
        } else {
            body.classList.remove('kq-sidebar-collapsed');
        }
    };
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener('resize', update);
    setInterval(update, 300);
    update();
})();
</script>
""",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def _get_async_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop


# API Key check
if not os.environ.get("MISTRAL_API_KEY"):
    try:
        key = st.secrets.get("MISTRAL_API_KEY")
        if key:
            os.environ["MISTRAL_API_KEY"] = key
    except Exception:
        pass

# Config
PDF_PATH = "Kenya-ARV-Guidelines-2022-Final-1.pdf"
REPO_OWNER = "MOH-Kenya"
REPO_NAME = "HIV-Guidelines"

st.set_page_config(page_title="Kini", layout="wide")


# Check API key
def _check_api_key():
    if not os.environ.get("MISTRAL_API_KEY"):
        st.error("Mistral API key is not set. Add MISTRAL_API_KEY to your .env file.")
        st.stop()


_check_api_key()

# Background agent init
_agent_holder = {}
_init_thread = None
_init_lock = threading.Lock()


def _heavy_init():
    import ingest
    import search_agent

    index = ingest.index_data(PDF_PATH)
    return search_agent.init_agent(index)


def _run_heavy_init():
    try:
        agent = _heavy_init()
        with _init_lock:
            _agent_holder["agent"] = agent
    except Exception as e:
        with _init_lock:
            _agent_holder["error"] = e


def get_agent():
    with _init_lock:
        if "agent" in _agent_holder:
            return _agent_holder["agent"]
        if "error" in _agent_holder:
            raise _agent_holder["error"]
        global _init_thread
        if _init_thread is None:
            _init_thread = threading.Thread(target=_run_heavy_init, daemon=True)
            _init_thread.start()
    return None


# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_page" not in st.session_state:
    st.session_state.current_page = "Quick chat"
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

# Initialize Query Builder state
if "qb_patient" not in st.session_state:
    st.session_state.qb_patient = "Select..."
if "qb_condition" not in st.session_state:
    st.session_state.qb_condition = "Select..."
if "qb_comorbidity" not in st.session_state:
    st.session_state.qb_comorbidity = "Select..."
if "qb_first" not in st.session_state:
    st.session_state.qb_first = False
if "qb_second" not in st.session_state:
    st.session_state.qb_second = False
if "qb_prophy" not in st.session_state:
    st.session_state.qb_prophy = False
if "qb_monitor" not in st.session_state:
    st.session_state.qb_monitor = False


# Stream response using threading and queue - proven working approach
_STREAM_END = object()


def stream_response(prompt: str):
    agent = get_agent()
    chunk_queue = queue.Queue()
    result_holder = {}

    async def run_async():
        full_text = ""
        messages = []
        try:
            async with agent.run_stream(user_prompt=prompt) as result:
                async for chunk in result.stream_text(delta=True, debounce_by=0.01):
                    if chunk:
                        full_text += chunk
                        chunk_queue.put(chunk)
                if not full_text:
                    output = await result.get_output()
                    full_text = str(output) if output else "No response generated."
                    chunk_queue.put(full_text)
                messages = result.new_messages()
            result_holder["full_text"] = full_text
            result_holder["messages"] = messages
        except asyncio.CancelledError:
            pass
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            result_holder["full_text"] = error_msg
            chunk_queue.put(error_msg)
        finally:
            chunk_queue.put(_STREAM_END)

    loop = _get_async_loop()
    try:
        future = asyncio.run_coroutine_threadsafe(run_async(), loop)
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            st.cache_resource.clear()
            loop = _get_async_loop()
            future = asyncio.run_coroutine_threadsafe(run_async(), loop)
        else:
            raise

    # Yield chunks as they arrive
    try:
        while True:
            piece = chunk_queue.get()
            if piece is _STREAM_END:
                break
            yield piece
    except GeneratorExit:
        pass
    finally:
        if not future.done():
            future.cancel()

    # Log and store result
    st.session_state._last_response = result_holder.get("full_text", "")
    if result_holder.get("messages"):
        with contextlib.suppress(BaseException):
            logs.log_interaction_to_file(agent, result_holder["messages"])


def format_timestamp():
    from datetime import datetime

    return datetime.now().strftime("%H:%M")


# Build query prefix from session state
def build_query_prefix():
    parts = []

    pt = st.session_state.get("qb_patient", "Select...")
    cond = st.session_state.get("qb_condition", "Select...")
    comorb = st.session_state.get("qb_comorbidity", "Select...")

    if pt != "Select...":
        parts.append(pt)
    if cond != "Select...":
        parts.append(cond)
    if comorb not in ["Select...", "None"]:
        parts.append(f"with {comorb}")

    filters = []
    if st.session_state.get("qb_first"):
        filters.append("first-line")
    if st.session_state.get("qb_second"):
        filters.append("second-line")
    if st.session_state.get("qb_prophy"):
        filters.append("prophylaxis")
    if st.session_state.get("qb_monitor"):
        filters.append("monitoring")

    result = ""
    if parts:
        result += "For " + " ".join(parts).lower() + ": "
    if filters:
        result += "[" + ", ".join(filters) + "] "

    return result


# Sidebar
with st.sidebar:
    st.markdown("**Kini**")

    pages = ["Quick chat", "Query builder", "About"]
    for page in pages:
        is_active = st.session_state.current_page == page
        btn_type = "primary" if is_active else "secondary"
        if st.button(page, key=f"nav_{page}", use_container_width=True, type=btn_type):
            if not is_active:
                st.session_state.current_page = page
                st.rerun()

# Handle pending prompts from query builder or sample questions
if st.session_state.pending_prompt:
    prompt_text = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

    agent = get_agent()
    if agent is None:
        with st.spinner("Loading..."):
            while get_agent() is None:
                time.sleep(0.5)
        agent = get_agent()

    st.session_state.messages.append({"role": "user", "content": prompt_text})

    try:
        with st.chat_message("user"):
            st.markdown(prompt_text)

        with st.chat_message("assistant"):
            response = ""
            status_placeholder = st.empty()
            response_placeholder = st.empty()
            status_placeholder.markdown(
                '<div class="kq-thinking" aria-hidden="true">'
                "<span></span><span></span><span></span></div>",
                unsafe_allow_html=True,
            )
            for chunk in stream_response(prompt_text):
                response += chunk
                response_placeholder.markdown(response)
                if status_placeholder:
                    status_placeholder.empty()
            if status_placeholder:
                status_placeholder.empty()
            st.caption(format_timestamp())

        st.session_state.messages.append({"role": "assistant", "content": response})
    except Exception as e:
        st.error(f"Error: {str(e)}")
        st.session_state.messages.append({"role": "assistant", "content": f"Error: {str(e)}"})

    st.rerun()

# Quick chat page
if st.session_state.current_page == "Quick chat":
    cols = st.columns([10, 1])
    with cols[0]:
        st.title("Kini")
        st.caption("Evidence-based answers from Kenya National HIV Treatment Guidelines")
    with cols[1]:
        st.write("")
        if st.button("Clear"):
            st.session_state.messages = []
            st.rerun()

    # Display messages
    for msg in st.session_state.messages:
        role = msg["role"]
        content = msg["content"]
        with st.chat_message(role):
            st.markdown(content)
            if role == "assistant":
                st.caption(format_timestamp())

    # Sample questions
    if len(st.session_state.messages) == 0:
        st.markdown('<div class="kq-section-title">Quick questions</div>', unsafe_allow_html=True)
        questions = [
            "What are the first-line ART regimens for adults?",
            "When should ART be initiated after HIV diagnosis?",
            "What are the eligibility criteria for starting ART?",
            "How should pregnant women with HIV be treated?",
        ]
        st.markdown('<div class="kq-quick">', unsafe_allow_html=True)
        qcols = st.columns(2)
        for i, q in enumerate(questions):
            with qcols[i % 2]:
                if st.button(q, key=f"q_{i}", use_container_width=True):
                    st.session_state.pending_prompt = q
                    st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # Input
    user_input = st.chat_input("Ask your question...")
    if user_input:
        st.session_state.pending_prompt = user_input
        st.rerun()

# Smart query builder page
elif st.session_state.current_page == "Query builder":
    cols = st.columns([10, 1])
    with cols[0]:
        st.title("Query builder")
        st.caption("Build structured clinical queries with context")
    with cols[1]:
        st.write("")
        if st.button("Clear"):
            st.session_state.qb_patient = "Select..."
            st.session_state.qb_condition = "Select..."
            st.session_state.qb_comorbidity = "Select..."
            st.session_state.qb_first = False
            st.session_state.qb_second = False
            st.session_state.qb_prophy = False
            st.session_state.qb_monitor = False
            st.rerun()

    st.markdown('<div class="kq-qb">', unsafe_allow_html=True)

    # Query Preview
    st.markdown('<div class="kq-qb-divider"></div>', unsafe_allow_html=True)
    st.markdown("**Query Preview**")
    preview = build_query_prefix()
    if preview:
        st.code(preview)
    else:
        st.markdown("*Select options below to build your query*")

    # Clinical Context
    st.markdown('<div class="kq-qb-divider"></div>', unsafe_allow_html=True)
    st.markdown("**Clinical Context**")
    ctx_cols = st.columns(3)
    with ctx_cols[0]:
        st.selectbox(
            "Patient Type",
            ["Select...", "Adult", "Adolescent (10-19)", "Child (<10)", "Infant (<1)"],
            key="qb_patient",
            label_visibility="collapsed",
        )
    with ctx_cols[1]:
        st.selectbox(
            "Condition",
            [
                "Select...",
                "Treatment-naive",
                "Treatment-experienced",
                "Pregnant",
                "Breastfeeding",
            ],
            key="qb_condition",
            label_visibility="collapsed",
        )
    with ctx_cols[2]:
        st.selectbox(
            "Comorbidity",
            [
                "Select...",
                "None",
                "TB",
                "Hepatitis B",
                "Hepatitis C",
                "Chronic kidney disease",
            ],
            key="qb_comorbidity",
            label_visibility="collapsed",
        )

    # Filters
    st.markdown('<div class="kq-qb-divider"></div>', unsafe_allow_html=True)
    st.markdown("**Filters**")
    fcols = st.columns(4)
    with fcols[0]:
        st.checkbox("First-line", key="qb_first")
    with fcols[1]:
        st.checkbox("Second-line", key="qb_second")
    with fcols[2]:
        st.checkbox("Prophylaxis", key="qb_prophy")
    with fcols[3]:
        st.checkbox("Monitoring", key="qb_monitor")

    # Question
    st.markdown('<div class="kq-qb-divider"></div>', unsafe_allow_html=True)
    st.markdown("**Your Question**")
    query_text = st.text_area(
        "Question",
        placeholder="What are the recommended regimens?",
        label_visibility="collapsed",
        height=80,
    )

    if st.button("Submit Query", use_container_width=True):
        if query_text.strip():
            prefix = build_query_prefix()
            full_query = prefix + query_text if prefix else query_text
            st.session_state.pending_prompt = full_query
            st.session_state.current_page = "Quick chat"
            st.rerun()
        else:
            st.warning("Please enter a question")

    st.markdown("</div>", unsafe_allow_html=True)

# About Page
elif st.session_state.current_page == "About":
    st.markdown(
        """
    <div class="kq-about-title">Kini</div>

    Kini is an evidence-based clinical decision support tapping on the official Kenya National HIV Prevention and Treatment Guidelines.
    Kini provides healthcare professionals guideline-based information to support patient service delivery.

    **Coverage:**
    - ART Regimens & Eligibility
    - Dosing & Special Populations
    - TB/HIV Co-infection
    - PMTCT & Pregnancy
    - Monitoring & Follow-up

    **Version:** 0.0.1

    """,
        unsafe_allow_html=True,
    )
