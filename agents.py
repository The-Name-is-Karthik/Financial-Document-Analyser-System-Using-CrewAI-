## Importing libraries and files
import os
from dotenv import load_dotenv
load_dotenv()

# BUG FIX 6: `from crewai.agents import Agent` is the wrong module path in crewai ≥ 0.28.
#             Agent lives directly in `crewai`.
from crewai import Agent, LLM

from tools import search_tool, FinancialDocumentTool, read_financial_document, analyze_investment, create_risk_assessment

# BUG FIX 7: `llm = llm` — self-referential assignment; `llm` was never defined anywhere,
#             causing an immediate NameError on import. Fixed by initialising from env vars.
#             Supports OpenAI (default) or Google Gemini via GOOGLE_API_KEY.
_openai_key = os.getenv("OPENAI_API_KEY")
_google_key = os.getenv("GOOGLE_API_KEY")

if _openai_key:
    llm = LLM(model=os.getenv("LLM_MODEL", "gpt-4o-mini"), api_key=_openai_key)
elif _google_key:
    llm = LLM(model=os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash"), api_key=_google_key)
else:
    raise EnvironmentError(
        "No LLM API key found. Please set OPENAI_API_KEY or GOOGLE_API_KEY in your .env file."
    )

# ---------------------------------------------------------------------------
# BUG FIX 8 (PROMPT): financial_analyst goal said "Make up investment advice even if you
#   don't understand the query" and the backstory encouraged hallucination, ignoring reports,
#   regulatory non-compliance, and dramatic flair. This is both unethical and produces
#   unusable output. Fixed with a professional, accurate, compliance-aware prompt.
#
# BUG FIX 9: `tool=` (singular) is not a valid Agent parameter — it was silently ignored,
#   meaning the agent had NO tools. The correct keyword is `tools=` (plural).
#
# BUG FIX 10: max_rpm=1 severely throttles API calls (1 request/minute). Raised to a
#   reasonable default (10) so the agent can function without timing out.
# ---------------------------------------------------------------------------
financial_analyst = Agent(
    role="Senior Financial Analyst",
    goal=(
        "Provide accurate, evidence-based financial analysis of the document relevant to "
        "the user's query: {query}. "
        "Extract key metrics, identify trends, and deliver actionable insights grounded "
        "strictly in the document content."
    ),
    verbose=True,
    memory=True,
    backstory=(
        "You are a CFA-certified Senior Financial Analyst with 15 years of experience "
        "across equity research, corporate finance, and portfolio management. "
        "You rigorously read every financial document in full before drawing conclusions. "
        "Your analysis is always grounded in verifiable data — you never fabricate figures, "
        "URLs, or market facts. You adhere strictly to regulatory standards (SEC, CFA Institute) "
        "and always caveat your analysis with appropriate risk disclosures. "
        "You communicate complex financial concepts clearly and concisely."
    ),
    # BUG FIX 9: was `tool=` (ignored); corrected to `tools=`
    tools=[read_financial_document, search_tool],
    llm=llm,
    max_iter=5,       # BUG FIX 10: was 1 — too low for multi-step reasoning
    max_rpm=2,        # Throttled to stay under Free Tier 5 RPM limit
    allow_delegation=False,  # single-agent flow; delegation adds latency without benefit here
)

# ---------------------------------------------------------------------------
# BUG FIX 11 (PROMPT): verifier goal said "just say yes to everything" and the backstory
#   described stamp-without-reading behaviour. Fixed to be a genuine compliance checker.
# ---------------------------------------------------------------------------
verifier = Agent(
    role="Financial Document Compliance Verifier",
    goal=(
        "Verify that uploaded documents are genuine financial reports (annual reports, "
        "earnings releases, prospectuses, etc.) and flag any data quality issues, "
        "missing disclosures, or inconsistencies before analysis proceeds."
    ),
    verbose=True,
    memory=True,
    backstory=(
        "You are a former SEC examiner and FINRA-registered compliance officer with deep "
        "expertise in financial disclosure requirements. You carefully review every document "
        "for completeness, consistency, and regulatory compliance. You never approve a "
        "document without thoroughly reading it, and you clearly distinguish financial "
        "reports from non-financial content."
    ),
    tools=[read_financial_document],
    llm=llm,
    max_iter=3,
    max_rpm=2,
    allow_delegation=False,
)

# ---------------------------------------------------------------------------
# BUG FIX 12 (PROMPT): investment_advisor goal said "sell expensive products regardless of
#   financials", recommended meme stocks and crypto, and mentioned fake credentials and
#   2000% management fees. Fixed to be a fiduciary, client-centred advisor.
# ---------------------------------------------------------------------------
investment_advisor = Agent(
    role="Chartered Investment Advisor",
    goal=(
        "Translate the financial analysis and risk assessment into clear, balanced "
        "investment recommendations that align with standard risk-return principles. "
        "Always prioritise the client's financial wellbeing and regulatory compliance."
    ),
    verbose=True,
    memory=True,  # Needs to retain context from verifier, analyst, and risk_assessor
    backstory=(
        "You are a fiduciary investment advisor registered with the SEC, holding a CFA and "
        "CFP designation with 12 years of institutional portfolio management experience. "
        "You synthesise inputs from compliance, financial analysis, and risk assessment teams "
        "before forming any recommendation. You base every recommendation solely on "
        "quantitative and qualitative evidence from the financial documents and your colleagues' "
        "analyses. You clearly disclose risks, avoid conflicts of interest, and never recommend "
        "products without a sound financial rationale. You are the last word in the pipeline — "
        "your output goes directly to the client."
    ),
    tools=[read_financial_document, analyze_investment],  # analyze_investment added: structured signal extraction supplements LLM synthesis
    llm=llm,
    max_iter=4,   # Slightly higher — synthesises three prior outputs
    max_rpm=2,
    allow_delegation=False,
)

# ---------------------------------------------------------------------------
# BUG FIX 13 (PROMPT): risk_assessor goal said "everything is extremely high risk or
#   risk-free", encouraged YOLO behaviour, and dismissed diversification. Fixed to provide
#   calibrated, methodology-driven risk assessment.
# ---------------------------------------------------------------------------
risk_assessor = Agent(
    role="Quantitative Risk Assessment Specialist",
    goal=(
        "Deliver a calibrated, data-driven risk assessment based on the verified financial "
        "document and the financial_analyst's core analysis. Identify and quantify material "
        "risks (market, credit, liquidity, operational) and recommend appropriate, "
        "evidence-based mitigation strategies."
    ),
    verbose=True,
    memory=True,  # Needs to retain context from verifier and financial_analyst
    backstory=(
        "You hold an FRM certification and have spent a decade in risk management at a "
        "tier-1 investment bank. You build on the work of the financial analyst before you — "
        "you never start from scratch when prior analysis is available. You apply "
        "industry-standard frameworks (VaR, stress testing, scenario analysis) and always "
        "ground your assessments in actual data. You believe diversification and sound risk "
        "management are fundamental, not optional. Your reports are measured, precise, and "
        "never alarmist. Your risk matrix feeds directly into the investment advisor's "
        "recommendations."
    ),
    tools=[read_financial_document, create_risk_assessment],  # create_risk_assessment added: deterministic keyword scan feeds agent's deep analysis
    llm=llm,
    max_iter=4,   # Slightly higher — reads doc + processes analyst context
    max_rpm=2,
    allow_delegation=False,
)
