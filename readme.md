# 🤖 AI Blog Writing Agent

An autonomous multi-agent system built with **LangGraph** and **Streamlit** that researches, plans, writes, and generates images for high-quality technical blogs. 

## 🏗️ Project Architecture & Workflow

The backend uses a directed graph (LangGraph) to manage different AI agents:

| Node | Responsibility |
| :--- | :--- |
| **Router** | Decides if the topic needs live web research or relies on evergreen knowledge. |
| **Researcher** | Uses Tavily Search to fetch the latest data and formats it into evidence. |
| **Orchestrator** | Acts as a lead editor, creating a multi-section plan based on the research. |
| **Workers** | Parallel agents that write individual markdown sections based on the plan. |
| **Reducer** | Merges sections, decides where diagrams are needed, generates images via Gemini, and saves the final state to **Supabase**. |

<img width="234" height="935" alt="Screenshot from 2026-06-20 17-47-07" src="https://github.com/user-attachments/assets/df72146f-8879-47c7-802f-770c289960f2" />


## 🛠️ Tech Stack

* **Frontend:** Streamlit
* **Orchestration:** LangGraph & LangChain
* **LLM & Vision:** Google Gemini (3.1-Flash-Lite)
* **Search Engine:** Tavily API
* **Database:** Supabase (PostgreSQL)

---

## 🚀 Local Setup Guide

Follow these simple steps to run the application on your local machine.

### 1. Prerequisites
Make sure you have **Python 3.12** installed.

### 2. Setup Virtual Environment
Create and activate a virtual environment to keep dependencies isolated:
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
```

### 3. Install Dependencies
Install all required libraries from the requirements.txt file:

```bash
pip install -r requirements.txt
```

### 4. Environment Variables (.env)
Create a .env file in the root directory of your project. You will need the following API keys:

| Variable	| Required For	| Where to get it |
| :--- | :--- | :--- |
| GEMINI_API_KEY |	Text & Image Generation	| Google AI Studio |
| TAVILY_API_KEY	| Web Research	| Tavily Developer Portal |
| SUPABASE_URL	| Saving Past Blogs	| Supabase Project Settings |
| SUPABASE_KEY	| Saving Past Blogs	| Supabase Project Settings |

## Example .env format:

```bash
GEMINI_API_KEY="your_google_gemini_api_key"
TAVILY_API_KEY="your_tavily_api_key"
SUPABASE_URL="your_supabase_project_url"
SUPABASE_KEY="your_supabase_anon_key"
```

### 5. Run the Application
Start the Streamlit server:

```base
streamlit run bwa_frontend.py
```

The application will open in your default web browser at http://localhost:8501.

### 📁 Folder Structure
```text
├── bwa_frontend.py       # Main Streamlit UI entry point
├── requirements.txt      # Project dependencies
├── .env                  # Private API keys (Not pushed to Git)
├── images/               # Locally saved generated diagrams
└── backend/              # Modular LangGraph Logic
    ├── __init__.py       
    ├── config.py         # DB and LLM configurations
    ├── state.py          # Pydantic schemas and Graph State
    ├── nodes.py          # AI Agents (Router, Research, Orchestrator, Worker)
    ├── reducer.py        # Formatting, Image Generation, and DB Saving
    └── graph.py          # Main Graph compiler and edge routing
```

