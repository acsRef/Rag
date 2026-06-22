from app.config import settings
from openai import OpenAI
from typing import Generator


class MiniMaxClient:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
        )
        self.model = settings.minimax_model

    def chat_stream(self, messages: list[dict], temperature: float = 0.7, top_p: float = 0.9) -> Generator[str, None, None]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=temperature,
            top_p=top_p,
            max_tokens=4096,
        )
        for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def chat(self, messages: list[dict]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=False,
            temperature=0.7,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""


minimax_client = MiniMaxClient()
