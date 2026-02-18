## Importing libraries and files
import os
from dotenv import load_dotenv
load_dotenv()

# BUG FIX 1: "from crewai_tools import tools" was wrong/unused import.
# BUG FIX 2: SerperDevTool import path was correct but missing proper initialization guard.
from crewai_tools import SerperDevTool
from crewai.tools import tool

# BUG FIX 3: PyPDF (langchain loader) was never imported — code referenced bare `Pdf` class
#             which doesn't exist in scope, causing NameError at runtime.
from langchain_community.document_loaders import PyPDFLoader

## Creating search tool
search_tool = SerperDevTool()


# BUG FIX 4: read_data_tool was defined as an async method inside a class with no @tool decorator
#             and no `self` parameter, making it impossible for CrewAI to discover or invoke it.
#             Fixed by: (a) making it a standalone @tool-decorated function, (b) making it sync
#             (CrewAI tool executor is synchronous), (c) adding proper type hints.
# Internal logic functions to allow cross-tool calls without correctly invoking 'Tool' objects
def _read_financial_document(path: str = "data/sample.pdf") -> str:
    if not os.path.exists(path):
        return f"Error: File not found at path '{path}'. Please verify the file was uploaded correctly."

    try:
        loader = PyPDFLoader(path)
        docs = loader.load()
    except Exception as e:
        return f"Error: Failed to load PDF at '{path}': {str(e)}"

    if not docs:
        return "Error: PDF appears to be empty or could not be parsed."

    full_report = ""
    for page in docs:
        content = page.page_content

        # Collapse excessive blank lines
        while "\n\n" in content:
            content = content.replace("\n\n", "\n")

        full_report += content.strip() + "\n"

    return full_report.strip()


@tool("Read Financial Document")
def read_financial_document(path: str = "data/sample.pdf") -> str:
    """Read and extract text content from a PDF financial document.

    Args:
        path: File system path to the PDF document.

    Returns:
        Full text content of the PDF, with normalised whitespace.
    """
    return _read_financial_document(path)


@tool("Analyze Investment Data")
def analyze_investment(path: str) -> str:
    """Extract and structure key investment signals from financial document text.

    Performs deterministic regex-based extraction of revenue figures, EPS values,
    margin percentages, cash positions, and YoY/QoQ growth rates. Returns a
    structured signal report.

    Args:
        path: File system path to the PDF document.

    Returns:
        Structured investment signal report with extracted metrics and growth flags.
    """
    import re

    financial_document_data = _read_financial_document(path)
    if financial_document_data.startswith("Error:"):
        return financial_document_data

    if len(financial_document_data) < 50:
        return "Error: Insufficient text provided for investment analysis (minimum 50 characters required)."

    text  = financial_document_data
    lines = ["=== INVESTMENT SIGNAL EXTRACTION ===\n"]

    # ── Revenue ──────────────────────────────────────────────────────────────
    # Matches: "$22.5B", "-$22,496", "22.5 billion", "revenue of $3.4B"
    rev_patterns = [
        r'(?:total\s+)?revenues?\s*[:\-]?\s*([\-]?\$?[\d,]+\.?\d*)\s*([BMK]|\bbillion\b|\bmillion\b)?',
        r'([\-]?\$?[\d,]+\.?\d*)\s*([BMK]|\bbillion\b|\bmillion\b)?\s*(?:in\s+)?(?:total\s+)?revenues?',
    ]
    rev_hits = []
    for pat in rev_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            snippet = text[max(0, m.start()-30):m.end()+30].replace("\n", " ").strip()
            rev_hits.append(f"  • {snippet}")
    if rev_hits:
        lines.append("[REVENUE SIGNALS]")
        lines.extend(rev_hits[:4])
    else:
        lines.append("[REVENUE SIGNALS]\n  • No revenue figures extracted.")

    # ── EPS ──────────────────────────────────────────────────────────────────
    eps_hits = []
    # Support negative EPS e.g. -$0.33 or ($0.33)
    for m in re.finditer(
        r'(?:EPS|earnings\s+per\s+share)[^\n]{0,60}?(\(?\-?\$?[\d.]+\)?)',
        text, re.IGNORECASE
    ):
        snippet = text[max(0, m.start()-20):m.end()+40].replace("\n", " ").strip()
        eps_hits.append(f"  • {snippet}")
    lines.append("\n[EPS SIGNALS]")
    lines.extend(eps_hits[:3]) if eps_hits else lines.append("  • No EPS figures extracted.")

    # ── Margins ───────────────────────────────────────────────────────────────
    margin_hits = []
    for m in re.finditer(
        r'(?:gross|operating|net|EBITDA)\s+margin[^\n]{0,50}?([\-]?[\d.]+)\s*%',
        text, re.IGNORECASE
    ):
        snippet = text[max(0, m.start()-10):m.end()+30].replace("\n", " ").strip()
        margin_hits.append(f"  • {snippet}")
    lines.append("\n[MARGIN SIGNALS]")
    lines.extend(margin_hits[:4]) if margin_hits else lines.append("  • No margin figures extracted.")

    # ── Cash & Liquidity ─────────────────────────────────────────────────────
    cash_hits = []
    for m in re.finditer(
        r'(?:cash(?:\s+and\s+(?:cash\s+equivalents?|investments?))?)'
        r'[^\n]{0,60}?\s*([\-]?\$?[\d,]+\.?\d*)\s*([BMK]|\bB\b|\bbillion\b|\bmillion\b)?',
        text, re.IGNORECASE
    ):
        snippet = text[max(0, m.start()-20):m.end()+40].replace("\n", " ").strip()
        cash_hits.append(f"  • {snippet}")
    lines.append("\n[CASH & LIQUIDITY SIGNALS]")
    lines.extend(cash_hits[:4]) if cash_hits else lines.append("  • No cash figures extracted.")

    # ── Free Cash Flow ────────────────────────────────────────────────────────
    fcf_hits = []
    for m in re.finditer(
        r'free\s+cash\s+flow[^\n]{0,60}?\s*([\-]?\$?[\d,\-]+\.?\d*)\s*([BMK]|\bbillion\b|\bmillion\b)?',
        text, re.IGNORECASE
    ):
        snippet = text[max(0, m.start()-10):m.end()+40].replace("\n", " ").strip()
        fcf_hits.append(f"  • {snippet}")
    lines.append("\n[FREE CASH FLOW SIGNALS]")
    lines.extend(fcf_hits[:3]) if fcf_hits else lines.append("  • No FCF figures extracted.")

    # ── YoY / QoQ Growth Flags ────────────────────────────────────────────────
    growth_hits = []
    for m in re.finditer(
        r'([\+\-]?\d+\.?\d*\s*%)[^\n]{0,60}?(?:YoY|QoQ|year.over.year|quarter.over.quarter)',
        text, re.IGNORECASE
    ):
        snippet = text[max(0, m.start()-20):m.end()+30].replace("\n", " ").strip()
        growth_hits.append(f"  • {snippet}")
    lines.append("\n[GROWTH RATE FLAGS]")
    lines.extend(growth_hits[:6]) if growth_hits else lines.append("  • No explicit YoY/QoQ rates extracted.")

    # ── Positive / Negative Sentiment Keywords ────────────────────────────────
    positive_kw = ["record", "growth", "increase", "improved", "expansion",
                   "launch", "milestone", "strong", "ahead", "acceleration"]
    negative_kw = ["decline", "decrease", "loss", "risk", "headwind",
                   "tariff", "uncertain", "lower", "shortage", "delay"]

    pos_found = [kw for kw in positive_kw if kw.lower() in text.lower()]
    neg_found = [kw for kw in negative_kw if kw.lower() in text.lower()]

    lines.append(f"\n[SENTIMENT KEYWORDS]")
    lines.append(f"  Positive indicators : {', '.join(pos_found) if pos_found else 'none detected'}")
    lines.append(f"  Negative indicators : {', '.join(neg_found) if neg_found else 'none detected'}")
    lines.append(f"\n[DOCUMENT STATS]")
    lines.append(f"  Word count : {len(text.split()):,}")
    lines.append(f"  Char count : {len(text):,}")
    lines.append("\n=== END INVESTMENT SIGNAL EXTRACTION ===")

    return "\n".join(lines)


@tool("Create Risk Assessment")
def create_risk_assessment(path: str) -> str:
    """Scan financial document text for risk-related disclosures and produce a structured risk inventory.

    Performs deterministic keyword and pattern matching across four risk categories.
    
    Args:
        path: File system path to the PDF document.

    Returns:
        Structured risk inventory with category, keyword match, contextual snippet,
        and preliminary severity signal.
    """
    import re

    financial_document_data = _read_financial_document(path)
    if financial_document_data.startswith("Error:"):
        return financial_document_data

    if len(financial_document_data) < 50:
        return "Error: Insufficient text provided for risk assessment (minimum 50 characters required)."

    text = financial_document_data

    # Risk categories: each entry is (keyword, preliminary_severity)
    RISK_CATEGORIES = {
        "MARKET RISK": [
            ("interest rate",      "Medium"),
            ("foreign exchange",   "Medium"),
            (r"\bFX\b",            "Medium"),
            ("inflation",          "Medium"),
            ("tariff",             "High"),
            ("commodity",          "Medium"),
            ("macro",              "Medium"),
            ("trade policy",       "High"),
            ("fiscal policy",      "Medium"),
            ("competition",        "Medium"),
            ("demand",             "Low"),
        ],
        "CREDIT RISK": [
            ("default",            "High"),
            ("counterparty",       "Medium"),
            ("credit risk",        "High"),
            ("debt covenant",      "High"),
            (r"accounts\s+receivable", "Low"),
            ("liquidity risk",     "High"),
            ("refinanc",           "Medium"),
            ("non-recourse debt",  "Medium"),
        ],
        "LIQUIDITY RISK": [
            ("cash runway",        "High"),
            ("working capital",    "Medium"),
            ("free cash flow",     "Medium"),
            ("capital expenditure","Medium"),
            (r"\bCapEx\b",         "Medium"),
            ("debt maturit",       "High"),
            ("financing activit",  "Low"),
            ("restricted cash",    "Low"),
        ],
        "OPERATIONAL RISK": [
            ("supply chain",       "High"),
            ("litigation",         "High"),
            ("regulatory",         "Medium"),
            ("key person",         "Medium"),
            ("cybersec",           "High"),
            ("factory",            "Low"),
            ("production ramp",    "Medium"),
            ("recall",             "High"),
            ("component supply",   "Medium"),
            ("tariff",             "High"),
            ("export control",     "High"),
        ],
    }

    lines = ["=== RISK INVENTORY SCAN ===\n"]
    any_found = False

    for category, keywords in RISK_CATEGORIES.items():
        hits = []
        seen_snippets = set()

        for kw_pattern, severity in keywords:
            for m in re.finditer(kw_pattern, text, re.IGNORECASE):
                # Grab a 120-char window around the match
                start = max(0, m.start() - 50)
                end   = min(len(text), m.end() + 70)
                snippet = " ".join(text[start:end].split())  # normalise whitespace

                # Deduplicate near-identical snippets
                key = snippet[:60]
                if key in seen_snippets:
                    continue
                seen_snippets.add(key)

                hits.append((severity, m.group(), f"...{snippet}..."))
                if len(hits) >= 3:   # max 3 hits per keyword group to stay concise
                    break

        if hits:
            any_found = True
            lines.append(f"[{category}]")
            for severity, keyword, snippet in hits:
                lines.append(f"  ⚠ Severity: {severity}")
                lines.append(f"    Keyword : '{keyword}'")
                lines.append(f"    Context : {snippet}")
            lines.append("")

    if not any_found:
        lines.append("No standard risk keywords detected in the provided text.")
        lines.append("The document may require manual review for non-standard risk language.")

    # ── Aggregate severity count ──────────────────────────────────────────────
    high_count   = sum(1 for line in lines if "Severity: High"   in line)
    medium_count = sum(1 for line in lines if "Severity: Medium" in line)
    low_count    = sum(1 for line in lines if "Severity: Low"    in line)

    lines.append("[SEVERITY SUMMARY]")
    lines.append(f"  High   : {high_count}")
    lines.append(f"  Medium : {medium_count}")
    lines.append(f"  Low    : {low_count}")
    lines.append(f"  Total risk signals identified: {high_count + medium_count + low_count}")
    lines.append("\nNOTE: Preliminary severity ratings based on keyword context only.")
    lines.append("The risk_assessor agent must apply expert judgement to validate and refine each rating.")
    lines.append("\n=== END RISK INVENTORY SCAN ===")

    return "\n".join(lines)


# Expose a FinancialDocumentTool namespace for backward-compatibility with agent imports
class FinancialDocumentTool:
    read_data_tool = read_financial_document
