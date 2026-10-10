#!/usr/bin/env python3
"""
Disable response_format JSON mode for Azure OpenAI profiles.

The Azure deployment rejects response_format={"type":"json_object"} with HTTP 400.
With supports_response_format=False, the parameter is omitted from the request body
and Azure returns JSON guided by the prompt ("Return only JSON...").
"""

from sqlalchemy import select

from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.db.models.llm import LLMProfile


def main():
    """Disable supports_response_format for all Azure OpenAI profiles."""
    engine = build_engine()
    session_maker = session_factory(engine)

    with session_maker() as session:
        stmt = select(LLMProfile).where(LLMProfile.base_url.like('%azure%'))
        profiles = session.execute(stmt).scalars().all()

        if not profiles:
            print("❌ No Azure OpenAI profiles found")
            return

        for profile in profiles:
            print(f"Profile: {profile.name} (ID: {profile.id})")
            print(f"  supports_response_format: {profile.supports_response_format} → False")
            profile.supports_response_format = False

        session.commit()
        print(f"\n✅ Set supports_response_format=False for {len(profiles)} Azure profile(s)")
        print("   Azure will now use prompt-guided JSON output (no response_format header).")


if __name__ == "__main__":
    main()
