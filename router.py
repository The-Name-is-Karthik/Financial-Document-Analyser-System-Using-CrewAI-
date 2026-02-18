"""
router.py — Dynamic routing logic for the financial analysis pipeline.
"""
import os
from crewai import Crew, Process

def classify_intent(query: str) -> str:
    """Uses the LLM to classify query into QUICK, AUDIT, or ADVISORY."""
    from agents import llm
    
    prompt = f"""
    Classify this financial query into exactly one category: 
    - QUICK: Factual extraction, numbers, dates, or company names (e.g., 'What is the revenue?', 'Who is the CEO?').
    - AUDIT: Concern about safety, debt, risks, red flags, or sustainability.
    - ADVISORY: Investment recommendation, buying strategy, portfolio advice, or deep growth thesis.
    
    Query: {query}
    Category (Just the word):"""
    
    try:
        response = llm.call([{"role": "user", "content": prompt}])
        category = response.strip().upper()
        if category in ["QUICK", "AUDIT", "ADVISORY"]:
            return category
    except Exception:
        pass
    
    return "ADVISORY" # Default to full pipeline if uncertain


def get_active_crew(query: str) -> Crew:
    """Returns a CrewAI Crew with a dynamic subset of tasks based on query intent."""
    from agents import financial_analyst, verifier, risk_assessor, investment_advisor
    from task import (
        verification_task,
        document_analysis_task,
        risk_assessment_task,
        investment_analysis_task,
    )

    intent = classify_intent(query)
    
    # Step 1: Always verify document integrity
    active_tasks = [verification_task]
    active_agents = [verifier]
    
    # Reset context wiring for dynamic selection to avoid referencing skipped tasks
    document_analysis_task.context = [verification_task]
    risk_assessment_task.context = [verification_task]
    investment_analysis_task.context = [verification_task]

    if intent == "QUICK":
        active_tasks.append(document_analysis_task)
        active_agents.append(financial_analyst)
        
    elif intent == "AUDIT":
        active_tasks.append(risk_assessment_task)
        active_agents.append(risk_assessor)
        
    else:  # ADVISORY
        # Full pipeline
        active_tasks.append(document_analysis_task)
        active_tasks.append(risk_assessment_task)
        active_tasks.append(investment_analysis_task)
        
        active_agents.extend([financial_analyst, risk_assessor, investment_advisor])
        
        # Rewire full context
        risk_assessment_task.context = [verification_task, document_analysis_task]
        investment_analysis_task.context = [verification_task, document_analysis_task, risk_assessment_task]

    return Crew(
        agents=list(dict.fromkeys(active_agents)), # unique set of agents in order
        tasks=active_tasks,
        process=Process.sequential,
        verbose=True,
    )
