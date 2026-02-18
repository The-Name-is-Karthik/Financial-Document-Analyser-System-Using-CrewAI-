# Financial Document Analyzer — Complete Code & Pipeline Deep Dive

> This document explains **every line of code** across all six source files and maps
> every possible execution workflow — including the full 4-stage agent pipeline,
> how context flows between agents, and all edge cases.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [The Dynamic Pipeline (Router)](#2-the-dynamic-pipeline-router)
3. [router.py — Line by Line](#3-routerpy--line-by-line)
4. [tools.py — Line by Line](#4-toolspy--line-by-line)
5. [agents.py — Line by Line](#5-agentspy--line-by-line)
6. [task.py — Line by Line](#6-taskpy--line-by-line)
7. [database.py — Line by Line](#7-databasepy--line-by-line)
8. [worker.py — Line by Line](#8-workerpy--line-by-line)
9. [main.py — Line by Line](#9-mainpy--line-by-line)
10. [pdf_gen.py — Line by Line](#10-pdf_genpy--line-by-line)
11. [Workflow Walkthroughs](#11-workflow-walkthroughs)
12. [Complete Data Flow Diagram](#12-complete-data-flow-diagram)
13. [Configuration Reference](#13-configuration-reference)
14. [Sample Test Document: Tesla Q2 2025](#14-sample-test-document-tesla-q2-2025-datatslaq2-2025-updatepdf)

---

## 1. System Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENT (curl / browser / SDK)                 │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ HTTP multipart/form-data
┌────────────────────────────────▼─────────────────────────────────────┐
│                          main.py  (FastAPI)                           │
│   POST /analyze    GET /jobs/{id}    GET /jobs    GET /              │
└───────┬──────────────────────────────────────────────────────────────┘
        │
        ├─── router.py (INTELLIGENT ROUTER) ──────────────────────────┐
        │    Intent: QUICK / AUDIT / ADVISORY                         │
        │                                                             │
        ┌─────────────────────────────────────────────────────────────▼─────────┐
        │                         CrewAI Crew                                   │
        │  Process.sequential — Dynamic Tasks (1–4) based on Intent             │
        │                                                                       │
        │  ┌──────────────────────────┐                                         │
        │  │ Stage 1: verifier        │ (Always runs)                           │
        │  └────────────┬─────────────┘                                         │
        │               │                                                       │
        │       ┌───────▼──────────────────────────┐                            │
        │       │ Intent Logic (router.py)         │                            │
        │       └───────┬──────────────┬───────────┘                            │
        │               │              │                                        │
        │   ┌───────────▼────────┐  ┌──▼────────────────┐  ┌──────────────────┐  │
        │   │ QUICK: Analyst only│  │ AUDIT: Risk only  │  │ ADVISORY: Full   │  │
        │   └────────────────────┘  └───────────────────┘  └──────────────────┘  │
        │                                                                       │
        └───────────────────────────┬───────────────────────────────────────────┘
                                    │ final result
        ┌───────────────────────────▼──────────┐
        │            database.py               │
        │    AnalysisJob   DocumentRecord      │
        └──────────────────────────────────────┘
```

---

---

## 2. The Dynamic Pipeline (Router)

In high-concurrency or cost-sensitive environments (like the Gemini Free Tier), running a full 4-agent sequential pipeline is often overkill and wasteful. If a user asks "What was the revenue?", running a Risk Assessor and an Investment Advisor consumes computational quota without adding value to that specific query.

The system now implements **Dynamic Routing** via `router.py`.

### Intent Paths

| Path | Query Type | Loaded Agents |
| :--- | :--- | :--- |
| **QUICK** | Factual extraction (numbers, dates, names) | Verifier, Financial Analyst |
| **AUDIT** | Safety, debt, risks, or red flags | Verifier, Risk Assessor |
| **ADVISORY** | Recommendations, strategy, or thesis | Full 4-Agent Pipeline |

---

## 3. router.py — Line by Line

The `router.py` module acts as a "Fast Classifier" that determines the minimal set of agents required to fulfill the request.

### `classify_intent` — The Logical Dispatcher

```python
def classify_intent(query: str) -> str:
    prompt = f"""
    Classify this financial query into exactly one category: 
    - QUICK: Factual extraction, numbers, dates...
    - AUDIT: Concern about safety, debt, risks...
    - ADVISORY: Investment recommendation, buying strategy...
    """
    response = llm.call([{"role": "user", "content": prompt}])
    return response.strip().upper()
```
By making a single, cheap LLM call at the very start, we save up to **10+ expensive agent calls** downstream. This classification determines which "Execution Path" the system follows.

### `get_active_crew` — Dynamic Assembly

```python
def get_active_crew(query: str) -> Crew:
    intent = classify_intent(query)
    active_tasks = [verification_task]
    active_agents = [verifier]
    
    if intent == "QUICK":
        active_tasks.append(document_analysis_task)
    elif intent == "AUDIT":
        active_tasks.append(risk_assessment_task)
    else: 
        # ADVISORY: Full 4-stage pipeline
```
This function is responsible for **Dynamic Context Re-wiring**. If the `financial_analyst` (Step 2) is skipped in an **AUDIT** run, the `risk_assessment_task.context` is automatically re-pointed to just the `verification_task`, preventing the agent from waiting for context data that doesn't exist.

---
| 2 | `financial_analyst` | `document_analysis_task` | PDF + Stage 1 context | Financial analysis report |
| 3 | `risk_assessor` | `risk_assessment_task` | PDF + Stages 1–2 context | Risk matrix + scenario analysis |
| 4 | `investment_advisor` | `investment_analysis_task` | PDF + Stages 1–3 context | Investment recommendations |

### How context flows — the key mechanism

CrewAI's `Task.context` parameter is what connects the stages. When you set:

```python
risk_assessment_task.context = [verification_task, document_analysis_task]
```

CrewAI takes the *output strings* of those two completed tasks and injects them as
additional context blocks in `risk_assessor`'s prompt before that agent starts.
The risk_assessor therefore sees everything the verifier and analyst found — without
re-reading the PDF from scratch (though it can still call the PDF tool if it needs to
verify a specific figure).

The final value returned to the client is **Stage 4's output** — the investment
recommendation — which implicitly synthesises all four stages.

---

## 3. tools.py — Line by Line

```python
import os
```
Used for `os.path.exists()` — checks the uploaded file exists on disk before trying
to open it, turning a raw `FileNotFoundError` into a clean, agent-readable error string.

```python
from dotenv import load_dotenv
load_dotenv()
```
Reads `.env` and injects key-value pairs into `os.environ`. Called at the top of
`tools.py` because `SerperDevTool()` reads `SERPER_API_KEY` from the environment
immediately on instantiation — if `load_dotenv()` ran later, the key would not be
present yet.

```python
from crewai_tools import SerperDevTool
from crewai.tools import tool
```
`SerperDevTool` — CrewAI's wrapper for the Serper Google Search API, used by
`financial_analyst` to look up real-time market data.
`@tool` — a decorator that registers a plain Python function as a CrewAI-compatible
tool, extracting its name and docstring so the LLM can read them when deciding which
tool to call.

```python
from langchain_community.document_loaders import PyPDFLoader
```
`PyPDFLoader` uses the `pypdf` library to parse a PDF into a list of `Document` objects,
one per page, each with `page_content` (raw extracted text) and `metadata` (page number,
source path). This was the critical missing import in the original code — `Pdf(...)` was
used but never defined or imported anywhere, causing an immediate `NameError` at runtime.

```python
search_tool = SerperDevTool()
```
A single shared instance created at module level. All agents that receive this tool share
one object. This is safe because Serper calls are stateless (each call is a fresh HTTP
request). Module-level instantiation avoids creating a new HTTP session for every agent
invocation.

---

### `read_financial_document` — the primary tool, used by all 4 agents

```python
@tool("Read Financial Document")
def read_financial_document(path: str = "data/sample.pdf") -> str:
```
`"Read Financial Document"` is the tool's display name — what the LLM sees when
deciding which tool to call. The function is synchronous (not `async`) because
CrewAI's tool executor runs in a synchronous context. The default `"data/sample.pdf"`
allows manual testing without the API. The project ships with a real sample document
at `data/TSLA-Q2-2025-Update.pdf` (Tesla's Q2 2025 earnings release, 30 pages)
— see **Section 12** for a full walkthrough of the pipeline run against it.

The docstring (lines below the `def`) is not just documentation — CrewAI's tool
system uses it to build the tool description that the LLM reads. A clear, accurate
docstring directly improves the agent's ability to call the tool correctly.

```python
    if not os.path.exists(path):
        return f"Error: File not found at path '{path}'..."
```
Returns a descriptive error *string* rather than raising an exception. In CrewAI, a
tool return value is an "observation" fed back to the LLM. A clear error lets the
agent understand what happened and potentially retry with a different path. An
unhandled exception would crash the entire crew run immediately.

```python
    loader = PyPDFLoader(path)
    docs = loader.load()
```
`PyPDFLoader(path)` creates the object (no file I/O yet). `.load()` opens the file and
iterates through every page, returning a list of `Document` objects. A 50-page earnings
release produces 50 `Document` elements.

```python
    if not docs:
        return "Error: PDF appears to be empty or could not be parsed."
```
Guards against PDFs that are structurally valid files but contain no extractable text
— e.g., scanned-image-only PDFs with no OCR layer. Without this guard, the for-loop
below would silently produce an empty string and all four agents would incorrectly
conclude the document has no content.

```python
    for page in docs:
        content = page.page_content
        while "\n\n" in content:
            content = content.replace("\n\n", "\n")
        full_report += content.strip() + "\n"
    return full_report.strip()
```
Collapses consecutive blank lines (common in financial PDFs between sections) into
single newlines. These waste LLM context tokens if left uncollapsed. `.strip()` on
each page removes page-level leading/trailing whitespace; the final `.strip()` cleans
the assembled document string.

---

### `analyze_investment` — investment signal extractor (Stage 4 tool)

```python
@tool("Analyze Investment Data")
def analyze_investment(path: str) -> str:
    """Extract and structure key investment signals from financial document text."""
```

**Purpose:** Deterministic, regex-based extraction that gives the `investment_advisor`
a structured signal report it can use to cross-check and supplement the LLM-generated
analysis from Stage 2. It does what an LLM cannot do reliably — run the same extraction
logic on every document and produce consistent, machine-readable output.

**What it extracts:**

| Section | Patterns matched | Example output (Tesla Q2 2025) |
|---------|-----------------|-------------------------------|
| Revenue signals | `revenues? ... $N[BMK]` | `Total revenues 22,496` |
| EPS signals | `EPS ... $N.NN` | `diluted (GAAP) 0.33 -18%` |
| Margin signals | `gross/operating/EBITDA margin ... N%` | `Operating margin 4.1%` |
| Cash & liquidity | `cash ... $N[BMK]` | `Cash, equivalents 36,782` |
| Free cash flow | `free cash flow ... $N[BMK]` | `Free cash flow 146 -89%` |
| Growth rate flags | `N% YoY/QoQ` | `12% YoY`, `42% YoY` |
| Sentiment keywords | Hard-coded positive/negative word lists | Positive: expansion; Negative: tariff, uncertain |

**Why regex, not LLM:** An LLM might paraphrase a revenue figure, combine two quarters,
or round numbers. Regex operates on the raw text and returns the exact string the
document contains — useful for cross-verification.

**Why it's assigned to `investment_advisor` and not `financial_analyst`:** The
`financial_analyst` already reads the full PDF directly. The investment_advisor
receives the analyst's report via context but does not read the PDF as its primary
action. This tool gives the advisor a way to run its own deterministic pass over the
figures to spot-check the analyst's work before forming recommendations.

**How the `investment_advisor` uses it:**

```
1. Call Analyze Investment Data(file_path) → get structured signal report
2. Compare signal report against Stage 2 analyst context
3. If discrepancies found → note them in the investment thesis
4. Build recommendations using the verified metrics
```

**Tool call flow (typical):**
```
investment_advisor iteration 1: calls Analyze Investment Data → gets signal report
investment_advisor iteration 2: synthesises context + signal report → drafts recommendations
investment_advisor iteration 3: adds risk disclosure → finalises output
```

---

### `create_risk_assessment` — risk inventory scanner (Stage 3 tool)

```python
@tool("Create Risk Assessment")
def create_risk_assessment(path: str) -> str:
    """Scan financial document text for risk-related disclosures and produce a structured risk inventory."""
```

**Purpose:** Deterministic keyword scan across four risk categories with preliminary
severity ratings. Gives the `risk_assessor` a starting inventory — so the agent spends
its `max_iter` budget deepening and validating findings rather than discovering them
from scratch through LLM reasoning alone.

**The four risk categories and their keywords:**

```python
RISK_CATEGORIES = {
    "MARKET RISK":      [("tariff","High"), ("trade policy","High"),
                         ("foreign exchange","Medium"), ("inflation","Medium"), ...],
    "CREDIT RISK":      [("default","High"), ("debt covenant","High"),
                         ("non-recourse debt","Medium"), ...],
    "LIQUIDITY RISK":   [("cash runway","High"), ("CapEx","Medium"),
                         ("debt maturit","High"), ...],
    "OPERATIONAL RISK": [("supply chain","High"), ("export control","High"),
                         ("regulatory","Medium"), ("litigation","High"), ...],
}
```

**What preliminary severity means:** The severity ratings in the tool are based purely
on keyword category, not on the magnitude or context of what the document says. The
`risk_assessor` agent is explicitly instructed to validate and adjust these ratings
using its expert judgement. The tool says "tariff → High" as a starting point; the
agent decides if the specific tariff exposure described in the document justifies
High, Medium, or Low after reading the context.

**What the output looks like (Tesla Q2 2025):**
```
[MARKET RISK]
  ⚠ Severity: High
    Keyword : 'tariff'
    Context : ...uncertain macroeconomic environment resulting from shifting tariffs,
              unclear impacts from changes to fiscal policy...

[OPERATIONAL RISK]
  ⚠ Severity: High
    Keyword : 'Supply chain'
    Context : ...Supply chain robustness has enabled a resilient vehicle capacity
              base despite trade and policy uncertainties...

[SEVERITY SUMMARY]
  High   : 5
  Medium : 6
  Low    : 2
  Total risk signals identified: 13
```

**Deduplication logic:** Within each keyword scan, snippets are deduplicated by their
first 60 characters. This prevents the same paragraph from appearing multiple times
because two different keywords both matched in the same sentence.

**Why it's assigned to `risk_assessor` and not `financial_analyst`:** The analyst's
job is to find metrics and trends, not build a risk taxonomy. Giving the risk_assessor
its own scanning tool keeps domain responsibilities clean and gives Stage 3 an
independent, reproducible starting point.

**How the `risk_assessor` uses it:**

```
1. Call Create Risk Assessment(file_path) → get preliminary risk inventory
2. Read analyst context (Stage 2) for any additional risk flags noted there
3. Apply expert judgement: validate/refine each severity rating
4. Add risks the keyword scan missed (e.g., regulatory risks phrased unusually)
5. Build the full risk matrix with evidence citations
6. Model three scenarios (base/downside/stress) using quantified document data
```

### `FinancialDocumentTool` class

```python
class FinancialDocumentTool:
    read_data_tool = read_financial_document
```
A compatibility shim preserving the old import namespace. Some import sites reference
`FinancialDocumentTool.read_data_tool`. Rather than changing every reference, this
class exposes the same `@tool` function under the legacy name. No instantiation needed
— `read_data_tool` is a class attribute accessed as `FinancialDocumentTool.read_data_tool`.

---

## 4. agents.py — Line by Line

```python
from dotenv import load_dotenv
load_dotenv()
```
Called again here even though `tools.py` already calls it. Safe — `load_dotenv()` is
idempotent. The reason: `agents.py` may be imported in test scripts that never touch
`tools.py`. Every module that reads env vars calls `load_dotenv()` defensively at the
top.

```python
from crewai import Agent, LLM
```
`Agent` is the AI persona class. `LLM` is CrewAI's unified wrapper over LLM providers
(OpenAI, Google, Anthropic, etc.) — configure it once, pass it to all four agents.

### LLM Initialisation

```python
_openai_key = os.getenv("OPENAI_API_KEY")
_google_key = os.getenv("GOOGLE_API_KEY")

if _openai_key:
    llm = LLM(model=os.getenv("LLM_MODEL", "gpt-4o-mini"), api_key=_openai_key)
elif _google_key:
    llm = LLM(model=os.getenv("LLM_MODEL", "gemini/gemini-1.5-flash"), api_key=_google_key)
else:
    raise EnvironmentError("No LLM API key found...")
```
OpenAI is tried first. Google Gemini is the fallback. If neither key is set, the server
refuses to start immediately with a clear, actionable error message — "fail fast" is
far easier to debug than a cryptic LLM API error 30 seconds into an analysis run.

All four agents share the same `llm` object. One LLM configuration, four different
personas built on top of it.

---

### Agent 1: `verifier`

```python
verifier = Agent(
    role="Financial Document Compliance Verifier",
    goal=("Verify that uploaded documents are genuine financial reports..."),
    memory=True,
    tools=[read_financial_document],   # no search_tool — doc-only
    max_iter=3,
    max_rpm=10,
    allow_delegation=False,
)
```

**`tools=[read_financial_document]` only** — no web search. Verification must be based
solely on the document's content. Allowing web search would let the verifier make
assumptions ("this looks like Tesla based on search results") rather than reading the
actual document.

**`max_iter=3`** — verification is simpler than full analysis. Three iterations suffices:
(1) read the document, (2) assess it, (3) write the verdict.

**`memory=True`** — the verifier's observations (company name, reporting period, quality
flags) are retained in memory during its own task. Its output is then injected as context
into all downstream tasks via the `context=` chain.

---

### Agent 2: `financial_analyst`

```python
financial_analyst = Agent(
    role="Senior Financial Analyst",
    goal=("Provide accurate, evidence-based financial analysis...{query}..."),
    memory=True,
    tools=[read_financial_document, search_tool],  # only agent with web search
    max_iter=5,
    max_rpm=10,
    allow_delegation=False,
)
```

**`{query}` in the goal** — at runtime, CrewAI's template engine replaces `{query}` with
the actual value from `crew.kickoff(inputs={"query": ...})`. This keeps the analyst
laser-focused on what the user specifically asked.

**`tools=[read_financial_document, search_tool]`** — the analyst is the only agent with
`search_tool`. Market context, competitor benchmarks, and analyst consensus estimates
are the analyst's domain — not the risk_assessor's or investment_advisor's.

**`max_iter=5`** — the highest of all agents. Five iterations allows: (1) read prior
context, (2) read PDF, (3) web search for context, (4) synthesise, (5) structure output.

---

### Agent 3: `risk_assessor`

```python
risk_assessor = Agent(
    role="Quantitative Risk Assessment Specialist",
    goal=("Deliver a calibrated, data-driven risk assessment based on the verified "
          "financial document and the financial_analyst's core analysis..."),
    memory=True,    # NEW: needs to retain analyst context across iterations
    backstory=("...You build on the work of the financial analyst before you — "
               "you never start from scratch when prior analysis is available..."),
    tools=[read_financial_document, create_risk_assessment],  # ← create_risk_assessment added
    max_iter=4,     # raised: reads doc + processes two prior context blocks
    max_rpm=10,
    allow_delegation=False,
)
```

**`tools=[read_financial_document, create_risk_assessment]`** — two tools now. The
`create_risk_assessment` tool provides a deterministic keyword scan as a starting
inventory. The `risk_assessor` is instructed to call it first (on the PDF text),
use the inventory as its starting point, then deepen each flagged risk with expert
judgement and document evidence. This is division of labour: the tool finds candidates,
the agent validates and deepens them.

---

### Agent 4: `investment_advisor`

```python
investment_advisor = Agent(
    role="Chartered Investment Advisor",
    goal=("Translate the financial analysis and risk assessment into clear, balanced "
          "investment recommendations..."),
    memory=True,    # NEW: synthesises three prior outputs
    backstory=("...You synthesise inputs from compliance, financial analysis, and risk "
               "assessment teams before forming any recommendation. You are the last word "
               "in the pipeline — your output goes directly to the client."),
    tools=[read_financial_document, analyze_investment],  # ← analyze_investment added
    max_iter=4,     # raised: synthesises three context blocks
    max_rpm=10,
    allow_delegation=False,
)
```

**`tools=[read_financial_document, analyze_investment]`** — two tools now. The
`analyze_investment` tool provides deterministic metric extraction (revenue, EPS,
margins, FCF, growth rates, sentiment keywords) from the raw PDF text, giving the
investment_advisor a structured signal report to cross-verify against the analyst's
Stage 2 context before forming recommendations. If the tool finds a figure the analyst
missed, the advisor can incorporate it. If the tool and analyst agree, the advisor
can cite with higher confidence.

**`memory=True`** — the investment_advisor receives the outputs of all three prior
agents as context. With memory enabled, it can reference any specific figure or risk
from those outputs across multiple LLM calls within its own task.

**"You are the last word in the pipeline"** — tells the LLM it is the final stage.
This framing encourages synthesis and closure rather than leaving questions open.

**`max_iter=4`** — (1) read PDF + run analyze_investment tool, (2) cross-check signal
report against Stage 2 context, (3) draft per-profile recommendations, (4) add
disclosures and finalise.

---

## 5. task.py — Line by Line

### Import section

```python
from agents import financial_analyst, verifier, investment_advisor, risk_assessor
from tools import read_financial_document, search_tool
```
All four agents are now imported because all four are assigned to tasks. Previously only
`financial_analyst` and `verifier` were imported — `investment_advisor` and `risk_assessor`
were defined in `agents.py` but never reached `task.py`, meaning they were permanently
dead code.

### Pipeline order comment block

```python
# Step 1: verification_task      → verifier
# Step 2: document_analysis_task → financial_analyst  (context: step 1)
# Step 3: risk_assessment_task   → risk_assessor      (context: steps 1–2)
# Step 4: investment_analysis_task → investment_advisor (context: steps 1–3)
#
# NOTE: context= references are set after all Task objects are created,
# at the bottom of this file, to avoid forward-reference errors.
```
`context=` references point to other Task objects. If `document_analysis_task.context`
referenced `verification_task` inside the `Task(...)` constructor body, Python would
raise a `NameError` because `verification_task` is not yet defined at that point
(Python executes top-to-bottom). Setting `context` as attributes after all four objects
exist solves this entirely.

---

### Task 1: `verification_task` — no context (first in chain)

```python
verification_task = Task(
    description=(
        "You are the first agent in the pipeline. Read the file at '{file_path}' "
        "using the Read Financial Document tool..."
        "Your output will be passed as context to every downstream agent, so "
        "include the company name, document type, and reporting period explicitly."
    ),
    agent=verifier,
    tools=[read_financial_document],
    async_execution=False,
)
```

**"You are the first agent in the pipeline"** — explicitly tells the LLM it has no prior
context and must read the document cold. Without this, an LLM with `memory=True` might
act as if prior context from a different task exists.

**"Your output will be passed as context to every downstream agent"** — key prompt
engineering. Tells the verifier its output serves as shared knowledge for the whole
pipeline, so it must be structured and specific (company name, period, currency written
out explicitly, not implied), not just a binary pass/fail verdict.

**`agent=verifier`** — the verifier runs this task. This was correctly assigned in the
original code. It is the only correctly assigned task in the original.

---

### Task 2: `document_analysis_task` — context: [verification_task]

```python
document_analysis_task = Task(
    description=(
        "You are Step 2 in the pipeline. The verifier (Step 1) has already read "
        "and validated the document — review their findings in your context before "
        "proceeding. If the verifier marked the document NOT SUITABLE, state that "
        "analysis cannot proceed and explain why.\n\n"
        ...
        "4. Note any risks mentioned (the risk_assessor will handle these in depth "
        "   in the next step — just flag them here).\n"
        "...Your output will be used by the risk_assessor and "
        "investment_advisor in subsequent steps."
    ),
    agent=financial_analyst,
    tools=[read_financial_document, search_tool],
    # context wired below
)
```

**"Review their findings in your context before proceeding"** — instructs the analyst
to look at the injected verifier output before doing anything. Without this explicit
instruction, some LLMs ignore injected context blocks and jump straight to using tools.

**"If the verifier marked NOT SUITABLE, state that analysis cannot proceed"** — gives
the analyst a clear instruction for what to do when it finds a NOT SUITABLE verdict in
its context. The pipeline self-aborts at Stage 2 without crashing or requiring special
code in `main.py`.

**"Note any risks...the risk_assessor will handle these in depth in the next step"** —
division of labour. Without this, the analyst might produce a thorough risk section
itself, duplicating Stage 3's work and bloating the context that Stage 4 receives.

**`agent=financial_analyst`** — correctly assigned. This was correct in the original.

---

### Task 3: `risk_assessment_task` — context: [verification_task, document_analysis_task]

```python
risk_assessment_task = Task(
    description=(
        "You are Step 3 in the pipeline. The verifier (Step 1) confirmed the "
        "document's legitimacy and the financial_analyst (Step 2) completed the "
        "core financial analysis. Both outputs are in your context — read them "
        "carefully before using the Read Financial Document tool..."
        "...Your risk matrix will be consumed directly by the investment_advisor "
        "in the next step."
    ),
    agent=risk_assessor,    # ← THE CRITICAL FIX: was financial_analyst
    tools=[read_financial_document],
    # context wired below
)
```

**`agent=risk_assessor`** — the most important fix in the entire pipeline activation.
In the original broken code, `risk_assessment_task` had `agent=financial_analyst`,
meaning the financial analyst was doing the risk assessment too, `risk_assessor` was
never used anywhere, and the specialisation was meaningless.

**"Both outputs are in your context — read them carefully before using the tool"** —
explicit instruction to consult prior context before reaching for the PDF tool.
Without this, agents often ignore injected context and call `read_financial_document`
immediately, wasting iterations and potentially producing duplicate findings.

**"Your risk matrix will be consumed directly by the investment_advisor"** — tells the
risk_assessor to format its output as a clean, structured matrix (as specified in
`expected_output`) so Stage 4 can parse and cross-reference it easily.

---

### Task 4: `investment_analysis_task` — context: [verification_task, document_analysis_task, risk_assessment_task]

```python
investment_analysis_task = Task(
    description=(
        "You are Step 4 and the final agent in the pipeline. You have full context "
        "from all three prior steps:\n"
        "  • Step 1 (verifier): document legitimacy and metadata\n"
        "  • Step 2 (financial_analyst): core financial analysis and key metrics\n"
        "  • Step 3 (risk_assessor): risk matrix with severity ratings\n\n"
        ...
        "2. Incorporate the risk_assessor's risk matrix: every recommendation must "
        "   acknowledge the relevant risks identified in Step 3.\n"
        ...
        "You are a fiduciary — client wellbeing and regulatory compliance come first."
    ),
    agent=investment_advisor,   # ← THE CRITICAL FIX: was financial_analyst
    tools=[read_financial_document],
    # context wired below
)
```

**`agent=investment_advisor`** — the second critical fix. In the original, this was
`agent=financial_analyst`, meaning one agent was running all four tasks. The actual
`investment_advisor` specialist was never executed.

**Bullet-point context summary** — the description explicitly lists what each prior
stage provided. When an LLM receives a long context block, explicitly naming what each
section contains helps it locate and use relevant information rather than treating the
whole context as an undifferentiated blob of text.

**"Every recommendation must acknowledge the relevant risks identified in Step 3"** —
forces cross-stage integration. Without this, the advisor might produce recommendations
that contradict or silently ignore the risk matrix.

**"You are a fiduciary"** — ethical and regulatory framing before any recommendations
are formed. Sets the operating mode as client-first, compliance-mandatory.

---

### Context wiring — the most important lines in the file

```python
document_analysis_task.context   = [verification_task]
risk_assessment_task.context     = [verification_task, document_analysis_task]
investment_analysis_task.context = [verification_task, document_analysis_task, risk_assessment_task]
```

This is where the pipeline is physically connected. Three attribute assignments wire up
the entire context chain. Set after all Task objects are defined to avoid forward-reference
`NameError`.

**What CrewAI does with `context`:** When it is time to run `risk_assessment_task`,
CrewAI takes the output strings of `verification_task` and `document_analysis_task`,
formats them as named context blocks, and prepends them to the `risk_assessor`'s
prompt. The risk_assessor sees the full prior work as if it had produced it itself.

**`verification_task` has no `context` set** — it is the first stage. It reads the PDF
cold with no prior agent output to reference.

---

## 7. database.py — Line by Line

```python
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./financial_analyzer.db")
```
Reads the connection string from env. Defaults to a local SQLite file in the working
directory. The `./` in `sqlite:///./` means "relative to wherever the process is run
from". Swap to `postgresql://user:pass@host:5432/db` for production with no other code
changes required.

```python
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
```
SQLite's default threading restriction prevents a connection created in one thread from
being used in another. FastAPI's async thread pool violates this. `check_same_thread:
False` disables the restriction for SQLite only — PostgreSQL and MySQL handle
multi-threading natively and need an empty `connect_args`.

```python
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor.execute("PRAGMA journal_mode=WAL")
```
WAL (Write-Ahead Logging) mode allows reads and writes to the SQLite file concurrently.
Without it, the Celery worker writing a completed result would block the FastAPI server
from serving `GET /jobs/{id}` read requests simultaneously. The `@event.listens_for`
hook fires automatically on every new database connection.

```python
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```
`autocommit=False` — you must call `db.commit()` explicitly. Correct for web apps
where each request should be a single, explicit transaction.
`autoflush=False` — SQLAlchemy won't auto-flush pending changes before queries. Prevents
unexpected partial writes.

### `AnalysisJob` model

Tracks every analysis request through its lifecycle: `pending → running → completed`
or `pending → running → failed`.

`id` — UUID stored as 36-char string. Generated in application code before the DB write,
so the same UUID can be used for the temp filename, the DB record, and the Celery task
ID simultaneously — making cross-system tracing trivial.

`status` — SQLAlchemy `Enum` column. The database itself enforces valid values at the
storage level. You cannot accidentally insert `status="done"`.

`result` — `Text` (unbounded length) not `String` (has a defined max length). A
4-stage pipeline output combining all four agent reports can easily exceed 10,000
characters.

`created_at` / `updated_at` — `default=datetime.utcnow` is the **function reference**,
not the result of calling it. SQLAlchemy calls it at insert time, not at module load time.
`onupdate=datetime.utcnow` auto-updates the timestamp on any field change.

### `get_db()` dependency generator

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```
Used as `db: Session = Depends(get_db)` in FastAPI route handlers. The `yield` pauses
execution and gives `db` to the handler. When the request finishes (success or exception),
FastAPI resumes the generator into `finally`, closing the connection and returning it to
the pool. Cleanup is guaranteed even if the handler raises.

---

## 8. worker.py — Line by Line

### Celery configuration

```python
celery_app = Celery("financial_analyzer", broker=REDIS_URL, backend=REDIS_URL)
```
`broker` — where task messages are sent (Redis acts as a queue). The API pushes to this.
`backend` — where Celery stores completed task results. Same Redis instance here for
simplicity; in production you might use a dedicated Redis DB or a relational database
backend.

```python
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_max_retries=3,
    result_expires=86400,
)
```
`task_acks_late=True` — by default, Celery removes a task from the queue the moment a
worker picks it up. If the worker crashes mid-execution, the task is gone. `acks_late`
keeps it in the queue until the worker explicitly acknowledges successful completion.

`task_reject_on_worker_lost=True` — if the worker process dies, the task is re-queued
automatically for another worker.

`result_expires=86400` — deletes Celery's copy of results from Redis after 24 hours.
Our database copy persists indefinitely and is the authoritative store.

### `_update_job()` helper

```python
def _update_job(db: Session, job_id: str, **kwargs) -> None:
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if job:
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow()
        db.commit()
```
Avoids repeating the query-mutate-commit pattern at each of the worker's status
transitions (running → completed / failed). `**kwargs` accepts any field name-value pair
and applies them via `setattr`. `datetime.utcnow()` is set explicitly because the
Celery worker's `setattr` approach may not trigger SQLAlchemy's ORM-level `onupdate` hook.

### `analyze_document_task` — Celery task

```python
@celery_app.task(bind=True, name="analyze_document")
def analyze_document_task(self, job_id: str, query: str, file_path: str) -> dict:
```
`bind=True` — makes `self` refer to the Celery task instance, required for `self.retry()`.
`name="analyze_document"` — stable human-readable name in Redis. Without this, Celery
uses the full module path, which breaks if the module is renamed.

```python
    from crewai import Crew, Process
    from agents import financial_analyst, verifier, risk_assessor, investment_advisor
    from task import (
        verification_task, document_analysis_task,
        risk_assessment_task, investment_analysis_task,
    )
```
All four agents and all four tasks imported inside the function body. This avoids
circular imports at module load time — the `worker` module can be imported without
triggering the entire CrewAI + LLM initialisation chain. These imports only execute
when a task is actually being processed by a worker.

```python
    crew = Crew(
        agents=[verifier, financial_analyst, risk_assessor, investment_advisor],
        tasks=[
            verification_task,
            document_analysis_task,
            risk_assessment_task,
            investment_analysis_task,
        ],
        process=Process.sequential,
        verbose=False,   # worker logs go to Celery log files, not stdout
    )
```
Identical to `run_crew_sync()` in `main.py`. `verbose=False` in the worker because
output goes to Celery worker logs, not the interactive terminal — verbose output here
would produce noise in background log files.

```python
    except Exception as exc:
        _update_job(db, job_id, status="failed", error_message=str(exc))
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
```
`2 ** self.request.retries` is exponential back-off: 1s → 2s → 4s between retries.
After 3 failures, `MaxRetriesExceededError` is raised, Celery marks the task permanently
failed, and the database already has `status="failed"` from the `_update_job` call.

---

## 9. main.py — Line by Line

### Queue detection at startup

```python
_USE_QUEUE = False
try:
    from worker import analyze_document_task, celery_app
    celery_app.control.inspect(timeout=1).ping()
    _USE_QUEUE = True
except Exception:
    _USE_QUEUE = False
```
Runs once at server startup. Tries to import Celery, then pings Redis with a 1-second
timeout. If either fails (libraries missing, Redis not running, wrong URL), `_USE_QUEUE`
stays `False` and all requests use synchronous in-process execution. No code changes
needed to switch modes — only infrastructure changes (start/stop Redis and the worker).

### `run_crew_sync()` — the full pipeline

```python
def run_crew_sync(query: str, file_path: str) -> str:
    from agents import financial_analyst, verifier, risk_assessor, investment_advisor
    from task import (
        verification_task, document_analysis_task,
        risk_assessment_task, investment_analysis_task,
    )
    crew = Crew(
        agents=[verifier, financial_analyst, risk_assessor, investment_advisor],
        tasks=[
            verification_task,        # Stage 1
            document_analysis_task,   # Stage 2
            risk_assessment_task,     # Stage 3
            investment_analysis_task, # Stage 4
        ],
        process=Process.sequential,
        verbose=True,
    )
    result = crew.kickoff(inputs={"query": query, "file_path": file_path})
    return str(result)
```
`Process.sequential` — tasks execute in the list order, one at a time. Each task's
output is available as context to subsequent tasks (via the `Task.context` chain we
wired in `task.py`).

`crew.kickoff(inputs={"query": query, "file_path": file_path})` — resolves all
`{query}` and `{file_path}` template tokens across all four task descriptions and all
four agent goals/backstories simultaneously, before any agent runs.

`str(result)` — `result` is a `CrewOutput` object. `str()` gives Stage 4's output text
— the investment recommendations — which is the final synthesised answer returned to
the client.

### `POST /analyze` endpoint

```python
async def analyze_financial_document(
    file: UploadFile = File(...),
    query: str = Form(default="Analyze this financial document and provide investment insights"),
    db: Session = Depends(get_db),
):
```
Three parameters injected by FastAPI:
- `file` — required multipart PDF upload
- `query` — optional form field with a useful default
- `db` — database session from the `get_db()` dependency

```python
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
```
Extension check before any disk I/O. Fast and cheap. Not foolproof (a `.jpg` renamed
to `.pdf` passes), but catches the most common accidental wrong-file uploads immediately.

```python
    job_id = str(uuid.uuid4())
    file_path = f"data/financial_document_{job_id}.pdf"
```
UUID in the filename guarantees no two concurrent uploads collide even if both have
the same original filename. The same UUID is used for the database record's `id`, the
temp file name, and the Celery task ID — making it trivial to trace one request across
all three systems.

```python
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    with open(file_path, "wb") as f:
        f.write(content)
```
`await file.read()` reads the entire upload into memory as bytes.
`"wb"` (write binary) — PDFs are binary files; opening in text mode would corrupt them.
The empty-file check happens after reading but before writing, so no empty file is ever
written to disk.

```python
        if _USE_QUEUE:
            analyze_document_task.apply_async(
                kwargs={"job_id": job_id, "query": query, "file_path": file_path},
                task_id=job_id,
            )
            return JSONResponse(status_code=202, content={...})
        else:
            job.status = "running"
            db.commit()
            analysis = run_crew_sync(query=query, file_path=file_path)
            job.status = "completed"
            job.result = json.dumps({"analysis": analysis})
            db.commit()
            return {"status": "success", ..., "analysis": analysis}
```
Two execution paths in one endpoint. `apply_async` dispatches to Redis without waiting
(fast). `run_crew_sync` blocks the request thread until all 4 stages complete (slow —
potentially several minutes for a large PDF).

```python
    except HTTPException:
        raise
    except Exception as e:
        job_record = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job_record:
            job_record.status = "failed"
            job_record.error_message = str(e)
            db.commit()
        raise HTTPException(status_code=500, detail=f"Error processing: {str(e)}")
    finally:
        if not _USE_QUEUE and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
```
Two `except` clauses: the first re-raises `HTTPException` unchanged (so 400 validation
errors stay as 400). The second catches all unexpected errors, marks the job failed,
and wraps the error as 500.

`finally` cleans up the temp file only in sync mode. In async mode, the file must
remain on disk until the Celery worker reads it during Stage 1. The worker is responsible
for its own cleanup in its own `finally` block.

### `GET /jobs/{job_id}` — polling endpoint

```python
    if job.status == "completed" and job.result:
        response["analysis"] = json.loads(job.result).get("analysis", "")
    elif job.status == "failed":
        response["error"] = job.error_message
```
Progressive disclosure: `pending`/`running` responses are small (status + timestamps
only). `completed` responses include the full Stage 4 output text. `failed` responses
include the error message. Clients poll cheaply until the job transitions to a terminal
state.

---

---

## 10. pdf_gen.py — Line by Line

```python
class AnalysisPDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'Financial Analysis Report', 0, 1, 'C')
```
Custom FPDF class that adds a persistent header and footer (page numbers) to every
page of the generated report.

```python
def generate_pdf(analysis_text, output_path):
    # ... markdown parsing logic ...
```
This utility function converts the raw string output from the CrewAI agents into a
properly formatted PDF. It performs a basic "markdown-to-PDF" transformation:
- Lines starting with `#`, `##`, or `###` are converted into Bold, larger-sized headers.
- Lines starting with `*` or `-` are converted into bullet points using the `•` character.
- Regular text is wrapped into multi-line cells (`multi_cell`) to prevent overflow.

**Persistence**: Every call to `generate_pdf` ensures the parent directory (e.g., `outputs/`)
exists via `os.makedirs(..., exist_ok=True)` before writing the file.

---

## 11. Workflow Walkthroughs

### 11.1 Happy Path — Full Pipeline, Synchronous Mode

```
1.  Server starts → init_db() creates tables → Redis ping fails → _USE_QUEUE = False

2.  Client: POST /analyze
      file  = tesla_q2_2025.pdf
      query = "Is Tesla's cash position improving and is it a good buy?"

3.  main.py: validates .pdf extension ✓
4.  main.py: reads 3.2 MB PDF into memory
5.  main.py: saves → data/financial_document_abc-123.pdf
6.  main.py: INSERT AnalysisJob(id="abc-123", status="pending")
7.  main.py: UPDATE status="running"
8.  main.py: run_crew_sync(query, file_path) ← blocks here

━━━━━━━━━━━━━━━━ STAGE 1 — verifier ━━━━━━━━━━━━━━━━

9.  CrewAI starts verification_task. {file_path} and {query} resolved in prompt.
10. verifier calls read_financial_document("data/financial_document_abc-123.pdf")
11. PyPDFLoader reads PDF → ~80,000 characters returned to agent
12. verifier LLM identifies: Tesla, Inc. / Q2 2025 Earnings Release / USD
13. verifier produces output:
    "Company: Tesla, Inc.
     Document type: Q2 2025 Earnings Release
     Reporting period: April–June 2025 / Currency: USD
     Data quality: Pass — income statement, balance sheet, cash flow all present
     Verdict: SUITABLE FOR ANALYSIS
     Caveats: None material"

━━━━━━━━━━━━━━━━ STAGE 2 — financial_analyst ━━━━━━━━━━━━━━━━

14. CrewAI injects Stage 1 output as context into document_analysis_task
15. financial_analyst receives: [verifier output] + task description (both in prompt)
16. analyst reads "SUITABLE FOR ANALYSIS" in verifier context → proceeds
17. analyst calls read_financial_document(path) → gets financial figures
18. analyst optionally calls search_tool("Tesla Q2 2025 analyst price target")
19. analyst produces output:
    "Executive Summary: Tesla reported Q2 2025 revenue of $X.XB, up Y% YoY...
     Key Metrics: Revenue | $X.XB | +Y% YoY | Operating Margin | Z% | +N bps
     Cash position: $X.XB, improved Z% from Q1 2025, driven by FCF of $XB
     [answers user query directly: 'Cash improving — here is the evidence...']
     Risk flags for downstream: [1] EV demand softness QoQ, [2] Rising capex"

━━━━━━━━━━━━━━━━ STAGE 3 — risk_assessor ━━━━━━━━━━━━━━━━

20. CrewAI injects Stages 1+2 outputs as context into risk_assessment_task
21. risk_assessor reads prior context (verifier verdict + analyst's risk flags)
22. Iteration 1: calls read_financial_document(path) → gets full PDF text
23. Iteration 2: calls Create Risk Assessment(full_text) → deterministic keyword scan:
    "[MARKET RISK]
       ⚠ High   'tariff'  → ...shifting tariffs, unclear fiscal policy impacts...
       ⚠ Medium 'macro'   → ...uncertain macroeconomic environment...
     [LIQUIDITY RISK]
       ⚠ Medium 'Free cash flow' → ...FCF 146 -89%...
       ⚠ Medium 'Capital expenditure' → ...2,394...
     [OPERATIONAL RISK]
       ⚠ High   'Supply chain' → ...resilient vehicle capacity despite trade uncertainties...
       ⚠ High   'export control' → ...unfavorable regulatory...export controls...
     [SEVERITY SUMMARY] High:5  Medium:6  Low:2  Total:13"
24. Iteration 3: validates/refines each hit using expert judgement,
    adds analyst's EV demand flag (not in keyword scan), builds full matrix
25. risk_assessor produces output:
    "Risk Summary Matrix:
     | EV demand soft.   | Market    | High   | Revenue -16% YoY, deliveries -13% | Diversify to energy |
     | Tariff exposure   | Market    | High   | Document cites shifting tariffs    | Supply localisation |
     | FCF compression   | Liquidity | High   | FCF $146M -89% QoQ                | Monitor CapEx pace  |
     | Supply chain      | Operational| Med   | Explicitly flagged, managed        | Dual-sourcing       |
     Scenario Analysis: Base/Downside/Stress modelled on revenue and FCF data..."

━━━━━━━━━━━━━━━━ STAGE 4 — investment_advisor ━━━━━━━━━━━━━━━━

26. CrewAI injects Stages 1+2+3 outputs as context into investment_analysis_task
27. investment_advisor reads all three prior outputs
28. Iteration 1: calls read_financial_document(path) → full text
29. Iteration 2: calls Analyze Investment Data(full_text) → deterministic signal report:
    "[REVENUE SIGNALS]   Total revenues 22,496
     [EPS SIGNALS]       diluted GAAP 0.33 -18%  |  non-GAAP 0.40 -23%
     [MARGIN SIGNALS]    Gross margin 17.2% -71bp  |  Operating margin 4.1% -219bp
     [CASH]              Cash, equivalents 36,782
     [FREE CASH FLOW]    Free cash flow 146 -89%
     [GROWTH FLAGS]      -12% YoY revenue  |  -42% YoY operating income
     [SENTIMENT]         Positive: expansion, launch  |  Negative: tariff, uncertain, decline"
30. Iteration 3: cross-checks signal report vs Stage 2 context → confirms FCF and
    margin numbers, notes FCF compression as critical from signal report + risk matrix
31. Iteration 4: synthesises → produces final output:
    "Investment Thesis: Tesla's strong cash position ($36.8B) and Robotaxi launch
     mark a strategic inflection, but near-term FCF compression ($146M, -89% QoQ)
     and margin pressure (operating margin 4.1%, -219bp YoY) require caution...
    
     Key Metrics Snapshot (tool-verified):
     Revenue $22.5B | Op.Margin 4.1% | EPS $0.33 | FCF $0.15B | Cash $36.8B
    
     Recommendations:
     Conservative | Hold  | Await FCF recovery           | FCF -89% (Liquidity/High)
     Moderate     | Accum | Robotaxi expansion catalyst  | Tariff exposure (Market/High)
     Growth       | OW    | FSD monetisation optionality | EV demand soft (Market/High)
    
     Risk Acknowledgement: Per Stage 3 matrix — top risks: EV demand softness (High),
     tariff exposure (High), FCF compression (High)...
     Risk Disclosure: Based solely on Tesla Q2 2025 Earnings Update. Not personalised
     financial advice. Past performance is not indicative of future results..."

━━━━━━━━━━━━━━━━ Back in main.py ━━━━━━━━━━━━━━━━

27. run_crew_sync returns Stage 4 output as a string
28. main.py: UPDATE AnalysisJob status="completed", result=JSON
29. main.py: os.remove("data/financial_document_abc-123.pdf")
30. Client: HTTP 200
    {
      "status":         "success",
      "job_id":         "abc-123",
      "query":          "Is Tesla's cash position improving and is it a good buy?",
      "analysis":       "Investment Thesis: Tesla's improving cash...",
      "file_processed": "tesla_q2_2025.pdf"
    }
```
**Total time:** 3–10 minutes depending on PDF size, LLM speed, and whether `search_tool` fires.

---

### 11.2 Happy Path — Full Pipeline, Queue Mode

```
1.  Redis available → _USE_QUEUE = True
2.  Celery worker running in separate terminal

3.  Client: POST /analyze → same validation + file save + DB insert as above
4.  main.py: apply_async(kwargs={job_id, query, file_path}, task_id="abc-123")
    Message pushed to Redis queue.
5.  Client receives immediately: HTTP 202
    { "status": "queued", "job_id": "abc-123",
      "message": "Analysis queued. Poll GET /jobs/abc-123 for results." }

6.  Celery worker picks up message from Redis queue
7.  worker.py: runs the identical 4-stage pipeline (stages 9–26 above)
8.  worker.py: UPDATE status="completed", result=JSON
9.  worker.py: os.remove(file_path)

10. Client polls GET /jobs/abc-123:
    → { "status": "running" }      (stages 1–3 in progress)
    → { "status": "running" }
    → { "status": "completed", "analysis": "Investment Thesis: ..." }
```
**API response time:** ~50ms. **Analysis latency:** same as sync, but non-blocking.
**Concurrency:** With `--concurrency=4`, 4 complete pipelines run in parallel.

---

### 11.3 How Context Flows Between Agents — Detailed

Here is exactly what CrewAI injects into Stage 3's prompt:

```
[System prompt: risk_assessor's role, goal, backstory]

--- Context from verification_task ---
Company: Tesla, Inc.
Document type: Q2 2025 Earnings Release
Reporting period: April–June 2025 / Currency: USD
Data quality: Pass — income statement, balance sheet, cash flow all present
Verdict: SUITABLE FOR ANALYSIS

--- Context from document_analysis_task ---
Executive Summary: Tesla reported Q2 2025 revenue of $X.XB, up Y% YoY...
Key Metrics: Revenue | $X.XB | +Y% YoY | Operating Margin | Z% | +N bps
Cash position: $X.XB, improved Z% from Q1 2025...
Risk flags: [1] EV demand softness QoQ, [2] Rising capex

[Task description: risk_assessment_task.description — resolves {file_path} and {query}]
"You are Step 3 in the pipeline. The verifier confirmed...
 Both outputs are in your context — read them carefully..."
```

The risk_assessor sees all of this before generating a single output token.
Stage 4 sees all of the above *plus* Stage 3's risk matrix in the same format.

This is why `memory=True` matters for Stages 3 and 4: as those agents iterate through
their own `max_iter` cycles, their `memory` retains all the injected context across LLM
calls within that task. Without `memory=True`, each iteration is a fresh LLM call and
the injected context might be partially lost between iterations.

---

### 11.4 Edge Case: Non-PDF File

```
Client: POST /analyze (file=report.docx)
→ file.filename.lower().endswith(".pdf") = False
→ HTTPException(400, "Only PDF files are supported.")
→ No file saved, no DB record created, no pipeline started
→ Client: HTTP 400
```

---

### 11.5 Edge Case: Empty File

```
Client: POST /analyze (file=empty.pdf, 0 bytes)
→ content = b""  →  len(content) == 0 → True
→ HTTPException(400, "Uploaded file is empty.")
→ No file written (check is before open())
→ No DB record (exception raised before INSERT)
→ Client: HTTP 400
```

---

### 11.6 Edge Case: Whitespace-Only Query

```
Client: POST /analyze (file=report.pdf, query="   ")
→ "   ".strip() == "" → True
→ query = "Analyze this financial document and provide investment insights"
→ Full 4-stage pipeline runs with the default query
→ Client: HTTP 200 with analysis using default query
```

---

### 11.7 Edge Case: Corrupt / Unreadable PDF

```
[File saved, Stage 1 begins]
verifier calls read_financial_document(path)
→ PyPDFLoader raises PdfReadError: "EOF marker not found"
→ CrewAI catches it and returns it as an observation:
  "Tool execution failed: PdfReadError: EOF marker not found"
→ verifier (max_iter=3) may retry; if all retries used, it produces output like:
  "Could not read the file at {file_path}. Error: PdfReadError.
   Verdict: NOT SUITABLE — file is unreadable."
→ Stages 2, 3, 4 receive this NOT SUITABLE context
→ Each follows its "if NOT SUITABLE, abort" instructions
→ Final output: "Analysis cannot proceed: the document could not be read."
→ main.py: UPDATE status="completed" (crew ran and produced a result)
→ Client: HTTP 200 with the explanation in "analysis" field
```

---

### 11.8 Edge Case: Non-Financial Document

```
[Grocery list uploaded as grocery.pdf]

Stage 1 — verifier:
→ reads: "Milk, eggs, bread, butter, coffee..."
→ outputs: "Verdict: NOT SUITABLE — document appears to be a grocery/shopping list,
  not a financial report. No financial statements, metrics, or disclosures found."

Stage 2 — financial_analyst:
→ sees NOT SUITABLE in context
→ description says "If verifier marked NOT SUITABLE, state analysis cannot proceed"
→ outputs: "Analysis cannot proceed. The document is a grocery list."

Stages 3 and 4:
→ both receive the aborted-analysis context
→ both produce short appropriate outputs acknowledging the abort

Final result: clean professional explanation to the user.
No special code needed — handled entirely through prompt design and context chain.
```

---

### 11.9 Edge Case: Verifier Returns NOT SUITABLE — Pipeline Self-Governs

This is a key pipeline design feature. The `verifier`'s NOT SUITABLE verdict propagates
downstream through the context chain automatically:

1. Verifier outputs "NOT SUITABLE" → injected into Stage 2's context
2. Stage 2's description says "if NOT SUITABLE, state analysis cannot proceed"
3. Analyst outputs a short abort message → injected into Stage 3's context
4. Stage 3 sees an aborted analysis and mirrors it
5. Stage 4 synthesises an appropriate "this document is not suitable" final message

No `if/else` logic in `main.py` needed. No code changes needed for different document
types. The LLM instructions handle it.

---

### 11.10 Edge Case: LLM API Key Missing

```
Server starts → agents.py imported →
_openai_key = None, _google_key = None →
raise EnvironmentError("No LLM API key found. Please set OPENAI_API_KEY or GOOGLE_API_KEY")
Server fails to start. All requests fail before any handler runs.
```

---

### 11.11 Edge Case: LLM Rate Limit During Pipeline

```
[Stage 2, financial_analyst, LLM API returns 429 Too Many Requests]

Sync mode:
→ LiteLLM (inside CrewAI) retries with exponential back-off automatically
→ If retry succeeds: pipeline continues
→ If max LiteLLM retries exceeded: exception → main.py → HTTP 500
   DB: status="failed", error_message="RateLimitError"

Queue mode:
→ Same LiteLLM retry happens first
→ If still failing: exception → Celery retries entire crew from Stage 1
   (countdown 1s → 2s → 4s)
→ After 3 Celery retries: permanent failure
→ DB: status="failed" / Client polling: { "status": "failed", "error": "..." }
```

---

### 11.12 Edge Case: Worker Crashes Mid-Pipeline (Stage 3)

```
Worker processing Stage 3 → machine loses power

→ task_acks_late=True: task acknowledgement never sent
→ task_reject_on_worker_lost=True: Celery detects heartbeat loss → re-queues
→ New worker picks up task → runs all 4 stages from Stage 1 again
→ DB: status "running" → "running" (new worker) → "completed"

WARNING: The temp file must still exist for Stage 1 to re-read.
If the file was on the crashed machine's local disk and that disk is gone,
Stage 1 fails with "File not found."

MITIGATION: Store uploaded files on shared persistent storage (S3, NFS, EFS) so any
worker can access them regardless of which machine handled the upload.
```

---

### 11.13 Edge Case: Redis Goes Down After API Starts

```
[_USE_QUEUE = True at startup; Redis crashes 20 minutes later]
Client: POST /analyze
→ _USE_QUEUE is still True (checked only at startup)
→ apply_async() → redis.ConnectionError
→ except block: UPDATE status="failed"
→ Client: HTTP 500

The API does NOT auto-fall-back to sync mode.
FIX: Restart the API server after Redis recovers — _USE_QUEUE will be re-evaluated.
```

---

### 11.14 Edge Case: Database Write Fails

```
main.py: db.commit() → SQLAlchemyError: disk I/O error
→ Propagates to except block
→ Except block tries UPDATE status="failed" → also fails (same disk issue)
→ Inner failure caught silently by `if job_record:` guard
→ HTTPException(500) returned to client
→ No DB record for this job
→ File cleanup in finally block (may fail too if disk is full — ignored)
→ Client: HTTP 500
```

---

### 11.15 Edge Case: Polling a Non-Existent Job ID

```
Client: GET /jobs/does-not-exist
→ db.query(...).first() → None
→ HTTPException(404, "Job 'does-not-exist' not found.")
→ Client: HTTP 404

Common cause: job ID from a previous server instance after SQLite DB was deleted.
```

---

### 11.16 Edge Case: Concurrent Uploads of the Same File

```
Client A: POST /analyze (file=tesla.pdf) → job_id="aaa"
Client B: POST /analyze (file=tesla.pdf) → job_id="bbb"  [simultaneous]

Saved as:  data/financial_document_aaa.pdf
           data/financial_document_bbb.pdf

No collision — UUID-named files are unique.

Sync mode: Both requests are handled by FastAPI async workers concurrently.
Each calls run_crew_sync(), blocking its own thread. Two separate crew instances.

Queue mode: Both dispatched to Redis immediately. Two separate Celery workers
(with --concurrency=4) handle them in parallel. Four workers total could handle
four concurrent full pipelines.
```

---

### 11.17 Edge Case: Very Large PDF (300+ pages)

```
[300-page annual report → ~600,000 tokens]

Stage 1: verifier calls read_financial_document()
→ PyPDFLoader reads all 300 pages → ~3,000,000 characters
→ LLM raises ContextLengthExceeded (GPT-4o-mini: 128K token limit)
→ Verifier may produce partial output or error message
→ All downstream stages receive incomplete/error context

MITIGATION — add to read_financial_document():
    MAX_CHARS = 120_000
    if len(full_report) > MAX_CHARS:
        full_report = full_report[:MAX_CHARS] + "\n[DOCUMENT TRUNCATED]"

PRODUCTION SOLUTION: Use RAG (Retrieval-Augmented Generation):
  - Index all pages in a vector store (e.g., ChromaDB, Pinecone)
  - Each agent queries for only the pages relevant to its task:
    verifier → "cover page, document header"
    analyst  → "income statement, cash flow statement"
    risk_assessor → "risk factors section"
    advisor  → "management guidance, outlook"
```

---

## 12. Complete Data Flow Diagram

```
POST /analyze (file, query)
        │
        ├─ invalid extension? → HTTP 400
        ├─ empty file?        → HTTP 400
        │
        ▼
save data/financial_document_{uuid}.pdf
INSERT AnalysisJob(status="pending")
        │
        ├── Redis? YES → apply_async() → HTTP 202 → [Celery Worker picks up]
        └── Redis? NO  → UPDATE status="running"
                                │
                    ┌───────────▼────────────────────────────────────────────┐
                    │                 CrewAI Crew                             │
                    │            process=Process.sequential                   │
                    │                                                         │
                    │  ┌──────────────────────────────────────────────────┐  │
                    │  │  Stage 1 — verifier / verification_task          │  │
                    │  │  reads PDF → classifies → produces verdict       │  │
                    │  └───────────────────────┬──────────────────────────┘  │
                    │    (output injected as context → Stage 2)              │
                    │  ┌───────────────────────▼──────────────────────────┐  │
                    │  │  Stage 2 — financial_analyst / document_analysis │  │
                    │  │  reads context + PDF + optional web search       │  │
                    │  │  → produces metrics, trends, query answer        │  │
                    │  └───────────────────────┬──────────────────────────┘  │
                    │    (outputs injected as context → Stage 3)             │
                    │  ┌───────────────────────▼──────────────────────────┐  │
                    │  │  Stage 3 — risk_assessor / risk_assessment       │  │
                    │  │  reads context + PDF → risk matrix + scenarios   │  │
                    │  └───────────────────────┬──────────────────────────┘  │
                    │    (outputs injected as context → Stage 4)             │
                    │  ┌───────────────────────▼──────────────────────────┐  │
                    │  │  Stage 4 — investment_advisor / investment_anal  │  │
                    │  │  synthesises all context → per-profile recs      │  │
                    │  └───────────────────────┬──────────────────────────┘  │
                    └───────────────────────────┼─────────────────────────────┘
                                               │ Stage 4 output = final result
                            UPDATE AnalysisJob(status="completed", result=JSON)
                            DELETE temp file
                                               │
                    Sync: HTTP 200 { "analysis": "Investment Thesis: ..." }
                   Async: client polls GET /jobs/{id}
                          → running → running → completed + full analysis
```

---

## 13. Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | One of these two | — | OpenAI key. Tried first. |
| `GOOGLE_API_KEY` | One of these two | — | Google Gemini key. Used if OpenAI absent. |
| `LLM_MODEL` | No | `gpt-4o-mini` / `gemini-1.5-flash` | Overrides the model for all 4 agents |
| `SERPER_API_KEY` | No | — | Enables `search_tool` in Stage 2. Without it, web search calls will fail. |
| `DATABASE_URL` | No | `sqlite:///./financial_analyzer.db` | SQLAlchemy connection URL |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis for Celery queue. Falls back to sync if unreachable. |

### Running the full pipeline

```bash
# Minimum — sync mode, OpenAI:
OPENAI_API_KEY=sk-... uvicorn main:app --port 8000

# Full — queue mode, PostgreSQL, web search enabled:
DATABASE_URL=postgresql://user:pass@localhost/fin_db \
REDIS_URL=redis://localhost:6379/0 \
OPENAI_API_KEY=sk-... \
SERPER_API_KEY=... \
uvicorn main:app --port 8000

# In a second terminal — start Celery workers:
celery -A worker worker --loglevel=info --concurrency=4

# Test with the bundled TSLA Q2 2025 sample document:
curl -X POST http://localhost:8000/analyze \
  -F "file=@data/TSLA-Q2-2025-Update.pdf" \
  -F "query=Is Tesla financially healthy and should I invest in Q3 2025?"

# Or use the generic sample:
curl -X POST http://localhost:8000/analyze \
  -F "file=@data/sample.pdf" \
  -F "query=Is this company financially healthy and should I invest?"
```

---

## 14. Sample Test Document: Tesla Q2 2025 (`data/TSLA-Q2-2025-Update.pdf`)

The repo ships with Tesla's official Q2 2025 investor update as a ready-to-run test document. It is an ideal test case because it contains every data type the pipeline is designed to process: financial tables, management commentary, risk disclosures, and forward-looking statements.

### Document summary

| Field | Value |
|-------|-------|
| Company | Tesla, Inc. (NASDAQ: TSLA) |
| Document type | Quarterly Earnings Update (Investor Presentation) |
| Reporting period | Q2 2025 (April–June 2025) |
| Pages | 30 |
| Currency | USD (millions unless stated) |
| Verified by Stage 1 as | SUITABLE FOR ANALYSIS |

### Key financials the pipeline will extract

| Metric | Q2 2025 | YoY change |
|--------|---------|------------|
| Total revenue | $22.5B | -12% |
| Gross margin | 17.2% | -71 bp |
| Operating income | $0.9B | -42% |
| Operating margin | 4.1% | -219 bp |
| Net income (GAAP) | $1.17B | -16% |
| Diluted EPS (GAAP) | $0.33 | -18% |
| Adjusted EBITDA | $3.4B | -7% |
| Free cash flow | $146M | -89% |
| Cash & investments | $36.8B | +20% |
| Total deliveries | 384,122 vehicles | -13% |

### What each tool will find

**`analyze_investment` (Stage 4 — called by `investment_advisor`):**
- Revenue signals: matches `$22,496`, `$19,335`, `$22.5B` style patterns
- EPS signals: matches GAAP diluted `$0.33`, non-GAAP `$0.40`
- Margin signals: matches `operating margin 4.1%`, `GAAP gross margin 17.2%`, `EBITDA margin 15.1%`
- Cash signals: matches `$36,782` (cash & investments at 30-Jun-25)
- FCF signals: matches `Free cash flow 146` and the -89% YoY change
- Growth rate flags: captures `-12% YoY`, `-42% YoY`, `-89% YoY` throughout the tables
- Sentiment keywords — Positive: `record`, `launch`, `expansion`, `milestone` (Robotaxi launch, Megapack records); Negative: `tariff`, `uncertain`, `decline`, `lower`, `risk`

**`create_risk_assessment` (Stage 3 — called by `risk_assessor`):**

The TSLA document is rich in risk language. Expected scan results:

```
[MARKET RISK]
  ⚠ Severity: High
    Keyword : 'tariff'
    Context : ...uncertain macroeconomic environment resulting from shifting tariffs,
              unclear impacts from changes to fiscal policy...

  ⚠ Severity: High
    Keyword : 'trade policy'
    Context : ...shifting tariff, trade and fiscal policies globally...

[OPERATIONAL RISK]
  ⚠ Severity: High
    Keyword : 'supply chain'
    Context : ...supply chain robustness has enabled a resilient vehicle capacity
              base despite trade and policy uncertainties...

  ⚠ Severity: Medium
    Keyword : 'regulatory'
    Context : ...pending regulatory approval...

  ⚠ Severity: Medium
    Keyword : 'production ramp'
    Context : ...Production rates depend on a variety of factors, including
              equipment uptime, component supply, downtime related to factory upgrades...

[LIQUIDITY RISK]
  ⚠ Severity: Medium
    Keyword : 'capital expenditure'
    Context : ...Capital expenditures (2,394)... Free cash flow 146...

[SEVERITY SUMMARY]
  High   : 5
  Medium : 7
  Low    : 2
  Total risk signals identified: 14
```

The `risk_assessor` receives this inventory and then uses its own expert judgement to promote/demote severity ratings based on Tesla's $36.8B cash position, low leverage, and strong operating cash flow — contextual factors the keyword scan cannot see.

### Expected pipeline output structure

After all 4 stages complete, the API returns a result with this structure. **Note: A professional PDF report is also automatically saved to the `/outputs` directory upon completion.**

```json
{
  "status": "completed",
  "result": {
    "stage_1_verification": "SUITABLE FOR ANALYSIS. Tesla, Inc. Q2 2025 Earnings Update...",
    "stage_2_analysis": "Revenue $22.5B (-12% YoY). Operating margin 4.1%...",
    "stage_3_risk": "| Market Risk | High | Tariff exposure... | Diversify supply chain... |...",
    "stage_4_recommendations": "Conservative: Hold. Moderate: Accumulate on weakness. Growth: Buy..."
  }
}
```

### Test queries to try with this document

```bash
# Financial health overview
-F "query=Is Tesla financially healthy based on Q2 2025 results?"

# Specific metric deep-dive
-F "query=How has Tesla's free cash flow trended over the past 5 quarters?"

# Risk-focused
-F "query=What are the biggest risks to Tesla's profitability in H2 2025?"

# Investment decision
-F "query=Should a conservative investor add Tesla to their portfolio based on Q2 2025?"

# Segment analysis
-F "query=How is Tesla's Energy business performing relative to Automotive?"
```

