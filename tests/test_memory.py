"""Phase 4: rolling conversation summaries for long chats."""

import asyncio
import uuid

import pytest
from google.genai import types

from receptionist.agent import memory
from receptionist.agent.client import ReceptionistAgent
from receptionist.config import get_settings
from receptionist.skills.loader import SkillRegistry
from tests.conftest import ROOT, FakeGemini, response, text_block


@pytest.fixture
def agent():
    settings = get_settings().model_copy(update={"summarize_after_turns": 2})
    skills = SkillRegistry(ROOT / "skills")
    skills.reload()
    return ReceptionistAgent(settings, skills, client=FakeGemini())


async def _say(agent, db, sid, text, *scripted):
    """Send one message; `scripted` are the fake model's outputs, in call order."""
    agent.client.responses = [response(text_block(t)) for t in scripted]
    result = await agent.reply(db, sid, text)
    await asyncio.gather(*agent._background)  # let background summaries finish
    return result


SUMMARY = "Parent asking about grade 7; told tuition is 2,900 USD."


def test_long_chat_is_summarised_and_carried_forward(agent, db):
    sid = str(uuid.uuid4())

    async def chat():
        await _say(agent, db, sid, "Hi, I'm a parent. Tuition for grade 7?", "2,900 USD per year.")
        # 2nd message reaches summarize_after_turns=2: reply first, then the summary call.
        await _say(agent, db, sid, "And the registration fee?", "150 USD.", SUMMARY)
        state = agent.sessions.get(sid)
        assert state.summary == SUMMARY
        assert state.contents == [] and state.turns == 0
        assert state.last_skill == "fees-and-payments"  # follow-ups keep their topic

        await _say(agent, db, sid, "Can I pay monthly?", "Per term or per year.")
        first_part = agent.client.calls[-1]["contents"][-1].parts[0].text
        assert first_part.startswith("<conversation_summary>") and SUMMARY in first_part

    asyncio.run(chat())


def test_summary_is_discarded_if_a_new_message_arrived(agent, db):
    sid = str(uuid.uuid4())

    async def run():
        await _say(agent, db, sid, "Hello", "Hi!")
        state = agent.sessions.get(sid)
        agent.client.responses = [response(text_block("stale summary"))]
        await agent._summarize(sid, turns_seen=state.turns + 5)  # turns no longer match
        assert agent.sessions.get(sid).summary is None

    asyncio.run(run())


def test_transcript_skips_internal_blocks_and_masks_personal_data():
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text='<school_reference skill="fees">secret</school_reference>'
                ),
                types.Part.from_text(text="Call me on 012 345 678, email a@b.com"),
            ],
        ),
        types.Content(role="model", parts=[types.Part.from_text(text="Sure.")]),
    ]
    text = memory.transcript(contents, previous_summary="Earlier: asked about fees.")
    assert "secret" not in text and "012 345 678" not in text and "a@b.com" not in text
    assert "Caller: Call me on [phone]" in text and "Receptionist: Sure." in text
    assert text.startswith("(Earlier summary) Earlier: asked about fees.")
