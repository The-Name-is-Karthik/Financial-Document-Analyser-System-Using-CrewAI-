## Importing libraries and files
from crewai import Task

# All four agents are now imported because all four are assigned to tasks.
# Previously investment_advisor and risk_assessor were defined in agents.py
# but never imported here, so they were never assigned to any task.
from agents import financial_analyst, verifier, investment_advisor, risk_assessor
from tools import read_financial_document, search_tool, analyze_investment, create_risk_assessment

# ---------------------------------------------------------------------------
# PIPELINE ARCHITECTURE
# Tasks are defined below in their full 'ADVISORY' configuration.
# The actual execution flow and context wiring are now managed DYNAMICALLY 
# by the 'router.py' module based on the user's query intent.
#
# Standard Full Pipeline (managed by router.py):
# Step 1: verification_task      → verifier
# Step 2: document_analysis_task → financial_analyst  (context: step 1)
# Step 3: risk_assessment_task   → risk_assessor      (context: steps 1–2)
# Step 4: investment_analysis_task → investment_advisor (context: steps 1–3)
#
# NOTE: The default `context=` wiring at the bottom of this file is maintained 
# for backward compatibility, but it is overridden in 'router.py' during 
# QUICK or AUDIT runs to prevent errors when upstream tasks are skipped.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# STEP 1 — Document Verification
# Agent : verifier
# Input : raw PDF at {file_path}
# Output: verification verdict (SUITABLE / NOT SUITABLE) + document metadata
# ---------------------------------------------------------------------------
verification_task = Task(
    description=(
        "You are the first agent in the pipeline. Read the file at '{file_path}' "
        "using the Read Financial Document tool and verify it is a legitimate "
        "financial document before any analysis proceeds.\n\n"
        "Your verification MUST:\n"
        "1. Confirm the file is readable and contains substantive text.\n"
        "2. Determine the document type (annual report, earnings release, "
        "   10-K/10-Q, prospectus, investor presentation, etc.).\n"
        "3. Identify the issuing company, reporting period, and reporting currency.\n"
        "4. Flag any data quality issues: missing sections, inconsistent figures, "
        "   redacted content, or incomplete disclosures.\n"
        "5. State clearly whether the document is SUITABLE FOR ANALYSIS or "
        "   NOT SUITABLE, with an explicit reason.\n\n"
        "If the document is NOT a financial report (e.g. a grocery list, legal "
        "contract, or image-only scan), state this clearly. Do NOT attempt to "
        "reinterpret non-financial content as financial data.\n\n"
        "Your output will be passed as context to every downstream agent, so "
        "include the company name, document type, and reporting period explicitly."
    ),
    expected_output=(
        "A concise verification report containing:\n"
        "- Company / Issuer name\n"
        "- Document type (e.g. Q2 2025 Earnings Release)\n"
        "- Reporting period and currency\n"
        "- Data quality assessment (Pass / Fail with specific notes)\n"
        "- Suitability verdict: SUITABLE FOR ANALYSIS or NOT SUITABLE (with reason)\n"
        "- Key caveats or limitations the downstream analysts should be aware of"
    ),
    agent=verifier,
    tools=[read_financial_document],
    async_execution=False,
)


# ---------------------------------------------------------------------------
# STEP 2 — Core Financial Analysis
# Agent : financial_analyst
# Input : {file_path}, {query}, + verification_task output (via context)
# Output: structured financial analysis answering the user's query
# ---------------------------------------------------------------------------
document_analysis_task = Task(
    description=(
        "You are Step 2 in the pipeline. The verifier (Step 1) has already read "
        "and validated the document — review their findings in your context before "
        "proceeding. If the verifier marked the document NOT SUITABLE, state that "
        "analysis cannot proceed and explain why.\n\n"
        "Assuming the document is suitable, read the financial document at "
        "'{file_path}' using the Read Financial Document tool and answer "
        "the user's specific query: {query}\n\n"
        "Your analysis MUST:\n"
        "1. Extract and summarise key financial metrics (revenue, gross/net profit, "
        "   EPS, operating margins, debt levels, free cash flow) with exact figures.\n"
        "2. Identify material trends (YoY and QoQ changes) supported by figures "
        "   from the document.\n"
        "3. Highlight significant opportunities disclosed in the report.\n"
        "4. Note any risks mentioned (the risk_assessor will handle these in depth "
        "   in the next step — just flag them here).\n"
        "5. Address the user's query: '{query}' directly and specifically.\n\n"
        "Do NOT fabricate figures, invent URLs, or make claims unsupported by the "
        "document. Your output will be used by the risk_assessor and "
        "investment_advisor in subsequent steps."
    ),
    expected_output=(
        "A structured financial analysis report containing:\n"
        "- Executive Summary (3–5 sentences referencing the verified company/period)\n"
        "- Key Financial Metrics table (Metric | Value | Prior Period | Change %)\n"
        "- Trend Analysis (2–4 paragraphs grounded in document figures)\n"
        "- Opportunities identified (bullet list with page/section references)\n"
        "- Risk flags for downstream assessment (brief bullet list)\n"
        "- Direct answer to the user's query: '{query}'\n"
        "All figures must be traceable to the source document."
    ),
    agent=financial_analyst,
    tools=[read_financial_document, search_tool],
    async_execution=False,
    # context is set below after all tasks are defined
)


# ---------------------------------------------------------------------------
# STEP 3 — Risk Assessment
# Agent : risk_assessor
# Input : {file_path}, {query}, + verification + analysis outputs (via context)
# Output: structured risk matrix with severity ratings and mitigations
# ---------------------------------------------------------------------------
risk_assessment_task = Task(
    description=(
        "You are Step 3 in the pipeline. The verifier (Step 1) confirmed the "
        "document's legitimacy and the financial_analyst (Step 2) completed the "
        "core financial analysis. Both outputs are in your context — read them "
        "carefully before using your tools.\n\n"
        "User query context: {query}\n\n"
        "RECOMMENDED TOOL SEQUENCE:\n"
        "1. Call the 'Create Risk Assessment' tool first, passing the '{file_path}' "
        "   string directly to the tool. This tool reads the file internally and "
        "   performs a deterministic keyword scan across market, credit, liquidity, "
        "   and operational risk categories with preliminary severity ratings — "
        "   use it as your starting inventory.\n"
        "2. Call 'Read Financial Document' directly to verify specific figures and "
        "   find contextual evidence for each risk the keyword scan flagged.\n"
        "3. Apply your expert judgement to validate, refine, and deepen each risk "
        "   the tool identified. Promote/demote severity ratings based on full context.\n"
        "4. Add any material risks the keyword scan missed that you identify through "
        "   direct reading.\n\n"
        "Your risk assessment MUST:\n"
        "1. Identify and categorise every material risk across four categories:\n"
        "   - Market risk (interest rates, FX, commodity prices, tariffs, macro)\n"
        "   - Credit risk (counterparty exposure, debt covenants, default probability)\n"
        "   - Liquidity risk (cash runway, working capital, debt maturity schedule)\n"
        "   - Operational risk (supply chain, regulatory, litigation, key-person)\n"
        "2. Support each identified risk with specific evidence quoted or cited "
        "   from the financial document (page/section where possible).\n"
        "3. Rate each risk's severity: High / Medium / Low, with written justification.\n"
        "4. Recommend a proportionate, evidence-based mitigation strategy per risk.\n"
        "5. Provide three scenario analyses: base case, downside, and stress case — "
        "   with quantified assumptions where the document provides data.\n\n"
        "Do NOT invent risk factors not evidenced by the document. Do NOT recommend "
        "speculative or extreme mitigation strategies. Your risk matrix will be "
        "consumed directly by the investment_advisor in the next step."
    ),
    expected_output=(
        "A structured risk assessment containing:\n"
        "- Risk Summary Matrix:\n"
        "  | Risk Name | Category | Severity | Evidence | Recommended Mitigation |\n"
        "- Detailed narrative for each High-severity risk (1–2 paragraphs each)\n"
        "- Scenario Analysis table (Metric | Base Case | Downside | Stress Case)\n"
        "- Portfolio / position-sizing guidance (conservative, moderate, growth)\n"
        "- Key monitoring metrics and trigger thresholds investors should watch\n"
        "All risks must be evidenced by the source document."
    ),
    agent=risk_assessor,
    tools=[read_financial_document, create_risk_assessment],
    async_execution=False,
    # context is set below after all tasks are defined
)


# ---------------------------------------------------------------------------
# STEP 4 — Investment Recommendations
# Agent : investment_advisor
# Input : {file_path}, {query}, + all three prior outputs (via context)
# Output: investor-profile-specific recommendations with full risk disclosure
# ---------------------------------------------------------------------------
investment_analysis_task = Task(
    description=(
        "You are Step 4 and the final agent in the pipeline. You have full context "
        "from all three prior steps:\n"
        "  • Step 1 (verifier): document legitimacy and metadata\n"
        "  • Step 2 (financial_analyst): core financial analysis and key metrics\n"
        "  • Step 3 (risk_assessor): risk matrix with severity ratings\n\n"
        "RECOMMENDED TOOL SEQUENCE:\n"
        "1. Call the 'Analyze Investment Data' tool first, passing the '{file_path}' "
        "   string directly to the tool. This tool reads the file internally and "
        "   extracts revenue figures, EPS, margins, cash position, FCF, growth rates, "
        "   and sentiment keywords — cross-check these against the analyst's context "
        "   to verify no metrics were missed.\n"
        "2. Use 'Read Financial Document' only if you need to verify a specific "
        "   figure not captured by the tool or the prior agents' context.\n"
        "3. Synthesise the tool output, the analyst's financial analysis (Step 2), "
        "   and the risk_assessor's risk matrix (Step 3) into your final recommendations.\n\n"
        "Using all of that context, develop balanced, fiduciary-grade investment "
        "recommendations in response to the user's query: {query}\n\n"
        "Your recommendations MUST:\n"
        "1. Open with an investment thesis grounded in the financial_analyst's findings.\n"
        "2. Incorporate the risk_assessor's risk matrix: every recommendation must "
        "   acknowledge the relevant risks identified in Step 3.\n"
        "3. Provide differentiated guidance for three investor profiles:\n"
        "   - Conservative (capital preservation, income focus)\n"
        "   - Moderate (balanced growth and income)\n"
        "   - Growth-oriented (higher risk tolerance, long-term horizon)\n"
        "4. Include valuation considerations if the document provides sufficient data "
        "   (P/E, EV/EBITDA, DCF inputs, or management guidance).\n"
        "5. Close with a mandatory regulatory risk disclosure statement.\n\n"
        "Do NOT recommend specific third-party securities not discussed in the document. "
        "Do NOT invent market data. Do NOT omit material risks identified in Step 3. "
        "You are a fiduciary — client wellbeing and regulatory compliance come first."
    ),
    expected_output=(
        "A structured investment recommendation report containing:\n"
        "- Investment Thesis (2–3 paragraphs synthesising Steps 1–3)\n"
        "- Key Metrics Snapshot (cross-verified from Analyze Investment Data tool output)\n"
        "- Recommendations by Investor Profile:\n"
        "    Conservative  | Action | Rationale | Key Risk\n"
        "    Moderate      | Action | Rationale | Key Risk\n"
        "    Growth        | Action | Rationale | Key Risk\n"
        "- Key Catalysts (positive and negative, with timeline estimates if available)\n"
        "- Valuation Snapshot (metrics from document, or note if insufficient data)\n"
        "- Risk Acknowledgement (cross-references Step 3 risk matrix explicitly)\n"
        "- Regulatory Disclosure Statement\n"
        "All claims must cite either the source document or the prior agents' outputs."
    ),
    agent=investment_advisor,
    tools=[read_financial_document, analyze_investment],
    async_execution=False,
    # context is set below after all tasks are defined
)


# ---------------------------------------------------------------------------
# CONTEXT WIRING
# Set after all Task objects exist to avoid forward-reference errors.
# Each task receives the outputs of all tasks that ran before it.
# CrewAI injects these as additional context blocks in the agent's prompt.
# ---------------------------------------------------------------------------
document_analysis_task.context  = [verification_task]
risk_assessment_task.context    = [verification_task, document_analysis_task]
investment_analysis_task.context = [verification_task, document_analysis_task, risk_assessment_task]
