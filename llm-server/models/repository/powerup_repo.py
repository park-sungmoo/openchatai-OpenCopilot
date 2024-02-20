from typing import List
from shared.models.opencopilot_db.powerups import PowerUp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_
from utils.llm_consts import redis_client
from utils.get_chat_model import get_chat_model
import urllib.parse
from langchain.schema import HumanMessage, SystemMessage
from langchain.output_parsers import PydanticOutputParser
from langchain.pydantic_v1 import BaseModel, Field


class Result(BaseModel):
    urn: str = Field(
        description="url after replacing dynamic parameters with empty brackets"
    )


class PowerUpRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_powerups_bulk(
        self, powerup_data_list: List[dict]
    ) -> List[PowerUp]:
        async with self.session as session:
            powerup_objects = [PowerUp(**data) for data in powerup_data_list]
            session.add_all(powerup_objects)
            await session.commit()
            await session.refresh(powerup_objects)
            return powerup_objects

    async def create_powerup(self, powerup_data: dict) -> PowerUp:
        async with self.session as session:
            powerup = PowerUp(**powerup_data)
            session.add(powerup)
            await session.commit()
            await session.refresh(powerup)
            return powerup

    async def get_powerup_by_id(self, powerup_id: int) -> PowerUp:
        async with self.session as session:
            stmt = select(PowerUp).where(PowerUp.id == powerup_id)
            result = await session.execute(stmt)
            powerup: PowerUp = result.scalars().first()
            return powerup

    async def get_all_powerups(self, path: str):
        async with self.session as session:
            stmt = select(PowerUp)
            if path:
                stmt = stmt.where(
                    or_(PowerUp.conditional == False, PowerUp.path == path)
                )
            results = (await session.execute(stmt)).scalars().all()
            return results

    async def update_powerup(self, powerup_id: int, powerup_data: dict) -> PowerUp:
        async with self.session as session:
            stmt = select(PowerUp).where(PowerUp.id == powerup_id)
            result = await session.execute(stmt)
            powerup: PowerUp = result.scalars().first()
            if powerup:
                for key, value in powerup_data.items():
                    setattr(powerup, key, value)
                await session.commit()
                await session.refresh(powerup)
            return powerup

    async def delete_powerup(self, powerup_id: int) -> bool:
        async with self.session as session:
            stmt = select(PowerUp).where(PowerUp.id == powerup_id)
            result = await session.execute(stmt)
            powerup: PowerUp = result.scalars().first()
            if powerup:
                await session.delete(powerup)
                await session.commit()
                return True
            return False

    def parse_url_result(self, input: str) -> Result:
        parser = PydanticOutputParser(pydantic_object=Result)
        return parser.parse(input)

    async def cache_result(self, key: str, value: str, ttl: int):
        await redis_client.setex(key, ttl, value)

    # @todo convert to async as well
    async def get_cached_result(self, key: str) -> str:
        cached_result = await redis_client.get(key)
        return cached_result

    async def get_regex_for_dynamic_params(self, url: str) -> str:
        redis_key = f"url_cache:{url}"
        cached_result = await self.get_cached_result(redis_key)

        if cached_result:
            return cached_result

        chat_model = get_chat_model()
        messages = []
        url_parts = urllib.parse.urlparse(url)
        url_without_query = urllib.parse.urlunparse(
            (
                url_parts.scheme,
                url_parts.netloc,
                url_parts.path,
                url_parts.params,
                "",
                url_parts.fragment,
            )
        )

        system_message = SystemMessage(
            content="You are an ai assistant that can carefully analyze the given url"
        )

        messages.append(system_message)
        messages.append(
            HumanMessage(
                content="""Given a url, you need to replace the dynamic param with empty brackets. You will return a json in the following format
            {
                "urn": "string"
            }                         
        """
            )
        )

        messages.append(HumanMessage(content=f"Here is the url: {url_without_query}"))
        content = chat_model(messages).content
        result = self.parse_url_result(content)

        # Cache the result with a TTL of 2 hours
        await self.cache_result(redis_key, result.urn, ttl=7200)

        return result.urn
