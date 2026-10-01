from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from gtm_agent.config import get_settings

T = TypeVar("T", bound=BaseModel)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
def parse(system: str, user: str, schema: type[T], temperature: float = 0.2) -> T:
    client = get_client()
    completions = getattr(client.chat.completions, "parse", None) or client.beta.chat.completions.parse
    resp = completions(
        model=get_settings().openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=schema,
        temperature=temperature,
    )
    parsed = resp.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(f"Model refused or returned no parse: {resp.choices[0].message.refusal}")
    return parsed
