"""Generate personalized outreach drafts for each contact without one."""

from pydantic import BaseModel, Field
from rich.console import Console
from sqlmodel import Session, select

from gtm_agent import llm
from gtm_agent.config import ProductConfig, load_product_config
from gtm_agent.models import Contact, EmailDraft, Institution, Role, is_suppressed

console = Console()


class EmailContent(BaseModel):
    subject: str = Field(description="Short, specific subject line under 70 characters, no clickbait")
    body: str = Field(description="Plain-text email body, greeting through closing line, WITHOUT signature")


BASE_RULES = """You write short, warm, plain-text cold emails from a startup founder to people in academic hematology/oncology.
Rules:
- 90-150 words. No marketing fluff, no emojis, no bullet lists, no exclamation marks.
- Be specific and honest: this is an early pilot user study, not a sales pitch.
- Mention the study format, time commitment, incentive, and the scheduling link exactly as given.
- Use only facts provided. Never invent details about the recipient or institution.
- End with a simple closing line like "Thanks," on its own line. Do NOT include a signature, address, or opt-out text."""

ROLE_INSTRUCTIONS = {
    Role.program_director: (
        "The recipient is a fellowship program director. Respect their time. Ask whether they would be willing to "
        "share this study opportunity with their hem/onc fellows (e.g. forward to the fellows' list), and note that "
        "participation is voluntary and outside clinical duties. Address them as Dr. <Last name>."
    ),
    Role.coordinator: (
        "The recipient is a fellowship program coordinator. Ask whether they could forward the opportunity to the "
        "current hem/onc fellows. Keep it especially brief and easy to forward. Address them by first name."
    ),
    Role.fellow: (
        "The recipient is a current hem/onc fellow. Invite them directly to participate. If their research or clinical "
        "interests are provided, connect to them in one sentence. Address them as Dr. <Last name>."
    ),
}


def signature_block(product: ProductConfig) -> str:
    s = product.sender
    return f"{s.name}\n{s.title}, {s.company}\n{s.email}\n\n--\n{s.company} | {s.physical_address}\n{product.opt_out_line}"


def build_prompt(contact: Contact, inst: Institution, product: ProductConfig) -> tuple[str, str]:
    system = f"{BASE_RULES}\n\n{ROLE_INSTRUCTIONS[contact.role]}"
    facts = [
        f"Recipient name: {contact.name}",
        f"Recipient role: {contact.role.value}",
        f"Recipient title: {contact.title or 'unknown'}",
        f"Institution: {inst.name}",
    ]
    if contact.research_interests:
        facts.append(f"Recipient interests: {contact.research_interests}")
    st = product.study
    user = (
        "\n".join(facts)
        + f"\n\nProduct: {product.product_name} - {product.one_liner}\n"
        + f"Context: {product.pitch.strip()}\n"
        + f"Study: {st.format}, {st.duration_minutes} minutes, incentive: {st.incentive}\n"
        + f"Scheduling link: {st.scheduling_link}\n"
        + f"Sender: {product.sender.name}, {product.sender.title} at {product.sender.company}"
    )
    return system, user


def draft_for_contact(contact: Contact, inst: Institution, product: ProductConfig) -> EmailDraft:
    system, user = build_prompt(contact, inst, product)
    content = llm.parse(system, user, EmailContent, temperature=0.6)
    body = f"{content.body.rstrip()}\n{signature_block(product)}"
    return EmailDraft(contact_id=contact.id, subject=content.subject.strip(), body=body)


def draft(session: Session, limit: int | None = None, roles: list[Role] | None = None, min_confidence: float = 0.0) -> int:
    product = load_product_config()
    drafted_ids = set(session.exec(select(EmailDraft.contact_id)).all())
    query = select(Contact, Institution).join(Institution).where(Contact.confidence >= min_confidence)
    if roles:
        query = query.where(Contact.role.in_(roles))
    count = 0
    for contact, inst in session.exec(query).all():
        if limit and count >= limit:
            break
        if contact.id in drafted_ids or is_suppressed(session, contact.email):
            continue
        console.print(f"Drafting for {contact.name} ({contact.role.value}, {inst.name})")
        session.add(draft_for_contact(contact, inst, product))
        session.commit()
        count += 1
    return count
