from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, List, Iterator, Tuple

import pandas as pd
import streamlit as st

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

# -----------------------------
# Import your compiled LangGraph app
# -----------------------------
# from bwa_backend import app
#Added folder of backend for better readability 
from backend.graph import app


# -----------------------------
# Helpers
# -----------------------------
def safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def bundle_zip(md_text: str, md_filename: str, images_dir: Path) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(md_filename, md_text.encode("utf-8"))

        if images_dir.exists() and images_dir.is_dir():
            for p in images_dir.rglob("*"):
                if p.is_file():
                    z.write(p, arcname=str(p))
    return buf.getvalue()


def images_zip(images_dir: Path) -> Optional[bytes]:
    if not images_dir.exists() or not images_dir.is_dir():
        return None
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in images_dir.rglob("*"):
            if p.is_file():
                z.write(p, arcname=str(p))
    return buf.getvalue()


def try_stream(graph_app, inputs: Dict[str, Any]) -> Iterator[Tuple[str, Any]]:
    """
    Stream graph progress if available; else invoke.
    Yields ("updates"/"values"/"final", payload).
    """
    try:
        current_state = dict(inputs)
        for step in graph_app.stream(inputs, stream_mode="updates"):
            yield ("updates", step)
            
            # Keep track of the state as it streams so we don't need to invoke() again
            if isinstance(step, dict):
                if len(step) == 1 and isinstance(next(iter(step.values())), dict):
                    inner = next(iter(step.values()))
                    current_state.update(inner)
                else:
                    current_state.update(step)
                    
        # Yield the accumulated final state directly
        yield ("final", current_state)
        return
    except Exception:
        pass

    try:
        final_state = None
        for step in graph_app.stream(inputs, stream_mode="values"):
            yield ("values", step)
            final_state = step
            
        if final_state:
            yield ("final", final_state)
        return
    except Exception:
        pass

    # Fallback if streaming fails completely
    out = graph_app.invoke(inputs)
    yield ("final", out)

def extract_latest_state(current_state: Dict[str, Any], step_payload: Any) -> Dict[str, Any]:
    if isinstance(step_payload, dict):
        if len(step_payload) == 1 and isinstance(next(iter(step_payload.values())), dict):
            inner = next(iter(step_payload.values()))
            current_state.update(inner)
        else:
            current_state.update(step_payload)
    return current_state


# -----------------------------
# Markdown renderer that supports local images
# -----------------------------
_MD_IMG_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
_CAPTION_LINE_RE = re.compile(r"^\*(?P<cap>.+)\*$")


def _resolve_image_path(src: str) -> Path:
    src = src.strip().lstrip("./")
    return Path(src).resolve()



def render_markdown_with_local_images(md: str):
    # 1.Removing any JSON-wrapped text from the markdown (if present)
    clean_md = md
    if md.strip().startswith("{") and "'text':" in md:
        try:
            # Taking away text key from Regex and joining all text parts with double newlines
            matches = re.findall(r"'text':\s*'(.*?)'", md, re.DOTALL)
            clean_md = "\n\n".join(matches)
            clean_md = clean_md.replace(r"\n", "\n")
        except:
            clean_md = md

    # 2. Finding Images in Markdown
    matches = list(_MD_IMG_RE.finditer(clean_md))
    
    # Directly render if no images found
    if not matches:
        st.markdown(clean_md, unsafe_allow_html=True)
        return

    # 3. If images found, split into parts (md/img) for sequential rendering
    parts: List[Tuple[str, str]] = []
    last = 0
    for m in matches:
        before = clean_md[last : m.start()]
        if before:
            parts.append(("md", before))
        
        alt = (m.group("alt") or "").strip()
        src = (m.group("src") or "").strip()
        parts.append(("img", f"{alt}|||{src}"))
        last = m.end()

    tail = clean_md[last:]
    if tail:
        parts.append(("md", tail))

    # 4. Render loop
    i = 0
    while i < len(parts):
        kind, payload = parts[i]
        if kind == "md":
            st.markdown(payload, unsafe_allow_html=True)
            i += 1
        else:
            alt, src = payload.split("|||", 1)
            # Resolve image path if it's a local file
            if src.startswith("http"):
                st.image(src, caption=alt or None, width="stretch")
            else:
                img_path = _resolve_image_path(src)
                if img_path.exists():
                    st.image(str(img_path), caption=alt or None, width="stretch")
                else:
                    st.warning(f"Image not found: {src}")
            i += 1

# -----------------------------
# ✅ NEW: Past blogs helpers
# -----------------------------
# def list_past_blogs() -> List[Path]:
#     """
#     Returns .md files in current working directory, newest first.
#     Filters out obvious non-blog markdown files if needed.
#     """
#     cwd = Path(".")
#     files = [p for p in cwd.glob("*.md") if p.is_file()]
#     files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
#     return files


# def read_md_file(p: Path) -> str:
#     return p.read_text(encoding="utf-8", errors="replace")


def extract_title_from_md(md: str, fallback: str) -> str:
    """
    Use first '# ' heading as title if present.
    """
    for line in md.splitlines():
        if line.startswith("# "):
            t = line[2:].strip()
            return t or fallback
    return fallback

#Directly fetch past blogs from Supabase instead of local files
def get_past_blogs_from_db():
    if not supabase: return []
    try:
        response = supabase.table("blogs").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        st.error(f"Error fetching past blogs: {e}")
        return []
    

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="LangGraph Blog Writer", layout="wide")

st.title("Blog Writing Agent")


with st.sidebar:
    st.header("Generate New Blog")
    with st.form("generation_form"):
        topic = st.text_area("Topic", height=120)
        as_of = st.date_input("As-of date", value=date.today())
       
        run_btn = st.form_submit_button("🚀 Generate Blog", type="primary")


    st.divider()
    st.subheader("Past blogs")

    past_blogs = get_past_blogs_from_db()
    if not past_blogs:
        st.caption("No saved blogs found in Database.")
        selected_blog = None
    else:
        options = []
        blog_by_label = {}
        for b in past_blogs:
            date_str = b.get("created_at", "")[:10]
            label = f"{b.get('title', 'Untitled')} ({date_str})"
            options.append(label)
            blog_by_label[label] = b

        selected_label = st.radio(
            "Select a blog to load",
            options=options,
            index=0,
            label_visibility="collapsed",
        )
        selected_blog = blog_by_label.get(selected_label)

        if st.button("📂 Load selected blog"):
            if selected_blog:
                # If JSON state is available, load it; else, create a minimal state with just the final content.
                if selected_blog.get("full_state"):
                    st.session_state["last_out"] = selected_blog["full_state"]
                else:
                    st.session_state["last_out"] = {
                        "plan": None,
                        "evidence": [],
                        "image_specs": [],
                        "final": selected_blog.get("content", "")
                    }
                st.session_state["topic_prefill"] = selected_blog.get("title", "")
   

# Keep your topic input as-is; optionally prefill for next run after loading a blog
if "topic_prefill" in st.session_state and isinstance(st.session_state["topic_prefill"], str):
    # Do not mutate widgets; just keep as a hint.
    pass

# Storage for latest run
if "last_out" not in st.session_state:
    st.session_state["last_out"] = None

# Layout
# tab_plan, tab_evidence, tab_preview, tab_images, tab_logs = st.tabs(
#     ["🧩 Plan", "🔎 Evidence", "📝 Markdown Preview", "🖼️ Images", "🧾 Logs"]
# )
tab_plan, tab_evidence, tab_preview, tab_images = st.tabs(
    ["🧩 Plan", "🔎 Evidence", "📝 Markdown Preview", "🖼️ Images"]
)

# logs: List[str] = []


# def log(msg: str):
#     logs.append(msg)


if run_btn:
    if not topic.strip():
        st.warning("Please enter a topic.")
        st.stop()

    inputs: Dict[str, Any] = {
        "topic": topic.strip(),
        "mode": "",
        "needs_research": False,
        "queries": [],
        "evidence": [],
        "plan": None,
        "as_of": as_of.isoformat(),
        "recency_days": 7,
        "sections": [],
        "merged_md": "",
        "md_with_placeholders": "",
        "image_specs": [],
        "final": "",
    }

    status = st.status("Running graph…", expanded=True)
    progress_area = st.empty()

    current_state: Dict[str, Any] = {}
    last_node = None

    for kind, payload in try_stream(app, inputs):
        if kind in ("updates", "values"):
            node_name = None
            if isinstance(payload, dict) and len(payload) == 1 and isinstance(next(iter(payload.values())), dict):
                node_name = next(iter(payload.keys()))
            if node_name and node_name != last_node:
                status.write(f"➡️ Node: `{node_name}`")
                last_node = node_name

            current_state = extract_latest_state(current_state, payload)

            summary = {
                "mode": current_state.get("mode"),
                "needs_research": current_state.get("needs_research"),
                "queries": current_state.get("queries", [])[:5] if isinstance(current_state.get("queries"), list) else [],
                "evidence_count": len(current_state.get("evidence", []) or []),
                "tasks": len((current_state.get("plan") or {}).get("tasks", [])) if isinstance(current_state.get("plan"), dict) else None,
                "images": len(current_state.get("image_specs", []) or []),
                "sections_done": len(current_state.get("sections", []) or []),
            }
            progress_area.json(summary)

            # log(f"[{kind}] {json.dumps(payload, default=str)[:1200]}")

        elif kind == "final":
            out = payload
            st.session_state["last_out"] = out
            status.update(label="✅ Done", state="complete", expanded=False)
            # log("[final] received final state")

# Render last result (if any)
out = st.session_state.get("last_out")
if out:
    # --- Plan tab ---
    with tab_plan:
        st.subheader("Plan")
        plan_obj = out.get("plan")
        if not plan_obj:
            st.info("No plan found in output.")
        else:
            if hasattr(plan_obj, "model_dump"):
                plan_dict = plan_obj.model_dump()
            elif isinstance(plan_obj, dict):
                plan_dict = plan_obj
            else:
                plan_dict = json.loads(json.dumps(plan_obj, default=str))

            st.write("**Title:**", plan_dict.get("blog_title"))
            cols = st.columns(3)
            cols[0].write("**Audience:** " + str(plan_dict.get("audience")))
            cols[1].write("**Tone:** " + str(plan_dict.get("tone")))
            cols[2].write("**Blog kind:** " + str(plan_dict.get("blog_kind", "")))

            tasks = plan_dict.get("tasks", [])
            if tasks:
                df = pd.DataFrame(
                    [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                            "target_words": t.get("target_words"),
                            "requires_research": t.get("requires_research"),
                            "requires_citations": t.get("requires_citations"),
                            "requires_code": t.get("requires_code"),
                            "tags": ", ".join(t.get("tags") or []),
                        }
                        for t in tasks
                    ]
                ).sort_values("id")
                st.dataframe(df, width="stretch", hide_index=True)

                with st.expander("Task details"):
                    st.json(tasks)

    # --- Evidence tab ---
    with tab_evidence:
        st.subheader("Evidence")
        evidence = out.get("evidence") or []
        if not evidence:
            st.info("No evidence returned (maybe closed_book mode or no Tavily key/results).")
        else:
            rows = []
            for e in evidence:
                if hasattr(e, "model_dump"):
                    e = e.model_dump()
                rows.append(
                    {
                        "title": e.get("title"),
                        "published_at": e.get("published_at"),
                        "source": e.get("source"),
                        "url": e.get("url"),
                    }
                )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # --- Preview tab ---
    with tab_preview:
        st.subheader("Markdown Preview")
        final_md = out.get("final") or ""
        if not final_md:
            st.warning("No final markdown found.")
        else:
            render_markdown_with_local_images(final_md)

            plan_obj = out.get("plan")
            if hasattr(plan_obj, "blog_title"):
                blog_title = plan_obj.blog_title
            elif isinstance(plan_obj, dict):
                blog_title = plan_obj.get("blog_title", "blog")
            else:
                # fallback: parse from markdown title
                blog_title = extract_title_from_md(final_md, "blog")

            md_filename = f"{safe_slug(blog_title)}.md"
            st.download_button(
                "⬇️ Download Markdown",
                data=final_md.encode("utf-8"),
                file_name=md_filename,
                mime="text/markdown",
            )

            bundle = bundle_zip(final_md, md_filename, Path("images"))
            st.download_button(
                "📦 Download Bundle (MD + images)",
                data=bundle,
                file_name=f"{safe_slug(blog_title)}_bundle.zip",
                mime="application/zip",
            )

    # --- Images tab ---
    with tab_images:
        st.subheader("Images")
        specs = out.get("image_specs") or []
        images_dir = Path("images")

        if not specs and not images_dir.exists():
            st.info("No images generated for this blog.")
        else:
            if specs:
                st.write("**Image plan:**")
                st.json(specs)

            if images_dir.exists():
                files = [p for p in images_dir.iterdir() if p.is_file()]
                if not files:
                    st.warning("images/ exists but is empty.")
                else:
                    for p in sorted(files):
                        st.image(str(p), caption=p.name, width="stretch")

                z = images_zip(images_dir)
                if z:
                    st.download_button(
                        "⬇️ Download Images (zip)",
                        data=z,
                        file_name="images.zip",
                        mime="application/zip",
                    )

    # --- Logs tab ---
    # with tab_logs:
    #     st.subheader("Logs")
    #     if "logs" not in st.session_state:
    #         st.session_state["logs"] = []
    #     if logs:
    #         st.session_state["logs"].extend(logs)

    #     st.text_area("Event log", value="\n\n".join(st.session_state["logs"][-80:]), height=520)
else:
    st.info("Enter a topic and click **Generate Blog**.")
