"""ElevenLabs client helper for VoiceBench.

Loads environment variables from .env and provides an initialized ElevenLabs client.
Credentials are read automatically from the ELEVENLABS_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Optional
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

# Load environment variables from .env
load_dotenv(".env")


def get_elevenlabs_client(api_key: Optional[str] = None) -> ElevenLabs:
    """Return an ElevenLabs client instance.

    Reads ELEVENLABS_API_KEY from environment if api_key is omitted.
    """
    key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    return ElevenLabs(api_key=key) if key else ElevenLabs()


# Client instance initialized from environment
client = get_elevenlabs_client()
