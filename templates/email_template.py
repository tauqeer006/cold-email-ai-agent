from __future__ import annotations

SENDER_NAME = "Tauqeer Ahmad Qureshi"
SENDER_COMPANY = "Your Company"
SENDER_DESIGNATION = "Senior Software Engineer"
DEFAULT_SUBJECT = "Quick idea for {company_name}"
EMAIL_GREETING = "Hi {first_name},"
EMAIL_INTRODUCTION = "I noticed {company_name} is doing interesting work in your space, and I wanted to reach out with a quick idea."
EMAIL_VALUE_PROPOSITION = "We help teams like yours improve {company_name} outreach by making message quality and relevance more consistent without adding more manual work."
EMAIL_CALL_TO_ACTION = "Would you be open to a brief 10-minute conversation to explore whether this fits your current priorities?"
EMAIL_CLOSING = "Best,\n{sender_name}"


def render_email_template(first_name: str, company_name: str, sender_name: str = SENDER_NAME) -> str:
    greeting = EMAIL_GREETING.format(first_name=first_name or "there")
    intro = EMAIL_INTRODUCTION.format(company_name=company_name or "your team")
    value = EMAIL_VALUE_PROPOSITION.format(company_name=company_name or "your team")
    closing = EMAIL_CLOSING.format(sender_name=sender_name)
    return "\n\n".join([greeting, intro, value, EMAIL_CALL_TO_ACTION, closing])
