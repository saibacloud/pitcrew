# Provider-neutral AI layer: routes call these, and the configured adapter (Gemini or OpenAI)
# is resolved per call, so a key change in Settings applies without a restart

import logging

from fastapi import HTTPException

from backend.services.ai.base import (  # noqa: F401
    Answer, EmptyResponse, Image, Message, NoAIKey, PdfBytes, PdfFile, PdfLimits, describe_error,
)
from backend.services.ai.images import compress_for_storage, make_thumbnail, pin_images  # noqa: F401
from backend.services.ai.prompts import (  # noqa: F401
    ANGLE_LABELS, CHAT_SYSTEM_INSTRUCTION, DOCS_SYSTEM_INSTRUCTION, INVENTORY_SCHEMA,
    INVENTORY_SYSTEM_INSTRUCTION, PARTS_SYSTEM_INSTRUCTION, build_docsearch_prompt,
    build_pin_research_prompt, build_research_prompt, car_contract, car_description, vehicle_profile,
)
from backend.services.ai.ranking import rank_passages, text_chunks  # noqa: F401
from backend.services.ai.settings import resolve

log = logging.getLogger(__name__)


async def chat(contract: str, history: list[dict], message: str) -> Answer:
    """One web-searched chat answer; history is [{role, body}] oldest first."""
    messages = [Message(m['role'], [m['body']]) for m in history] + [Message('user', [message])]
    return await (await resolve()).generate(
        f'{CHAT_SYSTEM_INSTRUCTION}\n\n{contract}', messages, search=True, label='chat')


async def extract_inventory(previous_answer: str | None, message: str, inventory: list[dict]) -> list[dict]:
    """Raw inventory changes proposed for one owner message; the caller validates them."""
    held = '\n'.join(f"- {i['name']}" + (f" ({i['detail']})" if i.get('detail') else '') for i in inventory)
    prompt = (
        f"Current inventory:\n{held or '- none'}\n\n"
        f"Assistant's previous message:\n{previous_answer or '(none)'}\n\n"
        f"Owner's latest message:\n{message}"
    )
    result = await (await resolve()).extract_json(
        INVENTORY_SYSTEM_INSTRUCTION, prompt, INVENTORY_SCHEMA, 'inventory extraction')
    return result.get('changes') or []


async def research(prompt: str) -> Answer:
    """Text-only parts research with web search."""
    return await (await resolve()).generate(
        PARTS_SYSTEM_INSTRUCTION, [Message('user', [prompt])], search=True, label='research')


async def research_with_images(prompt_text: str, images: list[bytes]) -> Answer:
    """Photo parts research with web search. Images are JPEG."""
    parts = [Image(img) for img in images] + [prompt_text]
    return await (await resolve()).generate(
        PARTS_SYSTEM_INSTRUCTION, [Message('user', parts)], search=True, label='image research')


async def ask_documents(content_parts: list, car: dict, question: str) -> str:
    """Answer from the given document parts only; the question goes after the documents."""
    parts = content_parts + [build_docsearch_prompt(car, question)]
    answer = await (await resolve()).generate(
        DOCS_SYSTEM_INSTRUCTION, [Message('user', parts)], search=False, label='doc search')
    return answer.text


async def pdf_limits() -> PdfLimits:
    return (await resolve()).pdf_limits


async def require_ai() -> None:
    """FastAPI dependency - 503 before any AI work when no key is configured."""
    try:
        await resolve()
    except NoAIKey:
        raise HTTPException(503, 'No AI API key set')
